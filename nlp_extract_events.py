"""
Fase 4-B — Extracción de eventos culturales desde Post.caption.

Pipeline por post:
  1. Zero-shot classification (cross-encoder/nli-MiniLM2-L6-H768) → tipo de evento
  2. NER spaCy → DATE, LOC/GPE, ORG
  3. Score de hotness (likes, comments, recency)
  4. Resolución inline → busca evento existente similar antes de crear (ver nlp_event_resolver.py)
  5. MERGE (:Event) + relaciones [:MENTIONS_EVENT], [:ORGANIZED], [:PARTICIPATED_IN],
     [:SUPPORTED], [:LOCATED_AT], [:HAS_HASHTAG]

Idempotente: marca cada post procesado con eventExtracted=true.
Depende de nlp_enrich_nodes.py para captionLanguage (opcional pero recomendado).
"""

import hashlib
import math
import os
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

# ── 2. Constantes ─────────────────────────────────────────────────────────────
ZS_MODEL = "cross-encoder/nli-MiniLM2-L6-H768"
ST_MODEL  = "paraphrase-multilingual-MiniLM-L12-v2"

# Etiquetas para zero-shot (en español para mejor recall sobre corpus hispano)
EVENT_LABELS = [
    "concierto o presentación musical",
    "exposición o muestra de arte",
    "festival cultural",
    "performance o instalación artística",
    "taller o clase creativa",
    "residencia artística",
    "conferencia o charla cultural",
    "vernissage o inauguración de exposición",
    "proyección de cine o audiovisual",
    "obra de teatro o danza",
    "convocatoria o llamado a participación",
    "no es un evento cultural",
]
NON_EVENT_LABEL  = "no es un evento cultural"
MIN_CAPTION_LEN  = 40   # caracteres mínimos para clasificar
LANG_TO_MODEL    = {"es": "es_core_news_lg", "en": "en_core_web_sm", "fr": "fr_core_news_lg"}
_NLP: dict       = {}

# ── 3. Helpers — NLP ──────────────────────────────────────────────────────────
def get_nlp(lang: str):
    if lang not in _NLP:
        model_name = LANG_TO_MODEL.get(lang)
        if not model_name:
            return None
        import spacy
        print(f"  📦 Cargando spaCy: {model_name}")
        _NLP[lang] = spacy.load(model_name, disable=["parser"])
    return _NLP[lang]


def detect_text_lang(text: str) -> str:
    try:
        from langdetect import detect, LangDetectException
        lang = detect(text)
        return lang if lang in LANG_TO_MODEL else "es"  # fallback español
    except Exception:
        return "es"


def extract_ner(text: str, lang: str) -> dict:
    """Devuelve {dates, locations, orgs} como listas de strings."""
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
    # Deduplicar preservando orden
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
    """True si ambas fechas están dentro de `window` días, o si alguna es None."""
    if not d1 or not d2:
        return True
    try:
        from datetime import datetime
        dt1 = datetime.fromisoformat(d1)
        dt2 = datetime.fromisoformat(d2)
        return abs((dt1 - dt2).days) <= window
    except Exception:
        return True


# ── 5. Hotness score ──────────────────────────────────────────────────────────
def compute_hotness(likes: int, comments: int, timestamp_str: str) -> float:
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        days_ago = max(0, (datetime.now(timezone.utc) - ts).days)
    except Exception:
        days_ago = 180
    recency = max(0.0, 1.0 - days_ago / 730.0)  # decay en 2 años
    return round(
        math.log1p(likes)    * 0.4 +
        math.log1p(comments) * 0.3 +
        recency * 2.0        * 0.3,  # escala 0-2 para equiparar con log
        4,
    )


# ── 6. ID estable para eventos ────────────────────────────────────────────────
def make_event_id(event_type: str, raw_date: str, loc_name: str) -> str:
    key = f"{event_type}|{(raw_date or '').strip()}|{(loc_name or '').lower().strip()}"
    return "evt_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


# ── 7. Resolver inline — deduplicación antes de crear ─────────────────────────
def find_similar_event(
    candidate: dict,
    cache: list,
    threshold: float = 0.82,
    date_window: int = 3,
) -> Optional[str]:
    """
    Busca en `cache` un evento similar por:
      - misma locationName (si ambos la tienen)
      - fecha dentro de `date_window` días (si ambos tienen fecha)
      - similitud coseno de embeddings > threshold

    Retorna el id del evento existente o None.
    """
    cand_emb = candidate.get("embedding")
    if not cand_emb:
        return None

    cand_loc  = (candidate.get("locationName") or "").lower().strip()
    cand_date = candidate.get("eventDate")

    for existing in cache:
        exist_emb = existing.get("embedding")
        if not exist_emb:
            continue

        # Filtro por location (si ambos la tienen, deben coincidir)
        exist_loc = (existing.get("locationName") or "").lower().strip()
        if cand_loc and exist_loc and cand_loc != exist_loc:
            continue

        # Filtro por fecha
        if not dates_close(cand_date, existing.get("eventDate"), date_window):
            continue

        sim = 1.0 - cosine_dist(cand_emb, exist_emb)
        if sim >= threshold:
            return existing["id"]

    return None


