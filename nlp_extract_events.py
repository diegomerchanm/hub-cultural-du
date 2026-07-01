"""
Fase 4-B — Extracción de eventos culturales desde Post.caption.

Pipeline por post:
  1. Zero-shot classification (cross-encoder/nli-MiniLM2-L6-H768) → tipo de evento
  2. NER spaCy → DATE, LOC/GPE, ORG
  3. Score compuesto = classifier_score * 0.6 + hotness_normalizado * 0.4
  4. Resolución inline → busca evento existente similar antes de crear
  5. MERGE (:Event) + relaciones [:MENTIONS_EVENT], [:ORGANIZED], [:PARTICIPATED_IN],
     [:SUPPORTED], [:LOCATED_AT], [:HAS_HASHTAG]

Idempotente: marca cada post procesado con eventExtracted=true.
Depende de nlp_enrich_nodes.py para captionLanguage (opcional pero recomendado).
"""

import hashlib
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase
from scipy.spatial.distance import cosine as cosine_dist
from tqdm import tqdm

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# ── 2. Taxonomía de etiquetas ─────────────────────────────────────────────────
# Diseñada para la diáspora colombiana en París: corpus trilingüe ES/FR/EN
# con contexto afrocolombian, andino, costeño e institucional-consular.
#
# Estructura: (label, categoria, penalización_política)
_LABEL_META: list[tuple[str, str, float]] = [
    # ── Música ────────────────────────────────────────────────────────────────
    ("concierto, recital o presentación musical en vivo",        "musical",       1.0),
    # ── Artes visuales ────────────────────────────────────────────────────────
    ("exposición, muestra o vernissage de arte visual",          "visual",        1.0),
    ("instalación artística o intervención urbana",              "visual",        1.0),
    # ── Artes escénicas ───────────────────────────────────────────────────────
    ("obra de teatro, danza o performance escénica",             "escenico",      1.0),
    # ── Audiovisual ───────────────────────────────────────────────────────────
    ("proyección cinematográfica, documental o audiovisual",     "audiovisual",   1.0),
    # ── Formación ─────────────────────────────────────────────────────────────
    ("taller, clase o formación creativa y artística",           "formacion",     1.0),
    ("residencia artística o convocatoria cultural",             "formacion",     1.0),
    # ── Festival / celebración ────────────────────────────────────────────────
    ("festival, feria o celebración de la cultura colombiana",   "festival",      1.0),
    # ── Comunidad / diáspora ──────────────────────────────────────────────────
    ("encuentro comunitario o evento de la diáspora colombiana", "comunitario",   1.0),
    # ── Institucional / consular ──────────────────────────────────────────────
    ("evento del consulado, embajada o institución colombiana",  "institucional", 1.0),
    # ── Académico / conferencias ──────────────────────────────────────────────
    ("conferencia, charla académica o panel cultural",           "academico",     1.0),
    # ── Gastronomía ───────────────────────────────────────────────────────────
    ("evento gastronómico o muestra culinaria colombiana",       "gastronomico",  1.0),
    # ── Político (penalizado, no eliminado) ───────────────────────────────────
    ("acto político, electoral o gubernamental",                 "politico",      0.2),
    # ── Clases nulas (no evento) ──────────────────────────────────────────────
    ("publicación informativa, noticia o comunicado",            "nulo",          0.0),
    ("promoción comercial, oferta o publicidad",                 "nulo",          0.0),
    ("contenido personal, cotidiano o sin relación cultural",    "nulo",          0.0),
]

EVENT_LABELS  = [lbl for lbl, _, _ in _LABEL_META]
_CAT_MAP      = {lbl: cat  for lbl, cat, _  in _LABEL_META}
_PEN_MAP      = {lbl: pen  for lbl, _, pen  in _LABEL_META}
NULL_CATS     = {"nulo"}          # categorías que descartan el post
HOTNESS_MAX   = 6.0               # cap para normalizar hotness a [0, 1]
MIN_CAPTION_LEN = 40

ZS_MODEL     = "cross-encoder/nli-MiniLM2-L6-H768"
ST_MODEL     = "paraphrase-multilingual-MiniLM-L12-v2"
LANG_TO_MODEL = {"es": "es_core_news_lg", "en": "en_core_web_sm", "fr": "fr_core_news_lg"}
_NLP: dict   = {}

