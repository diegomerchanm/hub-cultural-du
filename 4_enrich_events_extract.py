"""
Fase 4-B — Extracción de eventos culturales desde Post.caption.

Arquitectura de 4 capas:
  Capa 1 — sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
            Similitud coseno MÁXIMA contra 100 frases de referencia (no promedio).
            Filtra candidatos por max_sim >= layer1_threshold.

  Capa 2a — Detección binaria (multilingual-MiniLMv2-L6-mnli-xnli, ligero)
             NLI batcheado con una sola hipótesis → P(entailment).
             ¿Es un evento con fecha/lugar? Descarta definitivamente si no.
             NOTA: se usa el MiniLMv2 multilingüe y NO cross-encoder/
             nli-deberta-v3-small porque este último es monolingüe inglés
             (SNLI/MNLI) y fallaría con captions es/fr.

  Capa 2b — Tipificación multi-label (multilingual-MiniLMv2, batcheado)
             Solo corre sobre los que pasaron 2a (fracción pequeña).
             Asigna tipo de evento con 12 labels culturales.
             --high-quality activa mDeBERTa-v3 (más lento, mejor precisión).

  Capa 3 — LLM: Ollama local (modelo configurable vía OLLAMA_MODEL, default
            qwen2.5:7b) por defecto, Groq disponible vía LLM_PROVIDER=groq
            (ver DD-033 y DD-033 update 3) y Cerebras vía LLM_PROVIDER=cerebras
            (ver DD-033 update 5 — mismo modelo llama-3.3-70b, ~10x cupo diario
            gratis frente a Groq, endpoint OpenAI-compatible). Solo corre
            sobre los que pasaron 2a (~30-50/corrida en pruebas, corpus
            completo en corridas reales — Ollama no tiene tope diario de
            tokens como el free tier de Groq/Cerebras). Limpia fecha/ubicación
            (spaCy/dateparser tienen bugs confirmados), redacta
            clean_description, y detecta noticias institucionales sin
            invitación real al público mediante is_public_invitation/is_upcoming.

Optimizaciones CPU: batch inference en 2a/2b, truncado a 256 tokens,
torch multi-thread, cache de embeddings de referencia en ref_embeddings.npz.

Score final = (layer2_score × 0.6 + hotness_norm × 0.4) × political_penalty × llm_penalty
  llm_penalty = 1.0 si is_public_invitation AND is_upcoming
              = 0.15 (LLM_REJECT_PENALTY)  si el LLM responde y NO es invitación futura
              = 0.5  (LLM_UNKNOWN_PENALTY) si el LLM falla (Ollama no disponible,
                o Groq tras agotar reintentos) — verdicto "incierto", ver DD-033-update.

Idempotente: marca cada post procesado con eventExtracted=true.
"""

import hashlib
import json
import math
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import requests

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
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

if not GROQ_API_KEY:
    print("  ⚠️  GROQ_API_KEY ausente en .env — Capa 3 (LLM) se omitirá (valores null, sin penalización)")

# Endpoint OpenAI-compatible de Groq. Confirmado en console.groq.com/docs/models
# (2026-07-24): llama-3.3-70b-versatile es el modelo de texto grande vigente con
# mejor soporte multilingüe entre los disponibles en producción.
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "llama-3.3-70b-versatile"

# Endpoint OpenAI-compatible de Cerebras (cloud.cerebras.ai). NO es el mismo
# modelo que Groq — Cerebras retiró llama-3.3-70b de su catálogo público;
# el modelo de producción vigente es gpt-oss-120b (OpenAI open-weight),
# confirmado en inference-docs.cerebras.ai/models/overview el 2026-07-30 —
# ver DD-033 (update 5, corregido). Al ser un modelo distinto, NO se puede
# asumir calidad equivalente a Groq solo por compatibilidad de API: hay que
# validar con --dry-run contra una muestra y revisar título/reasoning a mano
# antes de usarlo para escritura real. Además su tier gratis exige tarjeta
# verificada (no es "sin tarjeta" como se asumió inicialmente).
CEREBRAS_ENDPOINT = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL    = "gpt-oss-120b"

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
# Capa 3 (Groq) — penalización cuando el veredicto NO es invitación pública
# futura (o cuando Groq falla tras agotar reintentos: verdicto "incierto",
# ver DD-033-update). No es 1.0 (confianza ciega) ni 0.15 (igual a rechazo
# confirmado) — demora acotada en ambas direcciones.
LLM_REJECT_PENALTY  = 0.15
LLM_UNKNOWN_PENALTY = 0.5
# Clamp de sanidad sobre eventDate: si la fecha razonada por Capa 3 se aleja
# del timestamp del post más de esto, se descarta (probable alucinación del
# LLM, p.ej. confundir una fecha histórica conmemorada con la del evento).
EVENT_DATE_CLAMP_DAYS = 1095
# Capa 2a — detección binaria: modelo NLI multilingüe LIGERO (6 capas).
# ⚠️ No sustituir por cross-encoder/nli-deberta-v3-small: es inglés-only.
DET_MODEL       = "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"
# Capa 2b — tipificación: modelo base multilingüe (mejor calidad).
TYPE_MODEL      = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
ZS_MODEL        = TYPE_MODEL   # compat: nombre usado en logs
ST_MODEL        = "paraphrase-multilingual-MiniLM-L12-v2"
MAX_NLI_TOKENS  = 256          # truncado de captions para NLI (velocidad CPU)
REF_CACHE_PATH  = "ref_embeddings.npz"
LANG_TO_MODEL   = {"es": "es_core_news_lg", "en": "en_core_web_sm", "fr": "fr_core_news_lg"}
# Fecha de corte fija del estudio — ancla recencia, no datetime.now()
STUDY_CUTOFF    = datetime(2026, 7, 1, tzinfo=timezone.utc)
_NLP: dict      = {}
_ST_MODEL       = None   # sentence-transformer compartido entre capas

