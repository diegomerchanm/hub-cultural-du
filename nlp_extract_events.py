"""
Fase 4-B — Extracción de eventos culturales desde Post.caption.

Arquitectura de 3 capas:
  Capa 1 — sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
            Similitud coseno MÁXIMA contra 100 frases de referencia (no promedio).
            Filtra candidatos por max_sim >= layer1_threshold.

  Capa 2a — Detección binaria (mDeBERTa-v3-base-xnli-multilingual-nli-2mil7)
             ¿Es un evento con fecha/lugar? Descarta definitivamente si no.

  Capa 2b — Tipificación multi-label (mismo modelo)
             Solo corre sobre los que pasaron 2a.
             Asigna tipo de evento con 12 labels culturales.

Score final = (layer2_score × 0.6 + hotness_norm × 0.4) × political_penalty

Idempotente: marca cada post procesado con eventExtracted=true.
"""

import hashlib
import math
import os
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import SessionExpired, ServiceUnavailable
from scipy.spatial.distance import cosine as cosine_dist
from tqdm import tqdm

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
    keep_alive=True,
    max_connection_lifetime=1800,   # reciclar conexiones cada 30 min
)


def neo4j_run_with_retry(query, params=None, retries=3):
    """Ejecuta una query con reintento automático si la sesión expira."""
    for attempt in range(retries):
        try:
            with driver.session() as session:
                return session.run(query, **(params or {})).data()
        except (SessionExpired, ServiceUnavailable) as e:
            if attempt < retries - 1:
                print(f"  ⚠️  Conexión Neo4j caída, reintentando ({attempt+1}/{retries})...")
            else:
                raise