# ── 3. Helpers — NLP ──────────────────────────────────────────────────────────
def get_nlp(lang: str):
    if lang not in _NLP:
        model_name = LANG_TO_MODEL.get(lang)
        if not model_name:
            return None
        import spacy
        print(f"  📦 Cargando spaCy: {model_name}")
        _NLP[lang] = spacy.load(model_name, disable=["senter"])
    return _NLP[lang]


def detect_text_lang(text: str) -> str:
    try:
        from langdetect import detect
        lang = detect(text)
        return lang if lang in LANG_TO_MODEL else "es"
    except Exception:
        return "es"


def extract_ner(text: str, lang: str) -> dict:
    nlp = get_nlp(lang)
    result = {"dates": [], "locations": [], "orgs": []}
    if not nlp:
        return result
    doc = nlp(text[:3000])
    for ent in doc.ents:
        txt = ent.text.strip()
        if not txt:
            continue
        if ent.label_ == "DATE":
            result["dates"].append(txt)
        elif ent.label_ in ("LOC", "GPE", "FAC"):
            result["locations"].append(txt)
        elif ent.label_ == "ORG":
            result["orgs"].append(txt)
    for key in result:
        seen: set = set()
        result[key] = [x for x in result[key] if not (x.lower() in seen or seen.add(x.lower()))]
    return result