# Capa 2a: la detección binaria ya no usa labels de pipeline ZS —
# ver DET_HYPOTHESIS + detect_events_batch() (una hipótesis, batcheado).
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


# ── 4b. Capa 2a — detector NLI binario batcheado ─────────────────────────────
DET_HYPOTHESIS = "Esta publicación anuncia un evento cultural con fecha o lugar."

_DET = None  # (tokenizer, model) cargados una sola vez


def get_detector():
    global _DET
    if _DET is None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(os.cpu_count() or 4)
        print(f"  📦 Cargando detector NLI ligero: {DET_MODEL}")
        tok = AutoTokenizer.from_pretrained(DET_MODEL)
        mdl = AutoModelForSequenceClassification.from_pretrained(DET_MODEL)
        mdl.eval()
        _DET = (tok, mdl)
    return _DET


def detect_events_batch(captions: list[str], batch_size: int = 32) -> list[float]:
    """P(evento) por caption, procesando en lotes.

    Una sola hipótesis por caption (en lugar de 2 labels del pipeline ZS)
    → mitad de forward-passes. Score = P(entailment) normalizado contra
    P(contradiction), igual que hace el pipeline zero-shot por label.
    """
    import torch
    tok, mdl = get_detector()
    ent_idx = mdl.config.label2id.get("entailment", 0)
    con_idx = mdl.config.label2id.get("contradiction", 2)

    scores: list[float] = []
    with torch.inference_mode():
        for i in range(0, len(captions), batch_size):
            chunk  = captions[i: i + batch_size]
            inputs = tok(
                chunk,
                [DET_HYPOTHESIS] * len(chunk),
                truncation=True,
                max_length=MAX_NLI_TOKENS,
                padding=True,
                return_tensors="pt",
            )
            logits = mdl(**inputs).logits                    # (B, 3)
            pair   = logits[:, [con_idx, ent_idx]]           # (B, 2)
            probs  = pair.softmax(dim=-1)[:, 1]              # P(entail | ent∨contra)
            scores.extend(probs.tolist())
    return scores


# ── 5. Capa 1 — matriz de embeddings de referencia (con cache en disco) ──────
_REF_EMBEDDINGS = None   # np.ndarray (100, 384), normalizado


def _ref_cache_key() -> str:
    """Hash de frases + modelo: invalida el cache si cambia cualquiera de los dos."""
    payload = ST_MODEL + "\n" + "\n".join(EVENT_REFERENCES)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def get_reference_embeddings() -> np.ndarray:
    """Matriz normalizada de embeddings de EVENT_REFERENCES.

    Orden de resolución: memoria → disco (ref_embeddings.npz) → cómputo.
    El cache en disco evita cargar el sentence-transformer solo para las
    referencias y se invalida automáticamente si cambian frases o modelo.
    """
    global _REF_EMBEDDINGS
    if _REF_EMBEDDINGS is not None:
        return _REF_EMBEDDINGS

    key = _ref_cache_key()
    if os.path.exists(REF_CACHE_PATH):
        try:
            data = np.load(REF_CACHE_PATH, allow_pickle=False)
            if str(data["key"]) == key:
                _REF_EMBEDDINGS = data["embs"]
                print(f"  💾 Embeddings de referencia leídos de {REF_CACHE_PATH}")
                return _REF_EMBEDDINGS
            print("  ♻️  Cache de referencias obsoleto — recalculando")
        except Exception:
            pass  # cache corrupto → recalcular

    model = get_st_model()
    _REF_EMBEDDINGS = model.encode(
        EVENT_REFERENCES,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )
    try:
        np.savez(REF_CACHE_PATH, embs=_REF_EMBEDDINGS, key=np.array(key))
    except Exception as e:
        print(f"  ⚠️  No se pudo guardar {REF_CACHE_PATH}: {e}")
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


# ── 6b. Capa 3 — enriquecimiento LLM (Groq por defecto, Ollama/Cerebras alt.) ──
# LLM_PROVIDER selecciona el transporte: "groq" (default), "cerebras" o
# "ollama". El prompt/esquema es EL MISMO para los tres (ver _build_llm_prompt)
# — solo cambia a quién se le manda. Ver DD-033 (update 4): Ollama local
# descartado por límite de RAM de la máquina (crashea/HTTP 500 junto al resto
# de modelos del pipeline, 36s+/llamada incluso aislado). Groq free tier tiene
# tope real observado de ~100k tokens/día (~78-80 llamadas/día en la práctica,
# ver update 3) — Cerebras (update 5) sirve gpt-oss-120b (modelo DISTINTO a
# Groq, no llama-3.3-70b — Cerebras retiró ese modelo de su catálogo público)
# con ~1M tokens/día gratis pero solo 5 req/min. Al ser otro modelo, su
# calidad de salida en este pipeline específico NO está validada todavía —
# probar con --dry-run antes de usarlo para escritura real.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()

OLLAMA_ENDPOINT = "http://localhost:11434/api/chat"
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()

_LLM_SCHEMA_HINT = """Responde ÚNICAMENTE con un objeto JSON (sin texto adicional) con estas claves exactas:
{
  "is_public_invitation": bool,   // invita al público/diáspora a asistir; no es noticia, comunicado, recap o aviso administrativo
  "is_upcoming": bool,            // describe algo futuro respecto a la fecha de publicación, no algo que ya ocurrió
  "city": string o null,           // ciudad donde ocurre el EVENTO — solo si el caption la menciona o es inequívoca por contexto; NUNCA la ciudad de la cuenta/institución que publica si el caption no la confirma
  "exact_address": string o null,  // dirección o venue específico (calle, número, nombre del lugar) SOLO si aparece textualmente en el caption; si no hay dirección exacta, null — no repitas aquí solo el nombre de la ciudad
  "clean_date": string "YYYY-MM-DD" o null,  // fecha real del evento, razonada por contexto
  "clean_description": string,   // 1-2 oraciones sin emojis/hashtags/menciones, para dashboard
  "title": string,                // título editorial corto (6-10 palabras), sin emojis/hashtags, para mostrar como encabezado de la tarjeta del evento — no repitas la categoría, describe el evento concreto
  "is_free": bool o null,          // true si el caption menciona explícitamente que es gratis/entrada libre, false si menciona un precio, null si el texto no lo aclara (no asumas)
  "reasoning": string             // breve justificación
}"""