# ── 2. Frases de referencia (Capa 1) ─────────────────────────────────────────
EVENT_REFERENCES = [
    # ── CONVOCATORIA DIRECTA ──────────────────────────────────────────────────
    "Te invitamos a nuestro evento este sábado en París",
    "Los esperamos este domingo en nuestra sede",
    "Estás invitado a participar en nuestra próxima actividad",
    "Únete a nosotros este fin de semana",
    "Nos vemos el jueves en la Alianza Francesa",
    "Rejoignez-nous ce samedi soir au centre culturel",
    "Venez nombreux ce vendredi à la galerie",
    "On vous attend ce weekend au consulat",
    "Vous êtes invités à notre prochain événement",
    "Join us this Saturday at the cultural center",
    "We invite you to our next community gathering",
    "See you next Friday at the embassy",
    # ── APERTURA / INAUGURACIÓN ───────────────────────────────────────────────
    "La exposición abre sus puertas este viernes en la galería",
    "El restaurante inaugura su nueva carta este mes",
    "La muestra estará disponible desde el próximo lunes",
    "Abrimos nuestras puertas este sábado a las 6pm",
    "La tienda abre este fin de semana con nueva colección",
    "L'exposition ouvre ses portes vendredi prochain",
    "La galerie inaugure sa nouvelle exposition ce mois-ci",
    "Le festival ouvre ce weekend au parc de la Villette",
    "The exhibition opens its doors next Thursday evening",
    "Grand opening this Saturday, everyone is welcome",
    # ── PROGRAMACIÓN / AGENDA ─────────────────────────────────────────────────
    "La agenda cultural de junio en París",
    "Programación del mes de julio para la comunidad colombiana",
    "Estas son las actividades de la semana en la Alianza Francesa",
    "Cartelera de eventos colombianos en Francia este mes",
    "Agenda de conciertos y exposiciones para agosto",
    "Le programme culturel du mois de juin à Paris",
    "Voici notre agenda pour les prochaines semaines",
    "Découvrez la programmation de juillet au centre culturel",
    "This month's agenda at the Colombian consulate in Paris",
    "Upcoming events for the Colombian community in France",
    # ── FECHA Y LUGAR EXPLÍCITOS ──────────────────────────────────────────────
    "El concierto es el sábado 15 de junio a las 8pm en la sala principal",
    "El taller se realizará el próximo martes en la rue de Berri",
    "La conferencia tendrá lugar el 20 de julio en el Instituto Francés",
    "El festival colombiano es el domingo en el Parc de la Villette",
    "Evento este viernes a las 7pm en la Alianza Francesa de París",
    "Le concert aura lieu samedi 15 juin à 20h salle Pleyel",
    "L'atelier se tiendra mardi prochain rue de Rivoli",
    "La conférence aura lieu le 20 juillet à l'Institut Français",
    "The concert is on Saturday June 15th at 8pm at the main hall",
    "Workshop next Tuesday at the Colombian cultural center",
    # ── INSCRIPCIÓN / REGISTRO ────────────────────────────────────────────────
    "Inscríbete antes del viernes para reservar tu lugar",
    "Cupos limitados para el taller del próximo mes",
    "Regístrate ahora para el próximo encuentro comunitario",
    "Las inscripciones están abiertas hasta el domingo",
    "Reserva tu entrada para el concierto de este sábado",
    "Inscrivez-vous avant vendredi pour réserver votre place",
    "Les inscriptions sont ouvertes jusqu'à dimanche",
    "Places limitées pour l'atelier du mois prochain",
    "Register now for the upcoming community event",
    "Book your tickets for this Saturday's concert",
    # ── ENTRADA LIBRE ─────────────────────────────────────────────────────────
    "Entrada libre este sábado a partir de las 6pm",
    "El evento es gratuito y abierto a todo público",
    "Acceso libre para la comunidad colombiana en Francia",
    "Sin costo de entrada, todos son bienvenidos este domingo",
    "Entrée libre ce samedi à partir de 18h",
    "L'événement est gratuit et ouvert à tous",
    "Accès libre pour la communauté colombienne en France",
    "Free entry this Saturday from 6pm onwards",
    "No registration needed, just show up this Sunday",
    # ── RECORDATORIO / CUENTA REGRESIVA ──────────────────────────────────────
    "Faltan 3 días para nuestro festival gastronómico",
    "Este sábado es el gran día, no te lo pierdas",
    "Último recordatorio para el evento de mañana",
    "Quedan pocas horas para el concierto de esta noche",
    "No olvides que mañana es la inauguración",
    "Plus que 3 jours avant notre festival culturel",
    "C'est ce samedi, ne manquez pas l'événement",
    "Dernier rappel pour l'événement de demain soir",
    "Only 3 days left until our cultural festival",
    "Reminder: the concert is tomorrow night",
    # ── MUSICAL ───────────────────────────────────────────────────────────────
    "Concierto de música colombiana este viernes en París",
    "El grupo de salsa se presenta este sábado en la sala",
    "Noche de cumbia y vallenato en el centro cultural",
    "Concert de musique colombienne vendredi soir à Paris",
    "Soirée salsa et cumbia ce samedi au centre culturel",
    # ── GASTRONÓMICO ──────────────────────────────────────────────────────────
    "Feria gastronómica colombiana este domingo en el parque",
    "Degustación de comida colombiana este sábado",
    "Pop-up de cocina colombiana el próximo fin de semana",
    "Festival de la gastronomie colombienne ce dimanche",
    "Colombian food fair this weekend at the market",
    # ── ARTÍSTICO ─────────────────────────────────────────────────────────────
    "Vernissage de la exposición de arte colombiano este jueves",
    "La instalación artística se inaugura el próximo viernes",
    "Presentación de danza contemporánea colombiana este mes",
    "Vernissage de l'exposition d'art colombien jeudi soir",
    "Colombian contemporary dance performance this weekend",
    # ── ACADÉMICO ─────────────────────────────────────────────────────────────
    "Conferencia sobre la diáspora colombiana en Europa este martes",
    "Panel de discusión sobre cultura e identidad colombiana",
    "Taller de español para la comunidad latinoamericana",
    "Conférence sur la diaspora colombienne en Europe mardi",
    "Workshop on Colombian identity and culture in France",
    # ── COMUNITARIO ───────────────────────────────────────────────────────────
    "Encuentro de la comunidad colombiana en París este domingo",
    "Reunión de connacionales colombianos en Francia",
    "Picnic comunitario para colombianos en el parque este sábado",
    "Rencontre de la communauté colombienne à Paris ce dimanche",
    "Colombian community gathering in Paris this weekend",
    # ── DEPORTIVO ─────────────────────────────────────────────────────────────
    "Torneo de fútbol colombiano este fin de semana en París",
    "Clases de salsa y baile colombiano este sábado",
    "Tournoi de football colombien ce weekend à Paris",
    "Colombian salsa dance class this Saturday evening",
    # ── INSTITUCIONAL ─────────────────────────────────────────────────────────
    "El consulado colombiano en París organiza una jornada especial",
    "Jornada de atención consular extraordinaria este mes",
    "Le consulat colombien organise une journée spéciale ce mois",
    "Special consular service day at the Colombian embassy",
]