# ── 4. Helpers — fechas ───────────────────────────────────────────────────────
def parse_date_safe(raw: str) -> Optional[str]:
    if not raw or len(raw.strip()) < 3:
        return None
    try:
        from dateutil import parser as dp
        dt = dp.parse(raw, fuzzy=True, dayfirst=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def dates_close(d1: Optional[str], d2: Optional[str], window: int = 3) -> bool:
    if not d1 or not d2:
        return True
    try:
        dt1 = datetime.fromisoformat(d1)
        dt2 = datetime.fromisoformat(d2)
        return abs((dt1 - dt2).days) <= window
    except Exception:
        return True


# ── 5. Scores ─────────────────────────────────────────────────────────────────
def compute_hotness(likes: int, comments: int, timestamp_str: str) -> float:
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        days_ago = max(0, (datetime.now(timezone.utc) - ts).days)
    except Exception:
        days_ago = 180
    recency = max(0.0, 1.0 - days_ago / 730.0)
    return round(
        math.log1p(likes)    * 0.4 +
        math.log1p(comments) * 0.3 +
        recency * 2.0        * 0.3,
        4,
    )


def compute_event_score(classifier_score: float, hotness: float, political_penalty: float) -> float:
    """
    Score compuesto final del evento:
      eventScore = (classifier_score * 0.6 + hotness_norm * 0.4) * political_penalty

    political_penalty = 1.0 para eventos culturales, 0.2 para políticos.
    """
    hotness_norm = min(hotness / HOTNESS_MAX, 1.0)
    raw = classifier_score * 0.6 + hotness_norm * 0.4
    return round(raw * political_penalty, 4)


# ── 6. ID estable para eventos ────────────────────────────────────────────────
def make_event_id(event_type: str, raw_date: str, loc_name: str) -> str:
    key = f"{event_type}|{(raw_date or '').strip()}|{(loc_name or '').lower().strip()}"
    return "evt_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


# ── 7. Resolver inline ────────────────────────────────────────────────────────
def find_similar_event(candidate: dict, cache: list, threshold: float, date_window: int) -> Optional[str]:
    cand_emb = candidate.get("embedding")
    if not cand_emb:
        return None
    cand_loc  = (candidate.get("locationName") or "").lower().strip()
    cand_date = candidate.get("eventDate")

    for existing in cache:
        exist_emb = existing.get("embedding")
        if not exist_emb:
            continue
        exist_loc = (existing.get("locationName") or "").lower().strip()
        if cand_loc and exist_loc and cand_loc != exist_loc:
            continue
        if not dates_close(cand_date, existing.get("eventDate"), date_window):
            continue
        sim = 1.0 - cosine_dist(cand_emb, exist_emb)
        if sim >= threshold:
            return existing["id"]
    return None


# ── 8. Neo4j — cache ──────────────────────────────────────────────────────────
def load_events_cache(session) -> list:
    rows = session.run("""
        MATCH (e:Event)
        RETURN e.id           AS id,
               e.eventDate    AS eventDate,
               e.locationName AS locationName,
               e.embedding    AS embedding
    """).data()
    print(f"  🗂️  {len(rows)} eventos existentes cargados en cache")
    return rows


# ── 9. Neo4j — crear o enriquecer evento ─────────────────────────────────────
def upsert_event(session, event: dict, post: dict, existing_id: Optional[str]):
    target_id = existing_id or event["id"]

    if existing_id:
        session.run("""
            MATCH (e:Event {id: $id})
            SET e.eventScore  = CASE WHEN $score > e.eventScore  THEN $score  ELSE e.eventScore  END,
                e.hotnessScore = CASE WHEN $hotness > e.hotnessScore THEN $hotness ELSE e.hotnessScore END,
                e.postCount    = coalesce(e.postCount, 0) + 1
        """, id=existing_id, score=event["eventScore"], hotness=event["hotnessScore"])
    else:
        session.run("""
            MERGE (e:Event {id: $id})
            SET e.title         = $title,
                e.type          = $type,
                e.category      = $category,
                e.rawDate       = $rawDate,
                e.eventDate     = $eventDate,
                e.locationName  = $locationName,
                e.hotnessScore  = $hotnessScore,
                e.eventScore    = $eventScore,
                e.confidence    = $confidence,
                e.postCount     = 1,
                e.embedding     = $embedding,
                e.createdAt     = $createdAt
        """, **{k: event[k] for k in [
            "id", "title", "type", "category", "rawDate", "eventDate",
            "locationName", "hotnessScore", "eventScore", "confidence",
            "embedding", "createdAt",
        ]})

        if event.get("locationName"):
            session.run("""
                MATCH (e:Event {id: $eid})
                MERGE (l:Location {name: $loc})
                MERGE (e)-[:LOCATED_AT]->(l)
            """, eid=target_id, loc=event["locationName"])

    # [:MENTIONS_EVENT] Post → Event
    session.run("""
        MATCH (p:Post {id: $pid})
        MATCH (e:Event {id: $eid})
        MERGE (p)-[:MENTIONS_EVENT]->(e)
    """, pid=post["id"], eid=target_id)

    # Hashtags
    for tag in (post.get("hashtags") or []):
        if tag:
            session.run("""
                MATCH (e:Event {id: $eid})
                MERGE (h:Hashtag {name: $tag})
                MERGE (e)-[:HAS_HASHTAG]->(h)
            """, eid=target_id, tag=tag.lower())

    # Autor → [:PARTICIPATED_IN]
    if post.get("author"):
        session.run("""
            MATCH (a:Account {username: $username})
            MATCH (e:Event {id: $eid})
            MERGE (a)-[:PARTICIPATED_IN]->(e)
        """, username=post["author"], eid=target_id)

    # Primer ORG → [:ORGANIZED]
    if event.get("organizerOrg"):
        result = session.run("""
            MATCH (a:Account)
            WHERE toLower(a.fullName) CONTAINS toLower($org)
               OR toLower(a.username) CONTAINS toLower($org)
            RETURN a.username AS username LIMIT 1
        """, org=event["organizerOrg"]).single()
        if result:
            session.run("""
                MATCH (a:Account {username: $username})
                MATCH (e:Event {id: $eid})
                MERGE (a)-[:ORGANIZED]->(e)
            """, username=result["username"], eid=target_id)

    # Tagged → [:PARTICIPATED_IN]
    for tu in (post.get("taggedUsers") or []):
        session.run("""
            MATCH (a:Account {username: $username})
            MATCH (e:Event {id: $eid})
            MERGE (a)-[:PARTICIPATED_IN]->(e)
        """, username=tu, eid=target_id)

    # Mentions → [:SUPPORTED]
    for mention in (post.get("mentions") or []):
        session.run("""
            MATCH (a:Account {username: $username})
            MATCH (e:Event {id: $eid})
            MERGE (a)-[:SUPPORTED]->(e)
        """, username=mention, eid=target_id)


# ── 10. Pipeline principal ────────────────────────────────────────────────────
def run_extraction(
    threshold: float     = 0.45,
    max_posts: int       = 50,
    batch_size: int      = 20,
    date_window: int     = 3,
    sim_threshold: float = 0.82,
    dry_run: bool        = False,
):
    print("\n🎭 Fase 4-B — Extracción de Eventos")
    print("=" * 60)
    print(f"  threshold={threshold}  max_posts={max_posts or '∞'}  "
          f"batch={batch_size}  sim≥{sim_threshold}  date±{date_window}d")

    limit_clause = f"LIMIT {max_posts}" if max_posts > 0 else ""
    with driver.session() as session:
        posts = session.run(f"""
            MATCH (a:Account)-[:PUBLISHED]->(p:Post)
            WHERE p.caption IS NOT NULL
              AND size(p.caption) >= {MIN_CAPTION_LEN}
              AND (p.eventExtracted IS NULL OR p.eventExtracted = false)
            RETURN p.id             AS id,
                   p.caption        AS caption,
                   p.likesCount     AS likes,
                   p.commentsCount  AS comments,
                   p.timestamp      AS timestamp,
                   p.hashtags       AS hashtags,
                   a.username       AS author,
                   collect(DISTINCT [(p)-[:TAGS_USER]->(tu) | tu.username])[0] AS taggedUsers,
                   collect(DISTINCT [(p)-[:MENTIONS]->(m)   | m.username])[0]  AS mentions
            {limit_clause}
        """).data()

    if not posts:
        print("  ✅ No hay posts pendientes.")
        return

    print(f"\n  🔍 {len(posts)} posts a clasificar")

    print(f"\n  📦 Cargando ZS classifier: {ZS_MODEL}")
    from transformers import pipeline as hf_pipeline
    classifier = hf_pipeline("zero-shot-classification", model=ZS_MODEL, device=-1)

    print(f"  📦 Cargando sentence-transformers: {ST_MODEL}")
    from sentence_transformers import SentenceTransformer
    st_model = SentenceTransformer(ST_MODEL)

    with driver.session() as session:
        cache = load_events_cache(session)

    created       = 0
    enriched      = 0
    skipped       = 0
    processed_ids: list = []
    dry_counts:   dict  = defaultdict(int)
    diag_records: list  = []          # siempre acumulado en dry-run

    for i in tqdm(range(0, len(posts), batch_size), desc="  posts"):
        batch      = posts[i: i + batch_size]
        captions   = [p["caption"][:512] for p in batch]
        zs_results = classifier(captions, EVENT_LABELS, multi_label=False)
        if isinstance(zs_results, dict):
            zs_results = [zs_results]

        for post, zs in zip(batch, zs_results):
            top_label = zs["labels"][0]
            top_score = zs["scores"][0]
            category  = _CAT_MAP.get(top_label, "nulo")
            penalty   = _PEN_MAP.get(top_label, 0.0)
            is_event  = category not in NULL_CATS and top_score >= threshold

            hotness = compute_hotness(
                post.get("likes", 0) or 0,
                post.get("comments", 0) or 0,
                post.get("timestamp", "") or "",
            )
            event_score = compute_event_score(top_score, hotness, penalty) if is_event else 0.0

            # NER siempre en dry-run para mostrar location en diagnóstico
            lang      = detect_text_lang(post["caption"])
            ner       = extract_ner(post["caption"], lang)
            raw_date  = ner["dates"][0]      if ner["dates"]     else None
            loc_name  = ner["locations"][0]  if ner["locations"] else None
            org_name  = ner["orgs"][0]       if ner["orgs"]      else None
            event_date = parse_date_safe(raw_date)

            if dry_run:
                diag_records.append({
                    "caption":     post["caption"],
                    "author":      post.get("author", ""),
                    "top3":        list(zip(zs["labels"][:3], zs["scores"][:3])),
                    "decision":    "EVENTO" if is_event else "no evento",
                    "category":    category,
                    "top_score":   top_score,
                    "hotness":     hotness,
                    "event_score": event_score,
                    "loc_name":    loc_name or "",
                    "raw_date":    raw_date or "",
                })
                if is_event:
                    dry_counts[category] += 1

            if not is_event:
                skipped += 1
                processed_ids.append(post["id"])
                continue

            if dry_run:
                processed_ids.append(post["id"])
                continue

            emb_text  = f"{post['caption'][:200]} {top_label} {raw_date or ''} {loc_name or ''}"
            embedding = st_model.encode([emb_text], show_progress_bar=False)[0].tolist()
            event_id  = make_event_id(top_label, raw_date or "", loc_name or "")

            candidate = {
                "id":           event_id,
                "title":        top_label.title(),
                "type":         top_label,
                "category":     category,
                "rawDate":      raw_date or "",
                "eventDate":    event_date or "",
                "locationName": loc_name or "",
                "hotnessScore": hotness,
                "eventScore":   event_score,
                "confidence":   round(top_score, 4),
                "embedding":    embedding,
                "organizerOrg": org_name,
                "createdAt":    datetime.now(timezone.utc).isoformat(),
            }

            existing_id = find_similar_event(candidate, cache, sim_threshold, date_window)

            with driver.session() as session:
                upsert_event(session, candidate, post, existing_id)

            if existing_id:
                enriched += 1
                for e in cache:
                    if e["id"] == existing_id:
                        e["hotnessScore"] = max(e.get("hotnessScore", 0), hotness)
                        break
            else:
                created += 1
                cache.append({
                    "id":           event_id,
                    "eventDate":    event_date or "",
                    "locationName": loc_name or "",
                    "embedding":    embedding,
                })
            processed_ids.append(post["id"])

        if not dry_run and processed_ids:
            with driver.session() as session:
                session.run("""
                    UNWIND $ids AS pid
                    MATCH (p:Post {id: pid})
                    SET p.eventExtracted = true
                """, ids=processed_ids[-len(batch):])

    if not dry_run and processed_ids:
        with driver.session() as session:
            session.run("""
                UNWIND $ids AS pid
                MATCH (p:Post {id: pid})
                SET p.eventExtracted = true
            """, ids=processed_ids)

    # ── Resumen ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  ✅ Posts procesados : {len(posts)}")
    if dry_run:
        total_ev = sum(dry_counts.values())
        print(f"  🎭 Eventos detectados: {total_ev}")
        print(f"  ⏭️  Sin evento        : {skipped}")
        if dry_counts:
            print("\n  Por categoría:")
            for cat, n in sorted(dry_counts.items(), key=lambda x: -x[1]):
                print(f"    {n:>4}  {cat}")
    else:
        print(f"  🆕 Eventos creados     : {created}")
        print(f"  🔄 Eventos enriquecidos: {enriched}")
        print(f"  ⏭️  Sin evento          : {skipped}")

    # ── Diagnóstico (solo dry-run) ────────────────────────────────────────────
    if dry_run and diag_records:
        detected   = [r for r in diag_records if r["decision"] == "EVENTO"]
        rejected   = [r for r in diag_records if r["decision"] == "no evento"]
        false_neg  = [r for r in rejected
                      if _CAT_MAP.get(r["top3"][0][0], "nulo") not in NULL_CATS]
        all_cls    = [r["top_score"]   for r in diag_records]
        all_hot    = [r["hotness"]     for r in diag_records]
        all_ev     = [r["event_score"] for r in detected]

        # ── 1. Todos los eventos detectados ───────────────────────────────────
        print(f"\n{'═'*60}")
        print(f"  🎭 EVENTOS DETECTADOS ({len(detected)})")
        print(f"{'═'*60}")
        for i, r in enumerate(sorted(detected, key=lambda x: -x["event_score"]), 1):
            print(f"\n  [{i:02d}] @{r['author']}  cat={r['category']}")
            print(f"       score={r['event_score']:.3f}  cls={r['top_score']:.3f}  "
                  f"hot={r['hotness']:.2f}  loc={r['loc_name'] or '-'}  "
                  f"date={r['raw_date'] or '-'}")
            print(f"       Label  : {r['top3'][0][0]}")
            print(f"       Caption: {r['caption'].replace(chr(10), ' ')}")

        # ── 2. Distribuciones de scores ───────────────────────────────────────
        print(f"\n{'─'*60}")
        print("  📊 DIAGNÓSTICO — Distribución de scores")
        print(f"{'─'*60}")
        print(f"  Classifier score (todos los posts):")
        print(f"    min={min(all_cls):.3f}  max={max(all_cls):.3f}  avg={sum(all_cls)/len(all_cls):.3f}")
        print(f"  Hotness raw:")
        print(f"    min={min(all_hot):.3f}  max={max(all_hot):.3f}  avg={sum(all_hot)/len(all_hot):.3f}")
        if all_ev:
            print(f"  Event score compuesto (solo aceptados):")
            print(f"    min={min(all_ev):.3f}  max={max(all_ev):.3f}  avg={sum(all_ev)/len(all_ev):.3f}")
        print(f"  Rechazados por score<{threshold} con label no-nulo: {len(false_neg)}")

        # ── 3. Top-5 rechazados con mayor score — posibles falsos negativos ───
        top5_fn = sorted(false_neg, key=lambda x: -x["top_score"])[:5]
        if top5_fn:
            print(f"\n{'─'*60}")
            print(f"  🔍 TOP-5 POSIBLES FALSOS NEGATIVOS (score<{threshold}, label no nulo)")
            print(f"{'─'*60}")
            for i, r in enumerate(top5_fn, 1):
                print(f"\n  [{i}] @{r['author']}  cls={r['top_score']:.3f}  "
                      f"hot={r['hotness']:.2f}  cat={r['category']}")
                print(f"      Label  : {r['top3'][0][0]}")
                print(f"      Caption: {r['caption'].replace(chr(10), ' ')}")
                print(f"      Top-3  :")
                for label, score in r["top3"]:
                    marker = "◀" if label == r["top3"][0][0] else " "
                    print(f"               {score:.3f}  {label} {marker}")

        # ── 4. 10 ejemplos aleatorios (todos, eventos y no-eventos) ───────────
        print(f"\n{'─'*60}")
        print("  🎲 10 EJEMPLOS ALEATORIOS (mezcla de eventos y no-eventos)")
        print(f"{'─'*60}")
        sample = random.sample(diag_records, min(10, len(diag_records)))
        for i, r in enumerate(sample, 1):
            preview = r["caption"][:100].replace("\n", " ")
            print(f"\n  [{i:02d}] {r['decision'].upper()}  cat={r['category']}  "
                  f"eventScore={r['event_score']:.3f}  hot={r['hotness']:.2f}  "
                  f"@{r['author']}")
            print(f"       Caption : {preview!r}")
            print(f"       Top-3   :")
            for label, score in r["top3"]:
                marker = "◀" if label == r["top3"][0][0] else " "
                print(f"                {score:.3f}  {label} {marker}")
        print(f"{'─'*60}")


# ── 11. CLI ───────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    threshold: float = typer.Option(
        0.45, "--threshold",
        help="Confianza mínima del clasificador ZS para considerar evento.",
    ),
    sim_threshold: float = typer.Option(
        0.82, "--sim-threshold",
        help="Similitud coseno mínima para deduplicar eventos.",
    ),
    date_window: int = typer.Option(
        3, "--date-window",
        help="Ventana de días para deduplicación por fecha.",
    ),
    max_posts: int = typer.Option(
        50, "--max-posts",
        help="Límite de posts a procesar. 0 = todos. Default 50 para testing rápido.",
    ),
    batch_size: int = typer.Option(
        20, "--batch-size",
        help="Posts por lote en ZS classification.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Clasificar y mostrar diagnóstico sin escribir en Neo4j.",
    ),
):
    """
    Fase 4-B: detecta eventos culturales en Post.caption y crea nodos (:Event).

    Score final = classifier_score × 0.6 + hotness_norm × 0.4 × penalización_política.
    Recomendado: ejecutar nlp_enrich_nodes.py primero.
    Para deduplicar eventos existentes: nlp_event_resolver.py
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    run_extraction(
        threshold=threshold,
        max_posts=max_posts,
        batch_size=batch_size,
        date_window=date_window,
        sim_threshold=sim_threshold,
        dry_run=dry_run,
    )

    driver.close()
    print("\n✅ Extracción de eventos completa.")


if __name__ == "__main__":
    app()