LLM_CAPTION_CHARS = 900   # suficiente para juzgar is_public_invitation/is_upcoming/
                           # city/exact_address sin el texto completo


def _build_llm_prompt(caption: str, anchor_date: str) -> str:
    """Prompt/esquema compartido entre Groq y Ollama — el transporte cambia, esto no."""
    return (
        f"Esta publicación de Instagram fue hecha el {anchor_date or 'fecha desconocida'}.\n"
        f"Caption:\n\"\"\"\n{caption[:LLM_CAPTION_CHARS]}\n\"\"\"\n\n"
        "Analiza si esta publicación es una invitación real y abierta a un evento cultural, "
        "o si en realidad es una noticia institucional, un comunicado, la visita de una "
        "personalidad, un aviso administrativo o el recap de algo que ya pasó.\n"
        "Para clean_date razona explícitamente si la fecha mencionada es pasada o futura "
        "según el contexto y la fecha de publicación — no asumas futuro por defecto. Si la "
        "publicación conmemora un aniversario, hito histórico o fecha pasada (ej. \"a 197 años "
        "de...\", \"en 1958...\"), clean_date NO es esa fecha histórica — es la fecha de la "
        "conmemoración/publicación actual (usa la fecha de publicación si no hay otra más "
        "específica).\n"
        "Para title, redacta un título editorial corto (6-10 palabras) que describa el evento "
        "concreto, no la categoría genérica. Para is_free, responde true/false solo si el "
        "texto menciona explícitamente precio o gratuidad — si no hay ninguna pista, "
        "responde null, no asumas.\n"
        "Para city y exact_address: el nombre de la cuenta o institución que publica NO es "
        "evidencia suficiente de dónde ocurre el evento (ej. una cuenta llamada \"Alianza "
        "Francesa de Medellín\" no implica que el evento sea en Medellín si el caption no lo "
        "dice explícitamente) — usa exclusivamente lo que el texto del caption confirma. Si el "
        "caption no da ninguna pista clara de ciudad o dirección, responde null en ambos "
        "campos; es preferible null a una ubicación adivinada.\n"
        "IMPORTANTE — idioma: title y clean_description deben estar en ESPAÑOL, sin importar "
        "el idioma del caption original (aunque esté en francés o inglés) — el público de este "
        "hub es la diáspora colombiana/latinoamericana en Francia. Excepción: mantén sin "
        "traducir los nombres propios (lugares, instituciones, títulos de eventos) tal como "
        "aparecen en el caption.\n\n"
        f"{_LLM_SCHEMA_HINT}"
    )


# ── 6b-i. Transporte Ollama (local, activo por defecto) ──────────────────────
def _ollama_request(caption: str, anchor_date: str, label: str = "") -> Optional[dict]:
    """Una llamada secuencial a Ollama local. Sin cuota que pacear — a diferencia
    de Groq, no hay throttling/backoff aquí, solo el tiempo de cómputo de la
    máquina. None si Ollama no está disponible o la respuesta falla/no parsea.
    """
    prompt = _build_llm_prompt(caption, anchor_date)
    try:
        resp = requests.post(
            OLLAMA_ENDPOINT,
            json={
                "model":    OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "format":   "json",
                "stream":   False,
                "options":  {"temperature": 0.0},
            },
            timeout=120,
        )
        if resp.status_code == 404 and "not found" in resp.text.lower():
            print(f"  ⚠️  Ollama: modelo {OLLAMA_MODEL} no está descargado [{label}] — "
                  f"correr `ollama pull {OLLAMA_MODEL}`", flush=True)
            return None
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)
    except requests.exceptions.ConnectionError:
        print(f"  ⚠️  Ollama no disponible en localhost:11434 [{label}] — "
              f"¿está corriendo `ollama serve` y el modelo {OLLAMA_MODEL} descargado?", flush=True)
        return None
    except Exception as e:
        print(f"  ⚠️  Ollama falló [{label}]: {type(e).__name__}: {e}", flush=True)
        return None


# ── 6b-ii. Transporte Groq (disponible vía LLM_PROVIDER=groq) ────────────────
# Free tier de Groq: 30 req/min Y 12,000 tokens/min (TPM), además de un tope
# de 100,000 tokens/día que no se ve en los headers por-minuto — ver DD-033
# (update 2 y 3). Se deja el throttling implementado como referencia/fallback,
# pero Ollama es la ruta activa por defecto para volumen alto.
GROQ_MAX_RPM             = 25
GROQ_MAX_TPM             = 12000
GROQ_TPM_SAFETY_MARGIN   = 11000  # margen bajo el límite real de 12000
GROQ_MAX_ATTEMPTS        = 3
GROQ_RESPONSE_TOKENS_EST = 150    # el JSON de salida es corto y de forma fija
_last_groq_call_ts       = 0.0
_groq_token_window: list = []     # [(timestamp, tokens_estimados), ...] últimos 60s


def _groq_rate_limit_wait() -> None:
    """Espacia llamadas para no superar GROQ_MAX_RPM, antes de cada intento."""
    global _last_groq_call_ts
    min_interval = 60.0 / GROQ_MAX_RPM
    elapsed = time.time() - _last_groq_call_ts
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_groq_call_ts = time.time()