# ── 8. Neo4j — cargar cache de eventos existentes ────────────────────────────
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
    """
    Si existing_id → enriquece el evento existente (hotness, postCount).
    Si no → MERGE nuevo :Event con todas sus relaciones.
    """
    target_id = existing_id or event["id"]

    if existing_id:
        # Enriquecer score y contador
        session.run("""
            MATCH (e:Event {id: $id})
            SET e.hotnessScore = CASE
                    WHEN $hotness > e.hotnessScore THEN $hotness
                    ELSE e.hotnessScore END,
                e.postCount = coalesce(e.postCount, 0) + 1
        """, id=existing_id, hotness=event["hotnessScore"])
    else:
        # Crear nuevo Event
        session.run("""
            MERGE (e:Event {id: $id})
            SET e.title        = $title,
                e.type         = $type,
                e.rawDate      = $rawDate,
                e.eventDate    = $eventDate,
                e.locationName = $locationName,
                e.hotnessScore = $hotnessScore,
                e.confidence   = $confidence,
                e.postCount    = 1,
                e.embedding    = $embedding,
                e.createdAt    = $createdAt
        """, **{k: event[k] for k in [
            "id", "title", "type", "rawDate", "eventDate",
            "locationName", "hotnessScore", "confidence", "embedding", "createdAt",
        ]})

        # [:LOCATED_AT] → Location
        if event.get("locationName"):
            session.run("""
                MATCH (e:Event {id: $eid})
                MERGE (l:Location {name: $loc})
                MERGE (e)-[:LOCATED_AT]->(l)
            """, eid=target_id, loc=event["locationName"])

    # [:MENTIONS_EVENT] Post → Event (siempre)
    session.run("""
        MATCH (p:Post {id: $pid})
        MATCH (e:Event {id: $eid})
        MERGE (p)-[:MENTIONS_EVENT]->(e)
    """, pid=post["id"], eid=target_id)

    # Hashtags del post → Event
    if post.get("hashtags"):
        for tag in post["hashtags"]:
            if tag:
                session.run("""
                    MATCH (e:Event {id: $eid})
                    MERGE (h:Hashtag {name: $tag})
                    MERGE (e)-[:HAS_HASHTAG]->(h)
                """, eid=target_id, tag=tag.lower())

    # Account (autor) → [:PARTICIPATED_IN]
    if post.get("author"):
        session.run("""
            MATCH (a:Account {username: $username})
            MATCH (e:Event {id: $eid})
            MERGE (a)-[:PARTICIPATED_IN]->(e)
        """, username=post["author"], eid=target_id)

    # Organizer (primer ORG de NER) → [:ORGANIZED]
    if event.get("organizerOrg"):
        # Buscar cuenta cuyo fullName o username contenga el nombre del org
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

    # Tagged users → [:PARTICIPATED_IN]
    for tu in post.get("taggedUsers", []):
        session.run("""
            MATCH (a:Account {username: $username})
            MATCH (e:Event {id: $eid})
            MERGE (a)-[:PARTICIPATED_IN]->(e)
        """, username=tu, eid=target_id)

    # Mentioned accounts → [:SUPPORTED]
    for mention in post.get("mentions", []):
        session.run("""
            MATCH (a:Account {username: $username})
            MATCH (e:Event {id: $eid})
            MERGE (a)-[:SUPPORTED]->(e)
        """, username=mention, eid=target_id)