# ── 3. Taxonomía Capa 2 ───────────────────────────────────────────────────────
_LABEL_META: list[tuple[str, str, float]] = [
    ("concierto, recital o presentación musical en vivo",        "musical",       1.0),
    ("exposición, muestra o vernissage de arte visual",          "visual",        1.0),
    ("instalación artística o intervención urbana",              "visual",        1.0),
    ("obra de teatro, danza o performance escénica",             "escenico",      1.0),
    ("proyección cinematográfica, documental o audiovisual",     "audiovisual",   1.0),
    ("taller, clase o formación creativa y artística",           "formacion",     1.0),
    ("residencia artística o convocatoria cultural",             "formacion",     1.0),
    ("festival, feria o celebración de la cultura colombiana",   "festival",      1.0),
    ("encuentro comunitario o evento de la diáspora colombiana", "comunitario",   1.0),
    ("evento del consulado, embajada o institución colombiana",  "institucional", 1.0),
    ("conferencia, charla académica o panel cultural",           "academico",     1.0),
    ("evento gastronómico o muestra culinaria colombiana",       "gastronomico",  1.0),
    ("acto político, electoral o gubernamental",                 "politico",      0.2),
    ("publicación informativa, noticia o comunicado",            "nulo",          0.0),
    ("promoción comercial, oferta o publicidad",                 "nulo",          0.0),
    ("contenido personal, cotidiano o sin relación cultural",    "nulo",          0.0),
]

EVENT_LABELS = [lbl for lbl, _, _ in _LABEL_META]
_CAT_MAP     = {lbl: cat for lbl, cat, _   in _LABEL_META}
_PEN_MAP     = {lbl: pen for lbl, _, pen   in _LABEL_META}
NULL_CATS    = {"nulo"}

HOTNESS_MAX     = 6.0
MIN_CAPTION_LEN = 40
ZS_MODEL        = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
ST_MODEL        = "paraphrase-multilingual-MiniLM-L12-v2"
LANG_TO_MODEL   = {"es": "es_core_news_lg", "en": "en_core_web_sm", "fr": "fr_core_news_lg"}
# Fecha de corte fija del estudio — ancla recencia, no datetime.now()
STUDY_CUTOFF    = datetime(2026, 7, 1, tzinfo=timezone.utc)
_NLP: dict      = {}
_ST_MODEL       = None   # sentence-transformer compartido entre capas

# Labels para Capa 2a (detección binaria)
BINARY_LABELS = [
    "un evento cultural con fecha o lugar específico",
    "contenido sin ningún evento anunciado",
]
# Labels para Capa 2b (tipificación) — excluye nulo
EVENT_LABELS_CULTURAL = [lbl for lbl, cat, _ in _LABEL_META if cat not in NULL_CATS]

# ── 4. Helpers — modelos ──────────────────────────────────────────────────────
def get_nlp(lang: str):
    if lang not in _NLP:
        model_name = LANG_TO_MODEL.get(lang)
        if not model_name:
            return None
        import spacy
        print(f"  📦 Cargando spaCy: {model_name}")
        _NLP[lang] = spacy.load(model_name, disable=["senter"])
    return _NLP[lang]


def get_st_model():
    global _ST_MODEL
    if _ST_MODEL is None:
        from sentence_transformers import SentenceTransformer
        print(f"  📦 Cargando sentence-transformers: {ST_MODEL}")
        _ST_MODEL = SentenceTransformer(ST_MODEL)
    return _ST_MODEL


# ── 5. Capa 1 — matriz de embeddings de referencia ───────────────────────────
_REF_EMBEDDINGS = None   # np.ndarray (100, 384), normalizado

def get_reference_embeddings() -> np.ndarray:
    """Calcula (y cachea) la matriz normalizada de embeddings de EVENT_REFERENCES."""
    global _REF_EMBEDDINGS
    if _REF_EMBEDDINGS is not None:
        return _REF_EMBEDDINGS
    model = get_st_model()
    _REF_EMBEDDINGS = model.encode(
        EVENT_REFERENCES,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )
    return _REF_EMBEDDINGS


def layer1_score(caption: str) -> float:
    """Similitud coseno MÁXIMA entre caption y las frases de referencia."""
    model    = get_st_model()
    cap_emb  = model.encode([caption[:512]], normalize_embeddings=True)[0]  # (384,)
    ref_embs = get_reference_embeddings()                                    # (100, 384)
    sims     = ref_embs @ cap_emb                                            # (100,)
    return float(sims.max())


# ── 6. Helpers — NLP ─────────────────────────────────────────────────────────
def detect_text_lang(text: str) -> str:
    try:
        from langdetect import detect
        lang = detect(text)
        return lang if lang in LANG_TO_MODEL else "es"
    except Exception:
        return "es"


def extract_ner(text: str, lang: str) -> dict:
    nlp    = get_nlp(lang)
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