def _estimate_tokens(text: str) -> int:
    """Heurística simple: ~4 caracteres por token."""
    return max(1, len(text) // 4)


def _groq_tokens_used_last_60s() -> int:
    global _groq_token_window
    cutoff = time.time() - 60
    _groq_token_window = [(ts, tok) for ts, tok in _groq_token_window if ts >= cutoff]
    return sum(tok for _, tok in _groq_token_window)


def _groq_tpm_wait(estimated_tokens: int) -> int:
    """Bloquea hasta que quepan estimated_tokens en la ventana de 60s bajo
    GROQ_TPM_SAFETY_MARGIN, en vez de disparar la llamada y esperar el 429.
    Devuelve los tokens ya usados en la ventana (para logging)."""
    while True:
        used = _groq_tokens_used_last_60s()
        if used + estimated_tokens <= GROQ_TPM_SAFETY_MARGIN or not _groq_token_window:
            return used
        oldest_ts = _groq_token_window[0][0]
        wait = max(0.5, 60 - (time.time() - oldest_ts) + 0.1)
        print(f"  ⏳ Groq TPM: {used}+{estimated_tokens} tok > {GROQ_TPM_SAFETY_MARGIN} margen"
              f" — esperando {wait:.1f}s", flush=True)
        time.sleep(wait)


def _groq_request(caption: str, anchor_date: str, label: str = "") -> Optional[dict]:
    """Llama a Groq con reintentos. None si falla tras agotar GROQ_MAX_ATTEMPTS.

    Loguea tipo de excepción/status en cada intento fallido para poder
    distinguir rate-limit (429) de timeout o de JSON inválido en la
    respuesta — antes fallaba en silencio y no había forma de saber por qué.
    Pacea por RPM y por TPM (ver DD-033 update 2); el manejo de 429 se
    mantiene como red de seguridad si el estimado de tokens falla, pero no
    debería activarse casi nunca con el pacing por TPM en su lugar.
    """
    if not GROQ_API_KEY:
        return None
    prompt = _build_llm_prompt(caption, anchor_date)
    est_tokens = _estimate_tokens(prompt) + GROQ_RESPONSE_TOKENS_EST

    for attempt in range(1, GROQ_MAX_ATTEMPTS + 1):
        _groq_rate_limit_wait()
        used = _groq_tpm_wait(est_tokens)
        _groq_token_window.append((time.time(), est_tokens))
        try:
            resp = requests.post(
                GROQ_ENDPOINT,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model":           GROQ_MODEL,
                    "messages":        [{"role": "user", "content": prompt}],
                    "temperature":     0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  ⚠️  Groq 429 rate-limit [{label}] intento {attempt}/{GROQ_MAX_ATTEMPTS}"
                      f" (~{est_tokens} tok, ventana={used}) — esperando {wait:.1f}s", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            print(f"  🪙 Groq [{label}] ~{est_tokens} tok  (ventana 60s: {used + est_tokens})", flush=True)
            return json.loads(content)
        except Exception as e:
            print(f"  ⚠️  Groq falló [{label}] intento {attempt}/{GROQ_MAX_ATTEMPTS}: "
                  f"{type(e).__name__}: {e}", flush=True)
            if attempt < GROQ_MAX_ATTEMPTS:
                time.sleep(1.5)
    return None


# ── 6b-iii. Transporte Cerebras (disponible vía LLM_PROVIDER=cerebras) ───────
# Free tier de gpt-oss-120b en Cerebras (confirmado en
# inference-docs.cerebras.ai/support/rate-limits el 2026-07-30, tabla "Free
# Trial"): 5 req/min, 30,000 tokens/min (TPM), 1,000,000 tokens/día (TPD) —
# el TPD sigue siendo ~10x el de Groq, pero el RPM es MÁS bajo que Groq (5
# vs 25), así que cada llamada individual va más espaciada aunque el cupo
# diario total sea mayor. Modelo distinto a Groq (gpt-oss-120b, no
# llama-3.3-70b) — pendiente de validar output contra el baseline de calidad
# ya medido con Groq (ver verify_events_extraction.py show_groq_quality_sample)
# antes de confiar en esto para escritura real.
CEREBRAS_MAX_RPM             = 4
CEREBRAS_MAX_TPM             = 30000
CEREBRAS_TPM_SAFETY_MARGIN   = 26000   # margen bajo el límite real de 30,000
CEREBRAS_MAX_ATTEMPTS        = 3
CEREBRAS_RESPONSE_TOKENS_EST = 150     # mismo JSON de salida, corto y de forma fija
_last_cerebras_call_ts       = 0.0
_cerebras_token_window: list = []      # [(timestamp, tokens_estimados), ...] últimos 60s


def _cerebras_rate_limit_wait() -> None:
    """Espacia llamadas para no superar CEREBRAS_MAX_RPM, antes de cada intento."""
    global _last_cerebras_call_ts
    min_interval = 60.0 / CEREBRAS_MAX_RPM
    elapsed = time.time() - _last_cerebras_call_ts
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_cerebras_call_ts = time.time()


def _cerebras_tokens_used_last_60s() -> int:
    global _cerebras_token_window
    cutoff = time.time() - 60
    _cerebras_token_window = [(ts, tok) for ts, tok in _cerebras_token_window if ts >= cutoff]
    return sum(tok for _, tok in _cerebras_token_window)


def _cerebras_tpm_wait(estimated_tokens: int) -> int:
    """Bloquea hasta que quepan estimated_tokens en la ventana de 60s bajo
    CEREBRAS_TPM_SAFETY_MARGIN, en vez de disparar la llamada y esperar el 429."""
    while True:
        used = _cerebras_tokens_used_last_60s()
        if used + estimated_tokens <= CEREBRAS_TPM_SAFETY_MARGIN or not _cerebras_token_window:
            return used
        oldest_ts = _cerebras_token_window[0][0]
        wait = max(0.5, 60 - (time.time() - oldest_ts) + 0.1)
        print(f"  ⏳ Cerebras TPM: {used}+{estimated_tokens} tok > {CEREBRAS_TPM_SAFETY_MARGIN} margen"
              f" — esperando {wait:.1f}s", flush=True)
        time.sleep(wait)


def _cerebras_request(caption: str, anchor_date: str, label: str = "") -> Optional[dict]:
    """Llama a Cerebras con reintentos. None si falla tras agotar
    CEREBRAS_MAX_ATTEMPTS. Mismo patrón que _groq_request (endpoint
    OpenAI-compatible, mismo prompt/esquema) — ver DD-033 update 5.
    """
    if not CEREBRAS_API_KEY:
        return None
    prompt = _build_llm_prompt(caption, anchor_date)
    est_tokens = _estimate_tokens(prompt) + CEREBRAS_RESPONSE_TOKENS_EST

    for attempt in range(1, CEREBRAS_MAX_ATTEMPTS + 1):
        _cerebras_rate_limit_wait()
        used = _cerebras_tpm_wait(est_tokens)
        _cerebras_token_window.append((time.time(), est_tokens))
        try:
            resp = requests.post(
                CEREBRAS_ENDPOINT,
                headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model":           CEREBRAS_MODEL,
                    "messages":        [{"role": "user", "content": prompt}],
                    "temperature":     0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  ⚠️  Cerebras 429 rate-limit [{label}] intento {attempt}/{CEREBRAS_MAX_ATTEMPTS}"
                      f" (~{est_tokens} tok, ventana={used}) — esperando {wait:.1f}s", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            print(f"  🧠 Cerebras [{label}] ~{est_tokens} tok  (ventana 60s: {used + est_tokens})", flush=True)
            return json.loads(content)
        except Exception as e:
            print(f"  ⚠️  Cerebras falló [{label}] intento {attempt}/{CEREBRAS_MAX_ATTEMPTS}: "
                  f"{type(e).__name__}: {e}", flush=True)
            if attempt < CEREBRAS_MAX_ATTEMPTS:
                time.sleep(1.5)
    return None


_LLM_DEFAULTS = {
    "is_public_invitation": None,
    "is_upcoming":          None,
    "city":                 None,
    "exact_address":        None,
    "clean_date":           None,
    "clean_description":    None,
    "title":                None,
    "is_free":              None,
    "reasoning":            None,
}


def _extract_llm_fields(data: Optional[dict]) -> dict:
    if data is None:
        return dict(_LLM_DEFAULTS)
    return {
        "is_public_invitation": data.get("is_public_invitation"),
        "is_upcoming":          data.get("is_upcoming"),
        "city":                 data.get("city") or None,
        "exact_address":        data.get("exact_address") or None,
        "clean_date":           data.get("clean_date") or None,
        "clean_description":    data.get("clean_description") or None,
        "title":                data.get("title") or None,
        "is_free":              data.get("is_free"),
        "reasoning":            data.get("reasoning") or None,
    }


def llm_enrich_event_ollama(caption: str, post_timestamp: str = "", label: str = "") -> dict:
    """Capa 3 vía Ollama local (modelo configurable vía OLLAMA_MODEL, default
    qwen2.5:7b) — mismo prompt/esquema que la versión Groq (_build_llm_prompt),
    solo cambia el transporte. Sin cuota que pacear: llamada secuencial, ver
    _ollama_request.
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_ollama_request(caption, anchor_date, label=label))


def llm_enrich_event_groq(caption: str, post_timestamp: str = "", label: str = "") -> dict:
    """Capa 3 vía Groq (llama-3.3-70b-versatile). Throttling RPM/TPM y
    reintentos en _groq_request — ver DD-033 (update 2).
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_groq_request(caption, anchor_date, label=label))


def llm_enrich_event_cerebras(caption: str, post_timestamp: str = "", label: str = "") -> dict:
    """Capa 3 vía Cerebras (llama-3.3-70b, mismo modelo que Groq). Throttling
    RPM/TPM y reintentos en _cerebras_request — ver DD-033 (update 5).
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_cerebras_request(caption, anchor_date, label=label))


# ── 6b-iv. Fallback automático entre proveedores cloud (DD-033 update 7) ────
# Cuando el proveedor preferido (LLM_PROVIDER) agota su cupo diario a mitad
# de una corrida, en vez de solo esperar/detenerse, se cambia automáticamente
# al otro proveedor cloud (Groq <-> Cerebras) para el resto de la corrida —
# así se aprovecha el cupo combinado de ambos sin que el usuario tenga que
# pararla y reiniciarla manualmente cambiando LLM_PROVIDER a mano (como se
# hizo antes de este fix). Ollama queda fuera: no tiene cupo diario que se
# agote, y mezclar local+nube automáticamente no es lo que se pidió.
_CLOUD_PROVIDERS = {
    "groq":     llm_enrich_event_groq,
    "cerebras": llm_enrich_event_cerebras,
}
_provider_failed_this_run: set = set()


def _llm_call_failed(result: dict) -> bool:
    """Mismo criterio que ya usa el caller para LLM_UNKNOWN_PENALTY: si
    is_public_invitation e is_upcoming vinieron ambos en None, el transporte
    no devolvió nada usable (agotó sus reintentos internos) — señal de que
    vale la pena probar el otro proveedor en vez de seguir insistiendo."""
    return result.get("is_public_invitation") is None and result.get("is_upcoming") is None


def llm_enrich_event(caption: str, post_timestamp: str = "", label: str = "") -> dict:
    """Capa 3 — limpia fecha/ubicación, redacta descripción y detecta noticias
    institucionales sin invitación real al público. LLM_PROVIDER elige el
    transporte preferido: llm_enrich_event_ollama() (default), o uno de los
    dos proveedores cloud (llm_enrich_event_groq()/llm_enrich_event_cerebras()).

    Si LLM_PROVIDER es un proveedor cloud y falla (cupo agotado u otro error
    tras sus reintentos internos), se reintenta automáticamente con el otro
    proveedor cloud antes de rendirse — ver DD-033 (update 7). Un proveedor
    que falla se marca para el resto de esta corrida (no se reintenta post
    a post; los cupos diarios no se recuperan a mitad de una corrida).

    Se llama SOLO sobre candidatos que ya pasaron Capas 1+2 (~30-50/corrida en
    pruebas, corpus completo en corridas reales). Si TODOS los transportes
    disponibles fallan se devuelven valores null — el caller aplica
    LLM_UNKNOWN_PENALTY (penalización intermedia) en ese caso, ver DD-033.
    """
    if LLM_PROVIDER not in _CLOUD_PROVIDERS:
        return llm_enrich_event_ollama(caption, post_timestamp, label=label)

    order = [LLM_PROVIDER] + [p for p in _CLOUD_PROVIDERS if p != LLM_PROVIDER]
    result = dict(_LLM_DEFAULTS)
    for provider in order:
        if provider in _provider_failed_this_run:
            continue
        result = _CLOUD_PROVIDERS[provider](caption, post_timestamp, label=label)
        if not _llm_call_failed(result):
            return result
        print(f"  🔀 {provider} agotado/fallando — cambiando de proveedor "
              f"para el resto de esta corrida.", flush=True)
        _provider_failed_this_run.add(provider)
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
    }
    langs = ["es", "fr", "en"]

    # Primero intenta sobre los fragmentos extraídos por regex (más preciso)
    for match in _DATE_RE.finditer(text[:600]):
        snippet = match.group(0).strip()
        if len(snippet) < 3:
            continue
        parsed = dateparser.parse(snippet, languages=langs, settings=settings)
        if parsed:
            return parsed.strftime("%Y-%m-%d")

    # Fallback: busca fechas en el texto completo
    try:
        from dateparser.search import search_dates
        results = search_dates(text[:600], languages=langs, settings=settings)
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
                e.cityName     = $cityName,
                e.exactAddress = $exactAddress,
                e.hotnessScore = $hotnessScore,
                e.eventScore   = $eventScore,
                e.confidence   = $confidence,
                e.layer1Score  = $layer1Score,
                e.postCount    = 1,
                e.embedding    = $embedding,
                e.createdAt    = $createdAt,
                e.description        = $description,
                e.isPublicInvitation = $isPublicInvitation,
                e.isUpcoming         = $isUpcoming,
                e.isFree             = $isFree,
                e.llmReasoning       = $llmReasoning,
                e.sourcePostUrl      = $sourcePostUrl,
                e.sourceAuthor       = $sourceAuthor,
                e.sourcePostDate     = $sourcePostDate
        """, **{k: event[k] for k in [
            "id", "title", "type", "category", "rawDate", "eventDate",
            "locationName", "cityName", "exactAddress", "hotnessScore", "eventScore", "confidence",
            "layer1Score", "embedding", "createdAt",
            "description", "isPublicInvitation", "isUpcoming", "isFree", "llmReasoning",
            "sourcePostUrl", "sourceAuthor", "sourcePostDate",
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
    batch_size: int         = 32,
    date_window: int        = 3,
    sim_threshold: float    = 0.82,
    dry_run: bool           = False,
    accounts: list[str]     = None,
    high_quality: bool      = False,
):
    t_start = time.time()
    print("\n🎭 Fase 4-B — Extracción de Eventos (4 capas)")
    print("=" * 60)
    if LLM_PROVIDER == "groq":
        capa3_status = f"Groq({GROQ_MODEL})" if GROQ_API_KEY else "Groq(GROQ_API_KEY ausente!)"
    elif LLM_PROVIDER == "cerebras":
        capa3_status = f"Cerebras({CEREBRAS_MODEL})" if CEREBRAS_API_KEY else "Cerebras(CEREBRAS_API_KEY ausente!)"
    else:
        capa3_status = f"Ollama({OLLAMA_MODEL}@localhost:11434)"
    print(f"  L1≥{layer1_threshold}  L2≥{layer2_threshold}  "
          f"max_posts={max_posts or '∞'}  batch={batch_size}  "
          f"sim≥{sim_threshold}  date±{date_window}d  "
          f"Capa3={capa3_status}")
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
                   p.url           AS url,
                   a.username      AS author,
                   collect(DISTINCT [(p)-[:TAGS_USER]->(tu) | tu.username])[0] AS taggedUsers,
                   collect(DISTINCT [(p)-[:MENTIONS]->(m)   | m.username])[0]  AS mentions,
                   [(p)-[:TAGGED_AT]->(loc:Location) | loc.name][0]            AS taggedLocation
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

    HYP_TMPL = "Esta publicación anuncia {}."

    with driver.session() as session:
        cache = load_events_cache(session)

    created       = 0
    enriched      = 0
    skipped_l1    = rejected_l1
    skipped_l2    = 0
    dates_clamped = 0
    cand_set      = set(candidates_idx)   # una sola vez, no dentro del loop
    processed_ids = [posts[i]["id"] for i in range(len(posts)) if i not in cand_set]
    dry_counts:   dict = defaultdict(int)
    diag_all:     list = []   # todos los posts — para distribución L1
    diag_cands:   list = []   # solo candidatos L1 — para distribución L2 y ejemplos

    # Registrar los rechazados de Capa 1 en diag_all
    for i, post in enumerate(posts):
        if i not in cand_set:
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

    # Candidatos de Capa 1
    cand_posts    = [posts[i] for i in candidates_idx]
    cand_embs     = [all_embs[i] for i in candidates_idx]
    cand_l1       = [layer1_scores[i] for i in candidates_idx]
    cand_captions = [p["caption"][:1200] for p in cand_posts]  # el tokenizer trunca a MAX_NLI_TOKENS

    # ── Capa 2a — detección binaria BATCHEADA (modelo ligero) ────────────────
    print(f"\n  🟠 Capa 2a — detección binaria batcheada ({DET_MODEL})...")
    t0 = time.time()
    det_scores     = detect_events_batch(cand_captions, batch_size=batch_size)
    is_event_flags = [s >= layer2_threshold for s in det_scores]
    pos_idx        = [j for j, f in enumerate(is_event_flags) if f]
    print(f"  ✅ Capa 2a: {len(pos_idx)} positivos / {len(cand_posts)} candidatos"
          f"  ({time.time() - t0:.1f}s, batch={batch_size})")

    # ── Capa 2b — tipificación multi-label SOLO sobre positivos ──────────────
    type_map: dict = {}
    if pos_idx:
        type_model_name = TYPE_MODEL if high_quality else DET_MODEL
        print(f"  🟣 Capa 2b — tipificación ({type_model_name}) sobre {len(pos_idx)} posts...")
        t0 = time.time()
        from transformers import pipeline as hf_pipeline
        type_clf = hf_pipeline("zero-shot-classification", model=type_model_name, device=-1)
        typ_list = type_clf(
            [cand_captions[j] for j in pos_idx],
            EVENT_LABELS_CULTURAL,
            hypothesis_template=HYP_TMPL,
            multi_label=True,
            batch_size=8,          # 12 labels × B pares por forward
            truncation=True,
        )
        if isinstance(typ_list, dict):
            typ_list = [typ_list]
        type_map = dict(zip(pos_idx, typ_list))
        print(f"  ✅ Capa 2b lista  ({time.time() - t0:.1f}s)")

    # ── Persistencia + NER (solo eventos, o todo en dry-run) ─────────────────
    since_last_mark = 0
    for j in tqdm(range(len(cand_posts)), desc="  Eventos"):
        post, emb, l1 = cand_posts[j], cand_embs[j], cand_l1[j]
        det_score = det_scores[j]
        is_event  = is_event_flags[j]

        # Tipo desde Capa 2b
        typ        = type_map.get(j)
        top_label  = typ["labels"][0]  if typ else EVENT_LABELS_CULTURAL[0]
        type_score = typ["scores"][0] if typ else det_score
        top3       = list(zip(typ["labels"][:3], typ["scores"][:3])) if typ else []

        category = _CAT_MAP.get(top_label, "nulo") if is_event else "nulo"
        penalty  = _PEN_MAP.get(top_label, 0.0)    if is_event else 0.0

        # NER + fechas solo cuando hace falta (eventos, o todo en dry-run
        # para diagnóstico) — evita pasar spaCy/langdetect por cada candidato.
        # NOTA (DD-033 update 6): loc_name YA NO sale de ner["locations"][0].
        # Ese fallback tomaba la primera entidad LOC/GPE/FAC que spaCy
        # encontrara en cualquier parte del caption sin verificar relevancia
        # — producía ubicaciones erróneas con frecuencia (ej. "Pisa" para un
        # evento en París, solo porque esa palabra apareció en el texto).
        # La ubicación ahora sale EXCLUSIVAMENTE de Capa 3 (city/exact_address,
        # más abajo) — si Capa 3 no corre o no la da, queda null en vez de
        # adivinada. ner["locations"] ya no se usa para nada.
        loc_name = org_name = event_date = None
        if is_event or dry_run:
            lang       = detect_text_lang(post["caption"])
            ner        = extract_ner(post["caption"], lang)
            org_name   = ner["orgs"][0] if ner["orgs"] else None
            # FIX 2: fechas ancladas al timestamp del post, no a datetime.now()
            event_date = extract_dates(post["caption"], post.get("timestamp", "") or "")

        hotness     = compute_hotness(
            post.get("likes", 0) or 0,
            post.get("comments", 0) or 0,
            post.get("timestamp", "") or "",
        )

        # Capa 3 — Groq, solo sobre candidatos que ya pasaron Capas 1+2 (is_event=True).
        is_public_invitation = is_upcoming = clean_description = llm_reasoning = None
        llm_title = llm_is_free = None
        llm_city = llm_exact_address = None
        llm_penalty = 1.0
        if is_event:
            llm_out = llm_enrich_event(
                post["caption"], post.get("timestamp", "") or "",
                label=f"@{post.get('author', '?')}/{post.get('id', '?')}",
            )
            llm_city          = llm_out.get("city")
            # Si el caption no menciona dirección explícita, caemos al geotag
            # propio de Instagram en el post (Post -[:TAGGED_AT]-> Location)
            # antes de dejarla en null — es una señal independiente y más
            # confiable que texto libre cuando existe (el usuario la marcó
            # directamente al publicar, no requiere inferencia).
            llm_exact_address = llm_out.get("exact_address") or post.get("taggedLocation")
            # locationName preferido: dirección exacta > ciudad > null.
            # Ambos vienen del mismo prompt reforzado que evita inferir la
            # ciudad solo del nombre de la cuenta/institución (DD-033 update 6).
            loc_name = llm_exact_address or llm_city or None
            if llm_out.get("clean_date"):
                event_date = llm_out["clean_date"]
                try:
                    ed = datetime.fromisoformat(event_date.replace("Z", "+00:00")).replace(tzinfo=None)
                    pd = datetime.fromisoformat(
                        (post.get("timestamp", "") or "").replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    if abs((ed - pd).days) > EVENT_DATE_CLAMP_DAYS:
                        event_date = None
                        dates_clamped += 1
                except (ValueError, TypeError):
                    pass
            is_public_invitation = llm_out.get("is_public_invitation")
            is_upcoming          = llm_out.get("is_upcoming")
            clean_description    = llm_out.get("clean_description")
            llm_reasoning         = llm_out.get("reasoning")
            llm_title            = llm_out.get("title")
            llm_is_free          = llm_out.get("is_free")
            if is_public_invitation is None or is_upcoming is None:
                # Groq falló tras agotar reintentos — verdicto incierto, no
                # confianza ciega (DD-033-update): penalización intermedia.
                llm_penalty = LLM_UNKNOWN_PENALTY
            else:
                llm_penalty = 1.0 if (is_public_invitation and is_upcoming) else LLM_REJECT_PENALTY

        event_score = compute_event_score(det_score, hotness, penalty * llm_penalty) if is_event else 0.0

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
            "is_public_invitation": is_public_invitation,
            "is_upcoming":          is_upcoming,
            "clean_description":    clean_description or "",
            "title":                llm_title or "",
            "is_free":              llm_is_free,
            "city":                 llm_city or "",
            "exact_address":        llm_exact_address or "",
        }
        diag_all.append(record)
        diag_cands.append(record)

        if is_event:
            dry_counts[category] += 1

        if not is_event:
            skipped_l2 += 1
            processed_ids.append(post["id"])
            since_last_mark += 1
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
            # Título editorial del LLM si existe; si no (Groq falló o el
            # post no llegó a Capa 3), cae al nombre de categoría como antes.
            "title":        llm_title or top_label.title(),
            "type":         top_label,
            "category":     category,
            "rawDate":      event_date or "",
            "eventDate":    event_date or "",
            "locationName": loc_name or "",
            "cityName":     llm_city,
            "exactAddress": llm_exact_address,
            "hotnessScore": hotness,
            "eventScore":   event_score,
            "confidence":   round(det_score, 4),
            "layer1Score":  round(l1, 4),
            "embedding":    event_emb,
            "organizerOrg": org_name,
            "createdAt":    datetime.now(timezone.utc).isoformat(),
            # Capa 3 (Groq) — description/flags/reasoning; source* solo se
            # fijan al crear el evento, nunca se sobreescriben al fusionar
            # (representan la publicación ORIGINAL, ver DD-033).
            "description":        clean_description or "",
            "isPublicInvitation":  is_public_invitation,
            "isUpcoming":          is_upcoming,
            "isFree":              llm_is_free,
            "llmReasoning":        llm_reasoning or "",
            "sourcePostUrl":       post.get("url"),
            "sourceAuthor":        post.get("author"),
            "sourcePostDate":      post.get("timestamp"),
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
        since_last_mark += 1

        # Marcado incremental cada ~100 posts (resumibilidad si el proceso muere)
        if not dry_run and since_last_mark >= 100:
            neo4j_run_with_retry("""
                UNWIND $ids AS pid
                MATCH (p:Post {id: pid})
                SET p.eventExtracted = true
            """, {"ids": processed_ids[-since_last_mark:]})
            since_last_mark = 0

    if not dry_run and processed_ids:
        neo4j_run_with_retry("""
            UNWIND $ids AS pid
            MATCH (p:Post {id: pid})
            SET p.eventExtracted = true
        """, {"ids": processed_ids})

    # ── Resumen ───────────────────────────────────────────────────────────────
    detected = [r for r in diag_cands if r["decision"] == "EVENTO"]
    print(f"\n{'═'*60}")
    print(f"  ⏱️  Tiempo total        : {time.time() - t_start:.1f}s")
    print(f"  ✅ Posts procesados    : {len(posts)}")
    print(f"  🔵 Descartados Capa 1  : {skipped_l1}  (L1<{layer1_threshold})")
    print(f"  🟠 Candidatos Capa 2   : {len(cand_posts)}")
    print(f"  ⏭️  Descartados Capa 2  : {skipped_l2}")
    print(f"  🗓️  Fechas clampeadas   : {dates_clamped}  (>{EVENT_DATE_CLAMP_DAYS}d del post)")
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
        print(f"       LLM (Capa 3): is_public_invitation={r['is_public_invitation']}  "
              f"is_upcoming={r['is_upcoming']}")
        if r["clean_description"]:
            print(f"       Descripción: {r['clean_description']}")
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
        32, "--batch-size",
        help="Tamaño de lote para la inferencia NLI de Capa 2a.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Clasificar y mostrar diagnóstico completo sin escribir en Neo4j.",
    ),
    accounts: Optional[str] = typer.Option(
        None, "--accounts",
        help="Cuentas a procesar separadas por coma, e.g. dichaparis,ivan_argote. Sin filtro = todas.",
    ),
    high_quality: bool = typer.Option(
        False, "--high-quality",
        help="Usar mDeBERTa-v3-base en Capa 2b. Más preciso, más lento. Por defecto se usa MiniLMv2-L6.",
    ),
):
    """
    Fase 4-B: extracción de eventos en 4 capas.

    Capa 1: sentence-transformers filtra candidatos por similitud coseno
    máxima contra 100 frases de referencia (--threshold).

    Capa 2a: NLI multilingüe ligero batcheado — detección binaria (--layer2-threshold).
    Capa 2b: MiniLMv2-L6 multi-label — tipificación solo sobre positivos.
             Usar --high-quality para activar mDeBERTa-v3-base (más lento).
    Capa 3:  Ollama local (modelo configurable vía OLLAMA_MODEL, default
             qwen2.5:7b) por defecto — o Groq si LLM_PROVIDER=groq
             (vía GROQ_API_KEY en .env), o Cerebras si LLM_PROVIDER=cerebras
             (vía CEREBRAS_API_KEY en .env). Limpia fecha/ubicación, redacta
             descripción y filtra noticias institucionales sin invitación
             real. Solo corre sobre los positivos de 2a.

    eventScore = (layer2_score × 0.6 + hotness_norm × 0.4) × political_penalty × llm_penalty
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
        high_quality=high_quality,
    )

    driver.close()
    print("\n✅ Extracción de eventos completa.")


if __name__ == "__main__":
    app()