# ── 10. Pipeline principal ────────────────────────────────────────────────────
def run_extraction(
    threshold: float = 0.55,
    max_posts: int   = 0,
    batch_size: int  = 20,
    date_window: int = 3,
    sim_threshold: float = 0.82,
    dry_run: bool = False,
):
    print("\n🎭 Fase 4-B — Extracción de Eventos")
    print("=" * 55)

    # Cargar posts pendientes
    limit_clause = f"LIMIT {max_posts}" if max_posts > 0 else ""
    with driver.session() as session:
        posts = session.run(f"""
            MATCH (a:Account)-[:PUBLISHED]->(p:Post)
            WHERE p.caption IS NOT NULL
              AND size(p.caption) >= {MIN_CAPTION_LEN}
              AND (p.eventExtracted IS NULL OR p.eventExtracted = false)
            RETURN p.id          AS id,
                   p.caption     AS caption,
                   p.likesCount  AS likes,
                   p.commentsCount AS comments,
                   p.timestamp   AS timestamp,
                   p.hashtags    AS hashtags,
                   a.username    AS author,
                   collect(DISTINCT [(p)-[:TAGS_USER]->(tu) | tu.username])[0] AS taggedUsers,
                   collect(DISTINCT [(p)-[:MENTIONS]->(m)   | m.username])[0]  AS mentions
            {limit_clause}
        """).data()

    if not posts:
        print("  ✅ No hay posts pendientes.")
        return

    print(f"  🔍 {len(posts)} posts a clasificar")

    # Cargar modelos
    print(f"\n  📦 Cargando ZS classifier: {ZS_MODEL}")
    from transformers import pipeline as hf_pipeline
    classifier = hf_pipeline(
        "zero-shot-classification",
        model=ZS_MODEL,
        device=-1,  # CPU; cambiar a 0 para GPU
    )

    print(f"  📦 Cargando sentence-transformers: {ST_MODEL}")
    from sentence_transformers import SentenceTransformer
    st_model = SentenceTransformer(ST_MODEL)

    # Cache de eventos existentes para resolución inline
    with driver.session() as session:
        cache = load_events_cache(session)

    created  = 0
    enriched = 0
    skipped  = 0
    processed_ids: list = []

    for i in tqdm(range(0, len(posts), batch_size), desc="  posts"):
        batch = posts[i: i + batch_size]

        # Zero-shot classification en batch
        captions = [p["caption"][:512] for p in batch]
        zs_results = classifier(captions, EVENT_LABELS, multi_label=False)
        if isinstance(zs_results, dict):
            zs_results = [zs_results]

        for post, zs in zip(batch, zs_results):
            top_label = zs["labels"][0]
            top_score = zs["scores"][0]

            # Filtro: rechazar si es "no evento" o confianza baja
            if top_label == NON_EVENT_LABEL or top_score < threshold:
                skipped += 1
                processed_ids.append(post["id"])
                continue

            # NER
            lang = detect_text_lang(post["caption"])
            ner  = extract_ner(post["caption"], lang)

            raw_date  = ner["dates"][0]   if ner["dates"]     else None
            loc_name  = ner["locations"][0] if ner["locations"] else None
            org_name  = ner["orgs"][0]    if ner["orgs"]      else None
            event_date = parse_date_safe(raw_date)

            # Hotness
            hotness = compute_hotness(
                post.get("likes", 0) or 0,
                post.get("comments", 0) or 0,
                post.get("timestamp", "") or "",
            )

            # Embedding del evento (texto = caption snippet + tipo + fecha + lugar)
            emb_text = f"{post['caption'][:200]} {top_label} {raw_date or ''} {loc_name or ''}"
            embedding = st_model.encode([emb_text], show_progress_bar=False)[0].tolist()

            event_id = make_event_id(top_label, raw_date or "", loc_name or "")

            candidate = {
                "id":           event_id,
                "title":        top_label.title(),
                "type":         top_label,
                "rawDate":      raw_date or "",
                "eventDate":    event_date or "",
                "locationName": loc_name or "",
                "hotnessScore": hotness,
                "confidence":   round(top_score, 4),
                "embedding":    embedding,
                "organizerOrg": org_name,
                "createdAt":    datetime.now(timezone.utc).isoformat(),
            }

            # Resolver inline
            existing_id = find_similar_event(candidate, cache, sim_threshold, date_window)

            if dry_run:
                action = "→ ENRICH" if existing_id else "→ CREATE"
                print(f"  [dry-run] {action} | {top_label} | {loc_name} | {raw_date} | score={top_score:.2f}")
                processed_ids.append(post["id"])
                continue

            with driver.session() as session:
                upsert_event(session, candidate, post, existing_id)

            if existing_id:
                enriched += 1
                # Actualizar hotness en cache
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

        # Marcar batch como procesado
        if not dry_run and processed_ids:
            with driver.session() as session:
                session.run("""
                    UNWIND $ids AS pid
                    MATCH (p:Post {id: pid})
                    SET p.eventExtracted = true
                """, ids=processed_ids[-len(batch):])

    # Marcar cualquier restante
    if not dry_run and processed_ids:
        remaining = [pid for pid in processed_ids]
        if remaining:
            with driver.session() as session:
                session.run("""
                    UNWIND $ids AS pid
                    MATCH (p:Post {id: pid})
                    SET p.eventExtracted = true
                """, ids=remaining)

    print(f"\n  ✅ Posts procesados : {len(posts)}")
    print(f"  🆕 Eventos creados  : {created}")
    print(f"  🔄 Eventos enriquecidos: {enriched}")
    print(f"  ⏭️  Sin evento       : {skipped}")


# ── 11. CLI ───────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    threshold: float = typer.Option(
        0.55, "--threshold", help="Confianza mínima ZS para considerar evento."
    ),
    sim_threshold: float = typer.Option(
        0.82, "--sim-threshold", help="Similitud coseno mínima para deduplicar."
    ),
    date_window: int = typer.Option(
        3, "--date-window", help="Ventana de días para deduplicación por fecha."
    ),
    max_posts: int = typer.Option(
        0, "--max-posts", help="Límite de posts a procesar (0 = todos)."
    ),
    batch_size: int = typer.Option(
        20, "--batch-size", help="Posts por lote para ZS classification."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Solo mostrar eventos detectados, no escribir."
    ),
):
    """
    Fase 4-B: detecta eventos en Post.caption, crea nodos (:Event) en Neo4j.

    Recomendado: ejecutar nlp_enrich_nodes.py primero para pre-procesar captions.
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