# ── 7. Helpers — fechas y scores ──────────────────────────────────────────────
# Patrones de fecha para extracción previa antes de dateparser
_DATE_RE = re.compile(
    r"""
    \b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b          # DD/MM o DD/MM/YYYY
    |\b\d{1,2}\s+de\s+[a-záéíóúüñ]+(?:\s+de\s+\d{4})?\b  # 15 de junio [de 2026]
    |\b\d{1,2}\s+[a-záéíóúüñ]{4,}(?:\s+\d{4})?\b         # 15 juin / 15 june 2026
    |\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo
         |lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche
         |monday|tuesday|wednesday|thursday|friday|saturday|sunday)
       (?:\s+\d{1,2}(?:\s+de\s+[a-záéíóúüñ]+)?)?
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_dates(text: str, post_timestamp: str) -> Optional[str]:
    """Extrae y normaliza la primera fecha del texto, anclada al timestamp del post."""
    import dateparser

    try:
        anchor = datetime.fromisoformat(post_timestamp.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        anchor = STUDY_CUTOFF.replace(tzinfo=None)

    settings = {
        "PREFER_DAY_OF_MONTH": "first",
        "RETURN_AS_TIMEZONE_AWARE": False,
        "RELATIVE_BASE": anchor,
        "PREFER_DATES_FROM": "future",
        "LANGUAGES": ["es", "fr", "en"],
    }

    # Primero intenta sobre los fragmentos extraídos por regex (más preciso)
    for match in _DATE_RE.finditer(text[:600]):
        snippet = match.group(0).strip()
        if len(snippet) < 3:
            continue
        parsed = dateparser.parse(snippet, settings=settings)
        if parsed:
            return parsed.strftime("%Y-%m-%d")

    # Fallback: busca fechas en el texto completo
    try:
        from dateparser.search import search_dates
        results = search_dates(text[:600], settings=settings, languages=["es", "fr", "en"])
        if results:
            return results[0][1].strftime("%Y-%m-%d")
    except Exception:
        pass

    return None


def dates_close(d1: Optional[str], d2: Optional[str], window: int) -> bool:
    if not d1 or not d2:
        return True
    try:
        dt1 = datetime.fromisoformat(d1)
        dt2 = datetime.fromisoformat(d2)
        return abs((dt1 - dt2).days) <= window
    except Exception:
        return True


def compute_hotness(likes: int, comments: int, timestamp_str: str) -> float:
    try:
        ts       = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        days_ago = max(0, (STUDY_CUTOFF - ts).days)   # anclado a fecha de corte fija
    except Exception:
        days_ago = 180
    recency = max(0.0, 1.0 - days_ago / 730.0)
    return round(math.log1p(likes) * 0.4 + math.log1p(comments) * 0.3 + recency * 2.0 * 0.3, 4)


def compute_event_score(layer2_score: float, hotness: float, penalty: float) -> float:
    hotness_norm = min(hotness / HOTNESS_MAX, 1.0)
    return round((layer2_score * 0.6 + hotness_norm * 0.4) * penalty, 4)


# ── 8. ID estable para eventos ────────────────────────────────────────────────
def make_event_id(event_type: str, event_date: str, loc_name: str) -> str:
    """ID estable basado en tipo, fecha ISO normalizada y ubicación."""
    key = f"{event_type}|{(event_date or '').strip()}|{(loc_name or '').lower().strip()}"
    return "evt_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


# ── 9. Resolver inline ────────────────────────────────────────────────────────
def find_similar_event(candidate: dict, cache: list, sim_thr: float, date_window: int) -> Optional[str]:
    cand_emb  = candidate.get("embedding")
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
        if float(1.0 - cosine_dist(cand_emb, exist_emb)) >= sim_thr:
            return existing["id"]
    return None


# ── 10. Neo4j — cache ─────────────────────────────────────────────────────────
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


# ── 11. Neo4j — crear o enriquecer evento ────────────────────────────────────
def upsert_event(session, event: dict, post: dict, existing_id: Optional[str]):
    target_id = existing_id or event["id"]

    if existing_id:
        session.run("""
            MATCH (e:Event {id: $id})
            SET e.eventScore   = CASE WHEN $score   > e.eventScore   THEN $score   ELSE e.eventScore   END,
                e.hotnessScore = CASE WHEN $hotness > e.hotnessScore THEN $hotness ELSE e.hotnessScore END,
                e.postCount    = coalesce(e.postCount, 0) + 1
        """, id=existing_id, score=event["eventScore"], hotness=event["hotnessScore"])
    else:
        session.run("""
            MERGE (e:Event {id: $id})
            SET e.title        = $title,
                e.type         = $type,
                e.category     = $category,
                e.rawDate      = $rawDate,
                e.eventDate    = $eventDate,
                e.locationName = $locationName,
                e.hotnessScore = $hotnessScore,
                e.eventScore   = $eventScore,
                e.confidence   = $confidence,
                e.layer1Score  = $layer1Score,
                e.postCount    = 1,
                e.embedding    = $embedding,
                e.createdAt    = $createdAt
        """, **{k: event[k] for k in [
            "id", "title", "type", "category", "rawDate", "eventDate",
            "locationName", "hotnessScore", "eventScore", "confidence",
            "layer1Score", "embedding", "createdAt",
        ]})
        if event.get("locationName"):
            session.run("""
                MATCH (e:Event {id: $eid})
                MERGE (l:Location {name: $loc})
                MERGE (e)-[:LOCATED_AT]->(l)
            """, eid=target_id, loc=event["locationName"])

    session.run("""
        MATCH (p:Post {id: $pid}) MATCH (e:Event {id: $eid})
        MERGE (p)-[:MENTIONS_EVENT]->(e)
    """, pid=post["id"], eid=target_id)

    for tag in (post.get("hashtags") or []):
        if tag:
            session.run("""
                MATCH (e:Event {id: $eid})
                MERGE (h:Hashtag {name: $tag})
                MERGE (e)-[:HAS_HASHTAG]->(h)
            """, eid=target_id, tag=tag.lower())

    if post.get("author"):
        session.run("""
            MATCH (a:Account {username: $username}) MATCH (e:Event {id: $eid})
            MERGE (a)-[:PARTICIPATED_IN]->(e)
        """, username=post["author"], eid=target_id)

    if event.get("organizerOrg"):
        result = session.run("""
            MATCH (a:Account)
            WHERE toLower(a.fullName) CONTAINS toLower($org)
               OR toLower(a.username) CONTAINS toLower($org)
            RETURN a.username AS username LIMIT 1
        """, org=event["organizerOrg"]).single()
        if result:
            session.run("""
                MATCH (a:Account {username: $username}) MATCH (e:Event {id: $eid})
                MERGE (a)-[:ORGANIZED]->(e)
            """, username=result["username"], eid=target_id)

    for tu in (post.get("taggedUsers") or []):
        session.run("""
            MATCH (a:Account {username: $username}) MATCH (e:Event {id: $eid})
            MERGE (a)-[:PARTICIPATED_IN]->(e)
        """, username=tu, eid=target_id)

    for mention in (post.get("mentions") or []):
        session.run("""
            MATCH (a:Account {username: $username}) MATCH (e:Event {id: $eid})
            MERGE (a)-[:SUPPORTED]->(e)
        """, username=mention, eid=target_id)


# ── 12. Pipeline principal ────────────────────────────────────────────────────
def run_extraction(
    layer1_threshold: float = 0.45,
    layer2_threshold: float = 0.40,
    max_posts: int          = 50,
    batch_size: int         = 20,
    date_window: int        = 3,
    sim_threshold: float    = 0.82,
    dry_run: bool           = False,
    accounts: list[str]     = None,
):
    print("\n🎭 Fase 4-B — Extracción de Eventos (2 capas)")
    print("=" * 60)
    print(f"  L1≥{layer1_threshold}  L2≥{layer2_threshold}  "
          f"max_posts={max_posts or '∞'}  batch={batch_size}  "
          f"sim≥{sim_threshold}  date±{date_window}d")
    if accounts:
        print(f"  Filtro cuentas: {accounts}")

    # Cargar posts
    limit_clause = f"LIMIT {max_posts}" if max_posts > 0 else ""
    account_filter = "AND a.username IN $accounts" if accounts else ""
    with driver.session() as session:
        posts = session.run(f"""
            MATCH (a:Account)-[:PUBLISHED]->(p:Post)
            WHERE p.caption IS NOT NULL
              AND size(p.caption) >= {MIN_CAPTION_LEN}
              AND (p.eventExtracted IS NULL OR p.eventExtracted = false)
              {account_filter}
            RETURN p.id            AS id,
                   p.caption       AS caption,
                   p.likesCount    AS likes,
                   p.commentsCount AS comments,
                   p.timestamp     AS timestamp,
                   p.hashtags      AS hashtags,
                   a.username      AS author,
                   collect(DISTINCT [(p)-[:TAGS_USER]->(tu) | tu.username])[0] AS taggedUsers,
                   collect(DISTINCT [(p)-[:MENTIONS]->(m)   | m.username])[0]  AS mentions
            {limit_clause}
        """, accounts=accounts or []).data()

    if not posts:
        print("  ✅ No hay posts pendientes.")
        return

    print(f"\n  🔍 {len(posts)} posts cargados")

    # Inicializar modelos
    st_model = get_st_model()
    print("  📐 Calculando embeddings de referencia (100 frases)...")
    ref_embs = get_reference_embeddings()   # (100, 384) normalizado
    print(f"  ✅ Embeddings de referencia listos  (shape={ref_embs.shape})")

    # ── Capa 1 — scoring masivo con similitud máxima ──────────────────────────
    print(f"\n  🔵 Capa 1 — similitud coseno máxima contra 100 referencias...")
    all_captions = [p["caption"][:512] for p in posts]
    all_embs     = st_model.encode(
        all_captions,
        normalize_embeddings=True,
        show_progress_bar=True,
        batch_size=32,
    )                                        # (N, 384)
    sims          = all_embs @ ref_embs.T   # (N, 100)
    layer1_scores = sims.max(axis=1).tolist()  # (N,) — máximo, no promedio

    candidates_idx = [i for i, s in enumerate(layer1_scores) if s >= layer1_threshold]
    rejected_l1    = len(posts) - len(candidates_idx)
    print(f"  ✅ Capa 1: {len(candidates_idx)} candidatos  "
          f"({rejected_l1} descartados, L1<{layer1_threshold})")

    # ── Capa 2 — zero-shot (2a detección + 2b tipificación) ──────────────────
    print(f"\n  🟠 Capa 2 — zero-shot classification ({ZS_MODEL})...")
    from transformers import pipeline as hf_pipeline
    classifier = hf_pipeline(
        "zero-shot-classification",
        model=ZS_MODEL,
        device=-1,
    )
    HYP_TMPL = "Esta publicación anuncia {}."

    with driver.session() as session:
        cache = load_events_cache(session)

    created       = 0
    enriched      = 0
    skipped_l1    = rejected_l1
    skipped_l2    = 0
    processed_ids = [posts[i]["id"] for i in range(len(posts)) if i not in set(candidates_idx)]
    dry_counts:   dict = defaultdict(int)
    diag_all:     list = []   # todos los posts — para distribución L1
    diag_cands:   list = []   # solo candidatos L1 — para distribución L2 y ejemplos

    # Registrar los rechazados de Capa 1 en diag_all
    for i, post in enumerate(posts):
        if i not in set(candidates_idx):
            diag_all.append({
                "caption":     post["caption"],
                "author":      post.get("author", ""),
                "layer1":      layer1_scores[i],
                "layer2":      None,
                "hotness":     compute_hotness(
                                   post.get("likes", 0) or 0,
                                   post.get("comments", 0) or 0,
                                   post.get("timestamp", "") or "",
                               ),
                "event_score": 0.0,
                "category":    "—",
                "decision":    "descartado-L1",
                "loc_name":    "",
                "raw_date":    "",
                "top3":        [],
            })

    # Procesar candidatos en batches para Capa 2
    cand_posts  = [posts[i] for i in candidates_idx]
    cand_embs   = [all_embs[i] for i in candidates_idx]
    cand_l1     = [layer1_scores[i] for i in candidates_idx]

    for bi in tqdm(range(0, len(cand_posts), batch_size), desc="  Capa 2"):
        batch       = cand_posts[bi: bi + batch_size]
        batch_embs  = cand_embs[bi: bi + batch_size]
        batch_l1    = cand_l1[bi: bi + batch_size]
        captions_zs = [p["caption"][:512] for p in batch]

        # ── Capa 2a — detección binaria ──────────────────────────────────────
        det_results = classifier(
            captions_zs,
            BINARY_LABELS,
            hypothesis_template=HYP_TMPL,
            multi_label=False,
        )
        if isinstance(det_results, dict):
            det_results = [det_results]

        # ── Capa 2b — tipificación solo sobre eventos detectados ──────────────
        event_indices = [
            j for j, dr in enumerate(det_results)
            if dr["labels"][0] == BINARY_LABELS[0] and dr["scores"][0] >= layer2_threshold
        ]
        type_map: dict = {}
        if event_indices:
            typ_list = classifier(
                [captions_zs[j] for j in event_indices],
                EVENT_LABELS_CULTURAL,
                hypothesis_template=HYP_TMPL,
                multi_label=True,
            )
            if isinstance(typ_list, dict):
                typ_list = [typ_list]
            for j, tr in zip(event_indices, typ_list):
                type_map[j] = tr

        for idx, (post, emb, l1, det) in enumerate(zip(batch, batch_embs, batch_l1, det_results)):
            det_score = det["scores"][0]
            is_event  = det["labels"][0] == BINARY_LABELS[0] and det_score >= layer2_threshold

            # Tipo desde Capa 2b
            typ       = type_map.get(idx)
            top_label = typ["labels"][0]  if typ else (EVENT_LABELS_CULTURAL[0] if is_event else EVENT_LABELS[-1])
            type_score = typ["scores"][0] if typ else det_score
            top3       = list(zip(typ["labels"][:3], typ["scores"][:3])) if typ else []

            category  = _CAT_MAP.get(top_label, "nulo") if is_event else "nulo"
            penalty   = _PEN_MAP.get(top_label, 0.0)    if is_event else 0.0

            lang       = detect_text_lang(post["caption"])
            ner        = extract_ner(post["caption"], lang)
            loc_name   = ner["locations"][0] if ner["locations"] else None
            org_name   = ner["orgs"][0]      if ner["orgs"]      else None
            # FIX 2: fechas ancladas al timestamp del post, no a datetime.now()
            event_date = extract_dates(post["caption"], post.get("timestamp", "") or "")

            hotness     = compute_hotness(
                post.get("likes", 0) or 0,
                post.get("comments", 0) or 0,
                post.get("timestamp", "") or "",
            )
            event_score = compute_event_score(det_score, hotness, penalty) if is_event else 0.0

            record = {
                "caption":     post["caption"],
                "author":      post.get("author", ""),
                "layer1":      l1,
                "layer2":      det_score,     # score de detección 2a
                "hotness":     hotness,
                "event_score": event_score,
                "category":    category,
                "decision":    "EVENTO" if is_event else "no evento",
                "loc_name":    loc_name or "",
                "raw_date":    event_date or "",
                "top3":        top3,          # tipos de Capa 2b
            }
            diag_all.append(record)
            diag_cands.append(record)

            if is_event:
                dry_counts[category] += 1

            if not is_event:
                skipped_l2 += 1
                processed_ids.append(post["id"])
                continue

            if dry_run:
                processed_ids.append(post["id"])
                continue

            emb_text  = f"{post['caption'][:200]} {top_label} {event_date or ''} {loc_name or ''}"
            event_emb = st_model.encode([emb_text], normalize_embeddings=True, show_progress_bar=False)[0].tolist()
            # FIX make_event_id usa event_date ISO, no raw_date
            event_id  = make_event_id(top_label, event_date or "", loc_name or "")

            candidate = {
                "id":           event_id,
                "title":        top_label.title(),
                "type":         top_label,
                "category":     category,
                "rawDate":      event_date or "",
                "eventDate":    event_date or "",
                "locationName": loc_name or "",
                "hotnessScore": hotness,
                "eventScore":   event_score,
                "confidence":   round(det_score, 4),
                "layer1Score":  round(l1, 4),
                "embedding":    event_emb,
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
                    "embedding":    event_emb,
                })
            processed_ids.append(post["id"])

        if not dry_run:
            neo4j_run_with_retry("""
                UNWIND $ids AS pid
                MATCH (p:Post {id: pid})
                SET p.eventExtracted = true
            """, {"ids": processed_ids[-len(batch):]})

    if not dry_run and processed_ids:
        neo4j_run_with_retry("""
            UNWIND $ids AS pid
            MATCH (p:Post {id: pid})
            SET p.eventExtracted = true
        """, {"ids": processed_ids})

    # ── Resumen ───────────────────────────────────────────────────────────────
    detected = [r for r in diag_cands if r["decision"] == "EVENTO"]
    print(f"\n{'═'*60}")
    print(f"  ✅ Posts procesados    : {len(posts)}")
    print(f"  🔵 Descartados Capa 1  : {skipped_l1}  (L1<{layer1_threshold})")
    print(f"  🟠 Candidatos Capa 2   : {len(cand_posts)}")
    print(f"  ⏭️  Descartados Capa 2  : {skipped_l2}")
    print(f"  🎭 Eventos detectados  : {len(detected)}")
    if not dry_run:
        print(f"  🆕 Eventos creados     : {created}")
        print(f"  🔄 Eventos enriquecidos: {enriched}")
    if dry_counts:
        print("\n  Por categoría:")
        for cat, n in sorted(dry_counts.items(), key=lambda x: -x[1]):
            print(f"    {n:>4}  {cat}")

    if not dry_run or not diag_cands:
        return

    # ── DIAGNÓSTICO (dry-run) ─────────────────────────────────────────────────

    # 1. Todos los eventos detectados
    print(f"\n{'═'*60}")
    print(f"  🎭 EVENTOS DETECTADOS ({len(detected)}) — ordenados por eventScore")
    print(f"{'═'*60}")
    for i, r in enumerate(sorted(detected, key=lambda x: -x["event_score"]), 1):
        print(f"\n  [{i:02d}] @{r['author']}  cat={r['category']}")
        print(f"       eventScore={r['event_score']:.3f}  "
              f"L1={r['layer1']:.3f}  L2={r['layer2']:.3f}  hot={r['hotness']:.2f}")
        print(f"       loc={r['loc_name'] or '-'}  date={r['raw_date'] or '-'}")
        print(f"       Label  : {r['top3'][0][0] if r['top3'] else '-'}")
        print(f"       Caption: {r['caption'].replace(chr(10), ' ')}")

    # 2. Distribución de scores L1 y L2
    l1_all   = [r["layer1"] for r in diag_all]
    l2_cands = [r["layer2"] for r in diag_cands if r["layer2"] is not None]
    ev_scores = [r["event_score"] for r in detected]

    print(f"\n{'─'*60}")
    print("  📊 DIAGNÓSTICO — Distribución de scores")
    print(f"{'─'*60}")
    print(f"  Capa 1 — similitud coseno (todos los posts, N={len(l1_all)}):")
    print(f"    min={min(l1_all):.3f}  max={max(l1_all):.3f}  avg={sum(l1_all)/len(l1_all):.3f}")
    if l2_cands:
        print(f"  Capa 2 — ZS confidence (candidatos L1, N={len(l2_cands)}):")
        print(f"    min={min(l2_cands):.3f}  max={max(l2_cands):.3f}  avg={sum(l2_cands)/len(l2_cands):.3f}")
    if ev_scores:
        print(f"  Event score compuesto (eventos aceptados, N={len(ev_scores)}):")
        print(f"    min={min(ev_scores):.3f}  max={max(ev_scores):.3f}  avg={sum(ev_scores)/len(ev_scores):.3f}")

    # 3. Top-5 falsos negativos (pasaron L1 pero no L2, label no nulo)
    false_neg = [
        r for r in diag_cands
        if r["decision"] == "no evento"
        and _CAT_MAP.get(r["top3"][0][0] if r["top3"] else "", "nulo") not in NULL_CATS
    ]
    top5_fn = sorted(false_neg, key=lambda x: -(x["layer2"] or 0))[:5]
    if top5_fn:
        print(f"\n{'─'*60}")
        print(f"  🔍 TOP-5 POSIBLES FALSOS NEGATIVOS (L2<{layer2_threshold}, label no nulo)")
        print(f"{'─'*60}")
        for i, r in enumerate(top5_fn, 1):
            print(f"\n  [{i}] @{r['author']}  L1={r['layer1']:.3f}  "
                  f"L2={r['layer2']:.3f}  hot={r['hotness']:.2f}  cat={r['category']}")
            print(f"      Label  : {r['top3'][0][0] if r['top3'] else '-'}")
            print(f"      Caption: {r['caption'].replace(chr(10), ' ')}")
            print(f"      Top-3  :")
            for label, score in r["top3"]:
                marker = "◀" if label == r["top3"][0][0] else " "
                print(f"               {score:.3f}  {label} {marker}")

    # 4. 10 ejemplos aleatorios
    print(f"\n{'─'*60}")
    print("  🎲 10 EJEMPLOS ALEATORIOS (candidatos Capa 1)")
    print(f"{'─'*60}")
    sample = random.sample(diag_cands, min(10, len(diag_cands)))
    for i, r in enumerate(sample, 1):
        preview = r["caption"][:100].replace("\n", " ")
        print(f"\n  [{i:02d}] {r['decision'].upper()}  cat={r['category']}  @{r['author']}")
        print(f"       L1={r['layer1']:.3f}  L2={r['layer2']:.3f}  "
              f"eventScore={r['event_score']:.3f}  hot={r['hotness']:.2f}")
        print(f"       Caption : {preview!r}")
        if r["top3"]:
            print(f"       Top-3   :")
            for label, score in r["top3"]:
                marker = "◀" if label == r["top3"][0][0] else " "
                print(f"                {score:.3f}  {label} {marker}")
    print(f"{'─'*60}")


# ── 13. CLI ───────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    threshold: float = typer.Option(
        0.45, "--threshold",
        help="Threshold de similitud coseno para Capa 1 (sentence-transformers).",
    ),
    layer2_threshold: float = typer.Option(
        0.40, "--layer2-threshold",
        help="Confianza mínima ZS para Capa 2 (cross-encoder).",
    ),
    sim_threshold: float = typer.Option(
        0.82, "--sim-threshold",
        help="Similitud coseno para deduplicar eventos existentes.",
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
        help="Posts por lote en Capa 2 (zero-shot).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Clasificar y mostrar diagnóstico completo sin escribir en Neo4j.",
    ),
    accounts: Optional[str] = typer.Option(
        None, "--accounts",
        help="Cuentas a procesar separadas por coma, e.g. dichaparis,ivan_argote. Sin filtro = todas.",
    ),
):
    """
    Fase 4-B: extracción de eventos en 2 capas.

    Capa 1: sentence-transformers filtra candidatos por similitud coseno
    contra embedding promedio de 100 frases de referencia (--threshold).

    Capa 2: zero-shot cross-encoder clasifica tipo de evento solo sobre
    los candidatos de Capa 1 (--layer2-threshold).

    eventScore = (layer2_score × 0.6 + hotness_norm × 0.4) × political_penalty
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    accounts_list = [a.strip() for a in accounts.split(",")] if accounts else None

    run_extraction(
        layer1_threshold=threshold,
        layer2_threshold=layer2_threshold,
        max_posts=max_posts,
        batch_size=batch_size,
        date_window=date_window,
        sim_threshold=sim_threshold,
        dry_run=dry_run,
        accounts=accounts_list,
    )

    driver.close()
    print("\n✅ Extracción de eventos completa.")


if __name__ == "__main__":
    app()
