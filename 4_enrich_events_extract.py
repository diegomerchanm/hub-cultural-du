"""
Fase 4-B — Extracción de eventos culturales desde Post.caption.

Arquitectura de 3 capas (la vieja Capa 2b de tipificación por embeddings se
eliminó en 2026-08 — ver eval de 100 posts en data_processed/eval_100_report.md:
el LLM de Capa 3 tipifica mejor y sin el sesgo de las referencias/hipótesis
NLI, así que se le sumó ese trabajo a la llamada que ya se hacía):

  Capa 1 — sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
            Similitud coseno MÁXIMA contra ~100 frases de referencia (no promedio).
            Filtra candidatos por max_sim >= layer1_threshold.

  Capa 2a — Detección binaria (multilingual-MiniLMv2-L6-mnli-xnli, ligero)
             NLI batcheado con una sola hipótesis → P(entailment).
             ¿Es un evento con fecha/lugar? Descarta definitivamente si no.
             La hipótesis se elige en el idioma detectado del caption
             (DET_HYPOTHESES es/en/fr) — antes estaba fija en español y
             penalizaba captions en otros idiomas más por el desajuste de
             idioma que por el contenido (confirmado en la eval de 100).
             NOTA: se usa el MiniLMv2 multilingüe y NO cross-encoder/
             nli-deberta-v3-small porque este último es monolingüe inglés
             (SNLI/MNLI) y fallaría con captions es/fr.

  Capa 3 — LLM: Ollama local (modelo configurable vía OLLAMA_MODEL, default
            qwen2.5:7b) por defecto, Groq disponible vía LLM_PROVIDER=groq
            (ver DD-033 y DD-033 update 3), Cerebras vía LLM_PROVIDER=cerebras
            (ver DD-033 update 5 — mismo modelo llama-3.3-70b, ~10x cupo diario
            gratis frente a Groq, endpoint OpenAI-compatible), y desde
            DD-033 update 8 también Google/Gemini (LLM_PROVIDER=google,
            gemini-2.5-flash-lite, tier gratis) y DeepSeek (LLM_PROVIDER=
            deepseek, deepseek-v4-flash — sin tier gratis, pago por token
            pero el más barato del mercado). El fallback automático entre
            proveedores cloud cuando uno se agota sigue el orden
            groq → google → deepseek → cerebras (ver _CLOUD_PROVIDERS). Solo corre
            sobre los que pasaron 2a (~30-50/corrida en pruebas, corpus
            completo en corridas reales — Ollama no tiene tope diario de
            tokens como el free tier de Groq/Cerebras). Limpia fecha/ubicación
            (spaCy/dateparser tienen bugs confirmados), TIPIFICA el evento
            (campo "type", misma taxonomía de 16 labels que antes tenía
            Capa 2b), da price_range, redacta clean_description, y detecta
            noticias institucionales sin invitación real al público mediante
            is_public_invitation/is_upcoming — estos dos ya no solo penalizan
            el score: si el LLM corrió y dice que NO es invitación pública
            futura, el nodo :Event NO SE CREA (antes se creaba igual con
            score bajo — ver eval de 100: eso explicaba 29/35 de los falsos
            positivos). `reasoning` solo se pide en --dry-run (fase de
            prueba); en producción no tiene consumidor y cuesta tokens.

Dedup por post_id antes de llamar Capa 3: cuando el mismo post está
co-publicado por varias cuentas, la query de carga trae una fila por cada
(Account,Post) — sin dedup, Capa 3 se llamaría dos veces por el mismo texto
exacto (confirmado: 3 pares duplicados en la eval de 100 posts).

Optimizaciones CPU: batch inference en 2a, truncado a 256 tokens,
torch multi-thread, cache de embeddings de referencia en ref_embeddings.npz.

Score final = (layer2_score × 0.6 + hotness_norm × 0.4) × category_penalty × llm_penalty
  llm_penalty = 1.0 si is_public_invitation AND is_upcoming
              = 0.15 (LLM_REJECT_PENALTY)  si el LLM responde y NO es invitación futura
              = 0.5  (LLM_UNKNOWN_PENALTY) si el LLM falla (Ollama no disponible,
                o Groq tras agotar reintentos) — verdicto "incierto", ver DD-033-update.

Idempotente: marca cada post procesado con eventExtracted=true.
"""

import csv
import hashlib
import json
import math
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
GOOGLE_API_KEY   = os.getenv("GOOGLE_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

if not GROQ_API_KEY:
    print("  ⚠️  GROQ_API_KEY ausente en .env — Capa 3 (LLM) se omitirá (valores null, sin penalización)")

# Endpoint OpenAI-compatible de Groq. llama-3.3-70b-versatile fue decomisionado
# por Groq el 2026-08-16 (aviso por email); reemplazo oficial recomendado por
# Groq: GPT OSS 120B, id confirmado en console.groq.com/docs/models (2026-08-15)
# como "openai/gpt-oss-120b" (con prefijo openai/, distinto del id que usa
# Cerebras para el mismo peso abierto — ver CEREBRAS_MODEL debajo). Mismo
# modelo subyacente que ya corre en el fallback de Cerebras, así que primario
# y fallback quedan alineados — sin necesidad de re-validar calidad desde cero.
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "openai/gpt-oss-120b"

# Endpoint OpenAI-compatible de Gemini (ai.google.dev/gemini-api/docs/openai) —
# se usa esta capa de compatibilidad en vez de la API nativa de Gemini
# (generateContent) para reutilizar el mismo shape de request/response que
# Groq/Cerebras (messages + response_format json_object) sin duplicar lógica.
# Modelo: gemini-3.5-flash-lite. Originalmente se eligió gemini-2.5-flash-lite
# por ser, dentro del tier gratis (sin tarjeta), el de más cupo diario de los
# tres modelos gratis de Gemini (confirmado en ai.google.dev/gemini-api/docs/
# rate-limits el 2026-08-21). Cambiado a 3.5 el 2026-08-24 tras un 404 real en
# producción: Google descontinuó 2.5-flash-lite para cuentas nuevas
# ("This model models/gemini-2.5-flash-lite is no longer available to new
# users... use models/gemini-3.5-flash-lite", error NOT_FOUND confirmado con
# la propia API key de Diego). No se re-verificaron los límites de cupo/RPM
# de 3.5-flash-lite contra el dashboard — los de abajo siguen siendo los
# aproximados heredados de 2.5, pendiente de confirmar si difieren. Los
# números exactos de RPM/TPM/RPD por tier viven en un dashboard interactivo
# — aistudio.google.com/rate-limit — no en una tabla estática. Igual que
# Groq/Cerebras: gratis con throttling, no pago por token.
GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GOOGLE_MODEL    = "gemini-3.5-flash-lite"

# Endpoint OpenAI-compatible de DeepSeek (api-docs.deepseek.com). A diferencia
# de Groq/Google/Cerebras, DeepSeek NO tiene tier gratis — es pago por token,
# aunque el más barato del mercado por bastante margen (confirmado en
# api-docs.deepseek.com/quick_start/pricing el 2026-08-21: deepseek-v4-flash,
# ~$0.22-0.44/M tokens de entrada y $0.66-1.32/M de salida según horario
# peak/off-peak). Tampoco publica RPM/TPM — su límite es de concurrencia
# (2500 conexiones simultáneas para v4-flash, ver quick_start/rate_limit),
# irrelevante para este pipeline que llama secuencialmente. Por eso NO tiene
# throttling propio como Groq/Cerebras/Google más abajo — solo reintento en
# 429/error igual que los demás. "Se acaba" en el sentido de que factura, no
# de que tenga un cupo diario gratis que se agote (a diferencia de los otros
# tres) — tenerlo en cuenta si algún día se le pone un tope de gasto.
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL    = "deepseek-v4-flash"

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
    # ── INSTITUCIONAL FORMAL / ACADÉMICO SOBRIO (DD-036) ─────────────────────
    # Registro de comunicado de prensa/institucional, sin llamado directo a
    # "únete/ven" — el resto de las secciones arriba está mayoritariamente en
    # tono de invitación coloquial-comunitaria. Se agrega este bloque porque
    # 3 falsos negativos de Capa 1 (eval 201-500) fueron precisamente posts en
    # este registro (mesa redonda académica, coloquio institucional) con score
    # justo debajo o rozando el umbral 0.45 — ver runs_log_es.md RUN-018/019.
    "El instituto acoge la celebración de un coloquio el próximo martes",
    "Tendrá lugar una mesa redonda dedicada a la memoria histórica este jueves",
    "La institución organiza una conferencia académica el 20 de julio",
    "Se llevará a cabo un encuentro de investigadores el próximo miércoles",
    "El centro cultural acoge una jornada de estudio este viernes",
    "La embajada organiza una ceremonia conmemorativa el próximo lunes",
    "L'institut accueille un colloque consacré à ce sujet mardi prochain",
    "Une table ronde aura lieu jeudi à l'auditorium",
    "Le centre de recherche propose une journée d'étude le 20 juillet",
    "L'ambassade organise une cérémonie commémorative lundi prochain",
    "Une conférence académique se tiendra au sein de l'institut ce mercredi",
    "Le centre de recherche et ses partenaires proposent une table ronde inédite",
    "The institute hosts an academic colloquium next Tuesday",
    "A round table discussion will take place this Thursday at the auditorium",
    "The research center holds a study day on July 20th",
    "The embassy organizes a commemorative ceremony next Monday",
    "A panel of researchers will convene this Wednesday afternoon",
    "The cultural institute presents a formal lecture series this month",
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
ST_MODEL        = "paraphrase-multilingual-MiniLM-L12-v2"
MAX_NLI_TOKENS  = 256          # truncado de captions para NLI (velocidad CPU)
REF_CACHE_PATH  = "ref_embeddings.npz"
LANG_TO_MODEL   = {"es": "es_core_news_lg", "en": "en_core_web_sm", "fr": "fr_core_news_lg"}
# Fecha de corte fija del estudio — ancla recencia, no datetime.now()
STUDY_CUTOFF    = datetime(2026, 7, 1, tzinfo=timezone.utc)
_NLP: dict      = {}
_ST_MODEL       = None   # sentence-transformer compartido entre capas


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
# Antes esta hipótesis estaba fija en español, sin importar el idioma del
# caption. Confirmado en la eval de 100 posts (2026-08): captions en inglés
# ("Mark your calendars — next event is this Sunday at 7PM!") sacaban scores
# casi cero (0.002-0.05) contra una hipótesis en español, mientras que un
# post en español con contenido equivalente pasaba fácil — el desajuste de
# IDIOMA entre premisa y hipótesis pesaba tanto o más que el contenido.
# Ahora se elige la hipótesis en el idioma detectado del caption (mismo
# detect_text_lang() que ya se usaba para NER más abajo).
DET_HYPOTHESES = {
    "es": "Esta publicación anuncia un evento cultural con fecha o lugar.",
    "en": "This post announces a cultural event with a date or place.",
    "fr": "Cette publication annonce un événement culturel avec une date ou un lieu.",
}

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


def detect_events_batch(captions: list[str], langs: list[str], batch_size: int = 32) -> list[float]:
    """P(evento) por caption, procesando en lotes.

    Una sola hipótesis por caption (en lugar de 2 labels del pipeline ZS)
    → mitad de forward-passes. Score = P(entailment) normalizado contra
    P(contradiction), igual que hace el pipeline zero-shot por label.

    `langs` (paralelo a `captions`) elige la hipótesis en el idioma de cada
    caption — ver nota en DET_HYPOTHESES sobre por qué esto importa.
    """
    import torch
    tok, mdl = get_detector()
    ent_idx = mdl.config.label2id.get("entailment", 0)
    con_idx = mdl.config.label2id.get("contradiction", 2)
    hyps = [DET_HYPOTHESES.get(l, DET_HYPOTHESES["es"]) for l in langs]

    scores: list[float] = []
    with torch.inference_mode():
        for i in range(0, len(captions), batch_size):
            chunk      = captions[i: i + batch_size]
            hyp_chunk  = hyps[i: i + batch_size]
            inputs = tok(
                chunk,
                hyp_chunk,
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

# "type" reemplaza la tipificación que antes hacía Capa 2b (zero-shot NLI
# multi-label sobre EVENT_LABELS_CULTURAL) — se elimina esa capa completa
# (menos sesgo de embeddings, un modelo menos que mantener) y el LLM elige
# directamente sobre la misma taxonomía de 16 labels (ver _LABEL_META),
# incluidas las opciones "nulo" para poder seguir detectando no-eventos.
_EVENT_TYPE_OPTIONS = "; ".join(f'"{lbl}"' for lbl in EVENT_LABELS)


def _llm_schema_hint(include_reasoning: bool) -> str:
    """`include_reasoning` solo debe ser True en --dry-run (fase de prueba) —
    en producción el campo no tiene consumidor downstream (ver DD, sección
    eficiencia de cuota LLM) y cuesta tokens de salida en cada llamada."""
    reasoning_line = (
        '\n  "reasoning": string             // breve justificación (solo se pide en --dry-run)'
        if include_reasoning else ""
    )
    return f"""Responde ÚNICAMENTE con un objeto JSON (sin texto adicional) con estas claves exactas:
{{
  "is_public_invitation": bool,   // invita al público/diáspora a asistir; no es noticia, comunicado, recap o aviso administrativo
  "is_upcoming": bool,            // describe algo futuro respecto a la fecha de publicación, no algo que ya ocurrió
  "type": string,                  // EXACTAMENTE uno de estos valores, copiado tal cual (incluye las opciones "nulo" si no es un evento real): {_EVENT_TYPE_OPTIONS}
  "city": string o null,           // ciudad donde ocurre el EVENTO — solo si el caption la menciona o es inequívoca por contexto; NUNCA la ciudad de la cuenta/institución que publica si el caption no la confirma
  "exact_address": string o null,  // dirección o venue específico (calle, número, nombre del lugar) SOLO si aparece textualmente en el caption Y nombra un lugar físico real y concreto (edificio, calle, plaza, institución con nombre propio) — NUNCA una palabra genérica, un verbo, el nombre de una persona, una marca sin dirección, o el título de una campaña/evento; si no hay algo así de concreto, null — no repitas aquí solo el nombre de la ciudad
  "clean_date": string "YYYY-MM-DD" o null,  // fecha real del evento, razonada por contexto
  "clean_description": string,   // 1-2 oraciones sin emojis/hashtags/menciones, para dashboard, EN ESPAÑOL
  "title": string,                // título editorial corto (6-10 palabras), sin emojis/hashtags, para mostrar como encabezado de la tarjeta del evento — no repitas la categoría, describe el evento concreto. EN ESPAÑOL
  "description_fr": string,      // MISMO contenido que clean_description, pero traducido al FRANCÉS — mismo criterio (1-2 oraciones, sin emojis/hashtags/menciones, nombres propios sin traducir)
  "title_fr": string,             // MISMO contenido que title, pero traducido al FRANCÉS — mismo criterio (6-10 palabras, sin emojis/hashtags, nombres propios sin traducir)
  "price_range": string o null,    // precio tal como aparece en el caption, ej. "Gratis", "Entrada libre", "30€ individual / 50€ grupo", "10€ sugerido" — usa una de esas frases tipo "Gratis"/"Entrada libre" si el caption dice explícitamente que no cuesta; null si el texto no menciona nada sobre precio, no asumas
  "art_tags": array de strings     // 1-3 etiquetas cortas (máx 3 palabras cada una) que describan la disciplina/medio artístico de ESTE evento concreto — más granular que "type", pensado como filtro clickeable. Reusá temas conocidos si aplican: "Música", "Danza", "Teatro", "Circo", "Literatura", "Cine", "Fotografía", "Artes visuales", "Moda", "Gastronomía", "Arquitectura", "Cómic" — pero si ninguno describe bien el evento, proponé uno nuevo corto. NUNCA uses paréntesis ni frases largas ni explicaciones dentro de cada tag (nada de "Multidisciplinario (música, historia)") — cada tag es una palabra o frase corta suelta, sin comas dentro del tag. Si el evento no tiene ningún componente artístico claro (p.ej. trámite consular, comunicado), devolvé una lista vacía [].
  "art_tags_fr": array de strings  // MISMO contenido que art_tags, un tag por tag en el mismo orden, pero cada uno traducido al FRANCÉS (mismo criterio: corto, sin paréntesis/comas, nombres propios sin traducir). Misma cantidad de elementos que art_tags, lista vacía [] si art_tags también lo es.{reasoning_line}
}}"""


LLM_CAPTION_CHARS = 900   # suficiente para juzgar is_public_invitation/is_upcoming/
                           # city/exact_address sin el texto completo


def _build_llm_prompt(caption: str, anchor_date: str, include_reasoning: bool = False) -> str:
    """Prompt/esquema compartido entre Groq y Ollama — el transporte cambia, esto no."""
    return (
        f"Esta publicación de Instagram fue hecha el {anchor_date or 'fecha desconocida'}.\n"
        f"Caption:\n\"\"\"\n{caption[:LLM_CAPTION_CHARS]}\n\"\"\"\n\n"
        "Analiza si esta publicación es una invitación real y abierta a un evento cultural, "
        "o si en realidad es una noticia institucional, un comunicado, la visita de una "
        "personalidad, un aviso administrativo o el recap de algo que ya pasó.\n"
        "Inscripciones a cursos regulares, inicio de clases, matrículas, tests de nivel "
        "o convocatorias de candidaturas/concursos NO cuentan como is_public_invitation "
        "aunque el post incluya fecha, hora y lugar — son trámites administrativos o "
        "llamados a participar, no invitaciones a asistir a un evento cultural puntual.\n"
        "Para clean_date razona explícitamente si la fecha mencionada es pasada o futura "
        "según el contexto y la fecha de publicación — no asumas futuro por defecto. Si la "
        "publicación conmemora un aniversario, hito histórico o fecha pasada (ej. \"a 197 años "
        "de...\", \"en 1958...\"), clean_date NO es esa fecha histórica — es la fecha de la "
        "conmemoración/publicación actual (usa la fecha de publicación si no hay otra más "
        "específica).\n"
        "Para title, redacta un título editorial corto (6-10 palabras) que describa el evento "
        "concreto, no la categoría genérica.\n"
        "Para city y exact_address: el nombre de la cuenta o institución que publica NO es "
        "evidencia suficiente de dónde ocurre el evento (ej. una cuenta llamada \"Alianza "
        "Francesa de Medellín\" no implica que el evento sea en Medellín si el caption no lo "
        "dice explícitamente) — usa exclusivamente lo que el texto del caption confirma.\n"
        "Para exact_address en particular, no basta con que el texto aparezca literalmente "
        "en el caption: tiene que nombrar un lugar físico concreto (una dirección, un "
        "edificio, una plaza, un parque, una institución con nombre propio) — nunca una "
        "palabra suelta genérica (ej. \"consulado\", \"remesas\", \"sur\"), un verbo, el "
        "nombre de una persona, una marca sin dirección, ni el título de una campaña o del "
        "propio evento. Aplicá esta prueba: si alguien viera ese texto solo, sin nada más de "
        "contexto, ¿alcanzaría para ubicarlo en un mapa? Si la respuesta es no, o si el "
        "caption no da ninguna pista clara de ciudad o dirección, respondé null en ambos "
        "campos — es preferible decir que no se encontró ubicación a inventar o adivinar "
        "una.\n"
        "IMPORTANTE — idioma: title y clean_description deben estar en ESPAÑOL, sin importar "
        "el idioma del caption original (aunque esté en francés o inglés) — el público de este "
        "hub es la diáspora colombiana/latinoamericana en Francia. Además, generá title_fr y "
        "description_fr: son la traducción al FRANCÉS de title/clean_description (el sitio "
        "también se muestra en francés) — mismo contenido, mismo criterio de longitud, pero en "
        "francés. Lo mismo aplica a art_tags: generá también art_tags_fr con la traducción al "
        "francés de cada tag, uno por uno y en el mismo orden. En TODOS los campos en francés: "
        "mantené sin traducir los nombres propios (lugares, instituciones, títulos de "
        "eventos) tal como aparecen en el caption.\n\n"
        f"{_llm_schema_hint(include_reasoning)}"
    )


# ── 6b-i. Transporte Ollama (local, activo por defecto) ──────────────────────
def _ollama_request(caption: str, anchor_date: str, label: str = "", include_reasoning: bool = False) -> Optional[dict]:
    """Una llamada secuencial a Ollama local. Sin cuota que pacear — a diferencia
    de Groq, no hay throttling/backoff aquí, solo el tiempo de cómputo de la
    máquina. None si Ollama no está disponible o la respuesta falla/no parsea.
    """
    prompt = _build_llm_prompt(caption, anchor_date, include_reasoning)
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

# Si un 429 pide esperar más que esto, no vale la pena reintentar: casi
# seguro es el tope diario (no el de por-minuto), que no se recupera en
# segundos. Se corta el intento ahí mismo y se deja que llm_enrich_event()
# falle sobre el otro proveedor cloud de inmediato, en vez de agotar los 3
# intentos internos esperando el Retry-After completo cada vez (~25min en
# el peor caso, observado en producción).
MAX_RATE_LIMIT_WAIT_S    = 300
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


def _groq_request(caption: str, anchor_date: str, label: str = "", include_reasoning: bool = False) -> Optional[dict]:
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
    prompt = _build_llm_prompt(caption, anchor_date, include_reasoning)
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
                if wait > MAX_RATE_LIMIT_WAIT_S:
                    print(f"  🔀 Groq pide esperar {wait:.0f}s (>{MAX_RATE_LIMIT_WAIT_S}s) [{label}]"
                          f" — probablemente tope diario, no por-minuto. No vale la pena esperar, "
                          f"cambiando de proveedor ya.", flush=True)
                    return None
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


# ── 6b-ii-bis. Transporte Google/Gemini (disponible vía LLM_PROVIDER=google) ─
# Mismo patrón de throttling que Groq (RPM + TPM paceados antes de llamar,
# más manejo de 429/Retry-After como red de seguridad). Números conservadores
# — ver comentario en GOOGLE_ENDPOINT sobre por qué no son un valor oficial
# exacto (Google no publica una tabla estática por tier, solo un dashboard).
GOOGLE_MAX_RPM             = 12
GOOGLE_MAX_TPM             = 200000
GOOGLE_TPM_SAFETY_MARGIN   = 180000
GOOGLE_MAX_ATTEMPTS        = 3
GOOGLE_RESPONSE_TOKENS_EST = 150
_last_google_call_ts       = 0.0
_google_token_window: list = []


def _google_rate_limit_wait() -> None:
    """Espacia llamadas para no superar GOOGLE_MAX_RPM, antes de cada intento."""
    global _last_google_call_ts
    min_interval = 60.0 / GOOGLE_MAX_RPM
    elapsed = time.time() - _last_google_call_ts
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_google_call_ts = time.time()


def _google_tokens_used_last_60s() -> int:
    global _google_token_window
    cutoff = time.time() - 60
    _google_token_window = [(ts, tok) for ts, tok in _google_token_window if ts >= cutoff]
    return sum(tok for _, tok in _google_token_window)


def _google_tpm_wait(estimated_tokens: int) -> int:
    """Bloquea hasta que quepan estimated_tokens en la ventana de 60s bajo
    GOOGLE_TPM_SAFETY_MARGIN, en vez de disparar la llamada y esperar el 429."""
    while True:
        used = _google_tokens_used_last_60s()
        if used + estimated_tokens <= GOOGLE_TPM_SAFETY_MARGIN or not _google_token_window:
            return used
        oldest_ts = _google_token_window[0][0]
        wait = max(0.5, 60 - (time.time() - oldest_ts) + 0.1)
        print(f"  ⏳ Google TPM: {used}+{estimated_tokens} tok > {GOOGLE_TPM_SAFETY_MARGIN} margen"
              f" — esperando {wait:.1f}s", flush=True)
        time.sleep(wait)


def _google_request(caption: str, anchor_date: str, label: str = "", include_reasoning: bool = False) -> Optional[dict]:
    """Llama a Gemini (vía capa de compatibilidad OpenAI) con reintentos.
    None si falla tras agotar GOOGLE_MAX_ATTEMPTS. Mismo patrón que
    _groq_request — ver ese docstring para el detalle del manejo de 429.
    """
    if not GOOGLE_API_KEY:
        return None
    prompt = _build_llm_prompt(caption, anchor_date, include_reasoning)
    est_tokens = _estimate_tokens(prompt) + GOOGLE_RESPONSE_TOKENS_EST

    for attempt in range(1, GOOGLE_MAX_ATTEMPTS + 1):
        _google_rate_limit_wait()
        used = _google_tpm_wait(est_tokens)
        _google_token_window.append((time.time(), est_tokens))
        try:
            resp = requests.post(
                GOOGLE_ENDPOINT,
                headers={"Authorization": f"Bearer {GOOGLE_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model":           GOOGLE_MODEL,
                    "messages":        [{"role": "user", "content": prompt}],
                    "temperature":     0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                if wait > MAX_RATE_LIMIT_WAIT_S:
                    print(f"  🔀 Google pide esperar {wait:.0f}s (>{MAX_RATE_LIMIT_WAIT_S}s) [{label}]"
                          f" — probablemente tope diario, no por-minuto. No vale la pena esperar, "
                          f"cambiando de proveedor ya.", flush=True)
                    return None
                print(f"  ⚠️  Google 429 rate-limit [{label}] intento {attempt}/{GOOGLE_MAX_ATTEMPTS}"
                      f" (~{est_tokens} tok, ventana={used}) — esperando {wait:.1f}s", flush=True)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            print(f"  🪙 Google [{label}] ~{est_tokens} tok  (ventana 60s: {used + est_tokens})", flush=True)
            return json.loads(content)
        except Exception as e:
            print(f"  ⚠️  Google falló [{label}] intento {attempt}/{GOOGLE_MAX_ATTEMPTS}: "
                  f"{type(e).__name__}: {e}", flush=True)
            if attempt < GOOGLE_MAX_ATTEMPTS:
                time.sleep(1.5)
    return None


# ── 6b-ii-ter. Transporte DeepSeek (disponible vía LLM_PROVIDER=deepseek) ────
# Sin throttling de RPM/TPM propio — ver comentario en DEEPSEEK_ENDPOINT sobre
# por qué (límite de concurrencia, no de por-minuto; irrelevante en llamadas
# secuenciales). Reintenta igual que los demás ante 429/error de red.
DEEPSEEK_MAX_ATTEMPTS        = 3
DEEPSEEK_RESPONSE_TOKENS_EST = 150


def _deepseek_request(caption: str, anchor_date: str, label: str = "", include_reasoning: bool = False) -> Optional[dict]:
    """Llama a DeepSeek con reintentos. None si falla tras agotar
    DEEPSEEK_MAX_ATTEMPTS. Mismo patrón que _groq_request pero sin pacing
    RPM/TPM (no aplica — ver comentario en DEEPSEEK_ENDPOINT).
    """
    if not DEEPSEEK_API_KEY:
        return None
    prompt = _build_llm_prompt(caption, anchor_date, include_reasoning)
    est_tokens = _estimate_tokens(prompt) + DEEPSEEK_RESPONSE_TOKENS_EST

    for attempt in range(1, DEEPSEEK_MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                DEEPSEEK_ENDPOINT,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model":           DEEPSEEK_MODEL,
                    "messages":        [{"role": "user", "content": prompt}],
                    "temperature":     0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  ⚠️  DeepSeek 429 [{label}] intento {attempt}/{DEEPSEEK_MAX_ATTEMPTS}"
                      f" (~{est_tokens} tok) — esperando {wait:.1f}s", flush=True)
                time.sleep(min(wait, MAX_RATE_LIMIT_WAIT_S))
                continue
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            content = choice["message"]["content"]
            if not content:
                # Hallazgo 2026-08-24: DeepSeek a veces devuelve 200 con
                # content vacío bajo carga (no un 429/error explícito) —
                # json.loads("") daría el mismo JSONDecodeError genérico de
                # siempre, pero acá lo distinguimos explícitamente y
                # logueamos finish_reason (length/content_filter/etc.) para
                # saber la causa real la próxima vez que pase.
                raise ValueError(f"content vacío, finish_reason={choice.get('finish_reason')!r}")
            print(f"  🪙 DeepSeek [{label}] ~{est_tokens} tok", flush=True)
            return json.loads(content)
        except Exception as e:
            print(f"  ⚠️  DeepSeek falló [{label}] intento {attempt}/{DEEPSEEK_MAX_ATTEMPTS}: "
                  f"{type(e).__name__}: {e}", flush=True)
            if attempt < DEEPSEEK_MAX_ATTEMPTS:
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


def _cerebras_request(caption: str, anchor_date: str, label: str = "", include_reasoning: bool = False) -> Optional[dict]:
    """Llama a Cerebras con reintentos. None si falla tras agotar
    CEREBRAS_MAX_ATTEMPTS. Mismo patrón que _groq_request (endpoint
    OpenAI-compatible, mismo prompt/esquema) — ver DD-033 update 5.
    """
    if not CEREBRAS_API_KEY:
        return None
    prompt = _build_llm_prompt(caption, anchor_date, include_reasoning)
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
                if wait > MAX_RATE_LIMIT_WAIT_S:
                    print(f"  🔀 Cerebras pide esperar {wait:.0f}s (>{MAX_RATE_LIMIT_WAIT_S}s) [{label}]"
                          f" — probablemente tope diario, no por-minuto. No vale la pena esperar, "
                          f"cambiando de proveedor ya.", flush=True)
                    return None
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
    "type":                 None,
    "city":                 None,
    "exact_address":        None,
    "clean_date":           None,
    "clean_description":    None,
    "title":                None,
    "description_fr":       None,
    "title_fr":             None,
    "price_range":          None,
    "art_tags":             [],
    "art_tags_fr":          [],
    "reasoning":            None,
}


def _clean_art_tags(raw) -> list[str]:
    """Valida/normaliza art_tags del LLM: debe ser una lista de strings cortos
    sin comas ni paréntesis (evita reproducir el problema de artType de cuenta
    en Excel, donde texto libre con comas dentro de paréntesis rompía
    cualquier split simple — ver docs/decisions_es.md DD-042). Cualquier tag
    que no cumpla el formato esperado se descarta en vez de intentar
    repararlo — mejor una lista más corta que un tag corrupto filtrando basura
    al menú."""
    if not isinstance(raw, list):
        return []
    out = []
    for tag in raw:
        if not isinstance(tag, str):
            continue
        t = tag.strip()
        if not t or "(" in t or ")" in t or "," in t or len(t) > 40:
            continue
        out.append(t)
    return out[:3]


def _extract_llm_fields(data: Optional[dict]) -> dict:
    if data is None:
        return dict(_LLM_DEFAULTS)
    return {
        "is_public_invitation": data.get("is_public_invitation"),
        "is_upcoming":          data.get("is_upcoming"),
        "type":                 data.get("type") or None,
        "city":                 data.get("city") or None,
        "exact_address":        data.get("exact_address") or None,
        "clean_date":           data.get("clean_date") or None,
        "clean_description":    data.get("clean_description") or None,
        "title":                data.get("title") or None,
        "description_fr":       data.get("description_fr") or None,
        "title_fr":             data.get("title_fr") or None,
        "price_range":          data.get("price_range") or None,
        "art_tags":             _clean_art_tags(data.get("art_tags")),
        "art_tags_fr":          _clean_art_tags(data.get("art_tags_fr")),
        "reasoning":            data.get("reasoning") or None,
    }


def llm_enrich_event_ollama(caption: str, post_timestamp: str = "", label: str = "", include_reasoning: bool = False) -> dict:
    """Capa 3 vía Ollama local (modelo configurable vía OLLAMA_MODEL, default
    qwen2.5:7b) — mismo prompt/esquema que la versión Groq (_build_llm_prompt),
    solo cambia el transporte. Sin cuota que pacear: llamada secuencial, ver
    _ollama_request.
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_ollama_request(caption, anchor_date, label=label, include_reasoning=include_reasoning))


def llm_enrich_event_groq(caption: str, post_timestamp: str = "", label: str = "", include_reasoning: bool = False) -> dict:
    """Capa 3 vía Groq (llama-3.3-70b-versatile). Throttling RPM/TPM y
    reintentos en _groq_request — ver DD-033 (update 2).
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_groq_request(caption, anchor_date, label=label, include_reasoning=include_reasoning))


def llm_enrich_event_cerebras(caption: str, post_timestamp: str = "", label: str = "", include_reasoning: bool = False) -> dict:
    """Capa 3 vía Cerebras (llama-3.3-70b, mismo modelo que Groq). Throttling
    RPM/TPM y reintentos en _cerebras_request — ver DD-033 (update 5).
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_cerebras_request(caption, anchor_date, label=label, include_reasoning=include_reasoning))


def llm_enrich_event_google(caption: str, post_timestamp: str = "", label: str = "", include_reasoning: bool = False) -> dict:
    """Capa 3 vía Gemini (gemini-2.5-flash-lite, capa de compatibilidad
    OpenAI). Throttling RPM/TPM y reintentos en _google_request — ver
    DD-033 (update 8).
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_google_request(caption, anchor_date, label=label, include_reasoning=include_reasoning))


def llm_enrich_event_deepseek(caption: str, post_timestamp: str = "", label: str = "", include_reasoning: bool = False) -> dict:
    """Capa 3 vía DeepSeek (deepseek-v4-flash). Sin tier gratis (pago por
    token, el más barato del mercado) — ver DD-033 (update 8). Reintentos
    en _deepseek_request, sin throttling RPM/TPM propio (no aplica, ver
    comentario en DEEPSEEK_ENDPOINT).
    """
    if not caption:
        return dict(_LLM_DEFAULTS)
    anchor_date = (post_timestamp or "")[:10]
    return _extract_llm_fields(_deepseek_request(caption, anchor_date, label=label, include_reasoning=include_reasoning))


# ── 6b-iv. Fallback automático entre proveedores cloud (DD-033 update 7, ────
#          orden ampliado en update 8) ──────────────────────────────────────
# Cuando el proveedor preferido (LLM_PROVIDER) agota su cupo diario a mitad
# de una corrida, en vez de solo esperar/detenerse, se cambia automáticamente
# al siguiente proveedor cloud para el resto de la corrida — así se aprovecha
# el cupo combinado de todos sin que el usuario tenga que pararla y
# reiniciarla manualmente cambiando LLM_PROVIDER a mano (como se hacía antes
# de este fix). Ollama queda fuera: no tiene cupo diario que se agote, y
# mezclar local+nube automáticamente no es lo que se pidió.
#
# Orden original pedido por Diego (2026-08-21): groq -> google -> deepseek ->
# cerebras. Reordenado 2026-08-24 tras observar en una corrida real de 315
# posts que DeepSeek falla con más frecuencia que los otros tres, pero de
# forma distinta a lo esperado: no es 429/cupo agotado, es la API
# devolviendo HTTP 200 con message.content VACÍO (revienta json.loads con
# "Expecting value: line 1 column 1 (char 0)", ver _deepseek_request) —
# probablemente el motor de inferencia cortando bajo carga sin devolver un
# error explícito. Como además es el único proveedor pago de los cuatro
# (groq/google/cerebras son gratis con cupo diario), no tiene sentido
# intentarlo antes que una alternativa gratis y más estable — se movió al
# final, después de Cerebras (que en esa misma corrida real no falló ni una
# vez, ver decisions_es.md DD-050). El orden de este dict ES el
# orden de fallback (Python preserva el orden de inserción) cuando
# LLM_PROVIDER="groq" (default).
_CLOUD_PROVIDERS = {
    "groq":     llm_enrich_event_groq,
    "google":   llm_enrich_event_google,
    "cerebras": llm_enrich_event_cerebras,
    "deepseek": llm_enrich_event_deepseek,
}
_provider_failed_this_run: set = set()


def _llm_call_failed(result: dict) -> bool:
    """Mismo criterio que ya usa el caller para LLM_UNKNOWN_PENALTY: si
    is_public_invitation e is_upcoming vinieron ambos en None, el transporte
    no devolvió nada usable (agotó sus reintentos internos) — señal de que
    vale la pena probar el otro proveedor en vez de seguir insistiendo."""
    return result.get("is_public_invitation") is None and result.get("is_upcoming") is None


def llm_enrich_event(caption: str, post_timestamp: str = "", label: str = "", include_reasoning: bool = False) -> dict:
    """Capa 3 — limpia fecha/ubicación, redacta descripción, tipifica el
    evento (reemplaza la vieja Capa 2b de embeddings) y detecta noticias
    institucionales sin invitación real al público. LLM_PROVIDER elige el
    transporte preferido: llm_enrich_event_ollama() (default), o uno de los
    cuatro proveedores cloud (groq/google/deepseek/cerebras — ver
    _CLOUD_PROVIDERS).

    `include_reasoning`: solo True en --dry-run — en producción no se pide
    ese campo (nadie lo consume río abajo, y cuesta tokens de salida en
    cada llamada).

    Si LLM_PROVIDER es un proveedor cloud y falla (cupo agotado u otro error
    tras sus reintentos internos), se reintenta automáticamente con el
    siguiente proveedor cloud en el orden de _CLOUD_PROVIDERS antes de
    rendirse — ver DD-033 (update 7, orden ampliado en update 8). Un
    proveedor que falla se marca para el resto de esta corrida (no se
    reintenta post a post; los cupos diarios no se recuperan a mitad de
    una corrida).

    Se llama SOLO sobre candidatos que ya pasaron Capas 1+2 (~30-50/corrida en
    pruebas, corpus completo en corridas reales). Si TODOS los transportes
    disponibles fallan se devuelven valores null — el caller aplica
    LLM_UNKNOWN_PENALTY (penalización intermedia) en ese caso, ver DD-033.
    """
    if LLM_PROVIDER not in _CLOUD_PROVIDERS:
        return llm_enrich_event_ollama(caption, post_timestamp, label=label, include_reasoning=include_reasoning)

    order = [LLM_PROVIDER] + [p for p in _CLOUD_PROVIDERS if p != LLM_PROVIDER]
    result = dict(_LLM_DEFAULTS)
    for provider in order:
        if provider in _provider_failed_this_run:
            continue
        result = _CLOUD_PROVIDERS[provider](caption, post_timestamp, label=label, include_reasoning=include_reasoning)
        if not _llm_call_failed(result):
            return result
        print(f"  🔀 {provider} agotado/fallando — cambiando de proveedor "
              f"para el resto de esta corrida.", flush=True)
        _provider_failed_this_run.add(provider)
    return result


# ── 7. Helpers — fechas y scores ──────────────────────────────────────────────
# Patrones de fecha para extracción previa antes de dateparser
# DD-037: se agregó "." como separador válido (además de "/" y "-") — los
# flyers de conciertos suelen listar fechas como "30.06 · Banda", que el
# regex anterior no capturaba en absoluto.
_DATE_RE = re.compile(
    r"""
    \b\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?\b          # DD/MM, DD.MM o DD/MM/YYYY
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

# DD-037: detecta el patrón puramente numérico D[./-]M(?:[./-]Y)? para poder
# validar el componente de "mes" antes de dejarlo pasar a dateparser. Sin este
# guardrail, notaciones de temporada como "26/27" (teatro/ópera) se leen
# como si "27" fuera un año de 2 dígitos y producen una fecha inventada
# (ej. "26/27" → 2027-07-26, usando el mes del post como relleno).
_NUMERIC_DM_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?$")


_WEEKDAY_RE = re.compile(
    r"\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo"
    r"|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)


def _has_day_signal(snippet: str) -> bool:
    """True si el snippet trae información de día real (dígito de día o
    nombre de día de la semana) — no solo mes/año. dateparser con
    PREFER_DAY_OF_MONTH='first' fabrica un día 1 arbitrario cuando el texto
    solo dice algo como "este marzo"; sin esta verificación esa falsa
    precisión se cuela en raw_date/eventDate como si fuera un dato real."""
    return bool(re.search(r"\d", snippet)) or bool(_WEEKDAY_RE.search(snippet))


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

    # DD-037: la ventana era de 600 caracteres — en captions largos (flyers
    # multi-línea con line-up, horarios y hashtags, comunes en este corpus)
    # la fecha real cae fuera de esa ventana y el post se descartaba como
    # "sin fecha en texto" sin llegar siquiera a Capa 3. Se amplió a 2000
    # (los captions de este corpus rara vez la superan).
    window = text[:2000]

    # Primero intenta sobre los fragmentos extraídos por regex (más preciso).
    # _DATE_RE ya exige dígito o nombre de día en sus 4 patrones, así que
    # cualquier match de acá trae señal de día real — no necesita el filtro.
    for match in _DATE_RE.finditer(window):
        snippet = match.group(0).strip()
        if len(snippet) < 3:
            continue

        # DD-037: si el snippet es puramente numérico (D/M o D/M/Y), valida
        # que el segundo componente sea un mes plausible (1-12) antes de
        # dejarlo pasar a dateparser. Sin este chequeo, notaciones de
        # temporada tipo "26/27" se leen como día=26 + año=27 (2 dígitos) y
        # producen una fecha inventada sin ninguna fecha real en el texto.
        numeric_match = _NUMERIC_DM_RE.match(snippet)
        if numeric_match:
            month_component = int(numeric_match.group(2))
            if month_component > 12:
                continue
            # Normaliza "." a "/" antes de dateparser: con punto como
            # separador, dateparser puede confundir "01.07" con una hora
            # (01:07) en vez de una fecha (1 de julio).
            snippet = snippet.replace(".", "/")

        parsed = dateparser.parse(snippet, languages=langs, settings=settings)
        if parsed:
            return parsed.strftime("%Y-%m-%d")

    # Fallback: busca fechas en el texto completo. A diferencia de _DATE_RE,
    # dateparser.search_dates() puede matchear un mes o año sueltos (p.ej.
    # "este marzo") y, con PREFER_DAY_OF_MONTH='first', inventarles un día 1
    # que no está en el texto. Se descarta ese caso en vez de devolver una
    # fecha con falsa precisión.
    try:
        from dateparser.search import search_dates
        results = search_dates(window, languages=langs, settings=settings)
        if results:
            matched_text, parsed_dt = results[0]
            # DD-037: mismo guardrail que en el loop principal — search_dates()
            # encuentra "26/27" por su cuenta (no pasa por _DATE_RE ni por el
            # chequeo de arriba), así que sin esto el fallback reintroduce el
            # mismo bug de notación de temporada leída como fecha inventada.
            numeric_match = _NUMERIC_DM_RE.match(matched_text.strip())
            if numeric_match and int(numeric_match.group(2)) > 12:
                pass
            elif _has_day_signal(matched_text):
                return parsed_dt.strftime("%Y-%m-%d")
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
    # Instagram permite ocultar el conteo de likes; algunos scrapes devuelven
    # -1 en ese caso. log1p exige x > -1, así que negativos se tratan como 0.
    likes    = max(0, likes or 0)
    comments = max(0, comments or 0)
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
        # :PendingReview — staging gate (2026-08-21, ver review_events.py). Todo
        # evento nuevo nace oculto del sitio hasta que Diego lo apruebe/edite/
        # rechace interactivamente; 5_export_dashboard_data.py excluye esta
        # label igual que ya excluye :Rejected. Los eventos que ya estaban en
        # Neo4j antes de este cambio no llevan la label, así que siguen en vivo.
        session.run("""
            MERGE (e:Event {id: $id})
            SET e:PendingReview,
                e.title        = $title,
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
                e.createdAt    = datetime(),
                e.description        = $description,
                e.titleFr            = $titleFr,
                e.descriptionFr      = $descriptionFr,
                e.isPublicInvitation = $isPublicInvitation,
                e.isUpcoming         = $isUpcoming,
                e.priceRange         = $priceRange,
                e.eventArtTags       = $eventArtTags,
                e.eventArtTagsFr     = $eventArtTagsFr,
                e.llmReasoning       = $llmReasoning,
                e.sourcePostUrl      = $sourcePostUrl,
                e.imageUrl           = $imageUrl,
                e.sourceAuthor       = $sourceAuthor,
                e.sourcePostDate     = $sourcePostDate,
                e.artType            = $artType,
                e.institutionType    = $institutionType,
                e.culturalIdentity   = $culturalIdentity,
                e.geoZone            = $geoZone,
                e.parentInstitution  = $parentInstitution
        """, **{k: event[k] for k in [
            "id", "title", "type", "category", "rawDate", "eventDate",
            "locationName", "cityName", "exactAddress", "hotnessScore", "eventScore", "confidence",
            "layer1Score", "embedding",
            "description", "titleFr", "descriptionFr",
            "isPublicInvitation", "isUpcoming", "priceRange", "eventArtTags", "eventArtTagsFr", "llmReasoning",
            "sourcePostUrl", "sourceAuthor", "sourcePostDate", "imageUrl",
            "artType", "institutionType", "culturalIdentity", "geoZone", "parentInstitution",
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
    skip_posts: int         = 0,
    batch_size: int         = 32,
    date_window: int        = 3,
    sim_threshold: float    = 0.82,
    dry_run: bool           = False,
    accounts: list[str]     = None,
    diag_csv: str           = None,
    max_post_age_days: int  = 20,
    only_dedicated_scraper: bool = True,
):
    t_start = time.time()
    print("\n🎭 Fase 4-B — Extracción de Eventos (3 capas)")
    print("=" * 60)
    if LLM_PROVIDER == "groq":
        capa3_status = f"Groq({GROQ_MODEL})" if GROQ_API_KEY else "Groq(GROQ_API_KEY ausente!)"
    elif LLM_PROVIDER == "google":
        capa3_status = f"Google({GOOGLE_MODEL})" if GOOGLE_API_KEY else "Google(GOOGLE_API_KEY ausente!)"
    elif LLM_PROVIDER == "deepseek":
        capa3_status = f"DeepSeek({DEEPSEEK_MODEL})" if DEEPSEEK_API_KEY else "DeepSeek(DEEPSEEK_API_KEY ausente!)"
    elif LLM_PROVIDER == "cerebras":
        capa3_status = f"Cerebras({CEREBRAS_MODEL})" if CEREBRAS_API_KEY else "Cerebras(CEREBRAS_API_KEY ausente!)"
    else:
        capa3_status = f"Ollama({OLLAMA_MODEL}@localhost:11434)"
    print(f"  L1≥{layer1_threshold}  L2≥{layer2_threshold}  "
          f"max_posts={max_posts or '∞'}  batch={batch_size}  "
          f"sim≥{sim_threshold}  date±{date_window}d  "
          f"max_post_age={max_post_age_days}d  "
          f"solo_scraper_dedicado={only_dedicated_scraper}  "
          f"Capa3={capa3_status}")
    if accounts:
        print(f"  Filtro cuentas: {accounts}")

    # Cargar posts
    # ORDER BY p.id: determinismo — sin esto, dos corridas MATCH...LIMIT sobre
    # el mismo grafo sin escrituras de por medio típicamente devuelven el
    # mismo set en la práctica, pero Cypher no lo garantiza. Importa sobre
    # todo para --diag-csv: si vas a comparar esta salida contra una
    # clasificación externa hecha aparte, necesitas la MISMA muestra.
    # skip_posts (SKIP, antes de LIMIT) existe para poder pedir un lote
    # NUEVO en --dry-run: como dry-run nunca marca eventExtracted, correr
    # el mismo comando dos veces sin --skip devuelve siempre el mismo lote.
    skip_clause  = f"SKIP {skip_posts}" if skip_posts > 0 else ""
    limit_clause = f"LIMIT {max_posts}" if max_posts > 0 else ""
    account_filter = "AND a.username IN $accounts" if accounts else ""
    # Filtro de recencia (hallazgo 2026-08-24, decisions_es.md): posts
    # embebidos en profile_<username>.json (source="profile_embed", ver
    # 2_build_graph.py) NUNCA pasan por la ventana onlyPostsNewerThan
    # verificada del scraper dedicado — pueden ser de cualquier antigüedad.
    # Comparación de string (los primeros 10 chars de un timestamp ISO
    # ordenan igual que la fecha real) en vez de datetime(p.timestamp) para
    # no reventar la query si algún timestamp viene malformado/vacío.
    age_filter = ""
    cutoff_date_str = None
    if max_post_age_days and max_post_age_days > 0:
        cutoff_date_str = (datetime.now(timezone.utc) - timedelta(days=max_post_age_days)).strftime("%Y-%m-%d")
        age_filter = "AND p.timestamp IS NOT NULL AND substring(p.timestamp, 0, 10) >= $cutoffDate"
    # Filtro de origen (2026-08-24): usa la firma de arquitectura del JSON que
    # 2_build_graph.py ya traduce a p.sourceDedicatedScraper — true solo si
    # ese post id alguna vez se cargó desde posts_<username>.json (el actor
    # apify/instagram-post-scraper, que sí trae latestComments/musicInfo/
    # productType y pasó por la ventana onlyPostsNewerThan verificada). Los
    # posts que SOLO llegaron embebidos en profile_<username>.json
    # (latestPosts del actor de perfil) quedan fuera por defecto.
    source_filter = "AND p.sourceDedicatedScraper = true" if only_dedicated_scraper else ""
    with driver.session() as session:
        posts = session.run(f"""
            MATCH (a:Account)-[:PUBLISHED]->(p:Post)
            WHERE p.caption IS NOT NULL
              AND size(p.caption) >= {MIN_CAPTION_LEN}
              AND (p.eventExtracted IS NULL OR p.eventExtracted = false)
              {age_filter}
              {source_filter}
              {account_filter}
            RETURN p.id            AS id,
                   p.caption       AS caption,
                   p.likesCount    AS likes,
                   p.commentsCount AS comments,
                   p.timestamp     AS timestamp,
                   [(p)-[:HAS_HASHTAG]->(h:Hashtag) | h.name] AS hashtags,
                   p.url           AS url,
                   p.displayUrl    AS displayUrl,
                   a.username      AS author,
                   a.artType             AS artType,
                   a.institutionType     AS institutionType,
                   a.culturalIdentity    AS culturalIdentity,
                   a.geoZone             AS geoZone,
                   a.parentInstitution   AS parentInstitution,
                   collect(DISTINCT [(p)-[:TAGS_USER]->(tu) | tu.username])[0] AS taggedUsers,
                   collect(DISTINCT [(p)-[:MENTIONS]->(m)   | m.username])[0]  AS mentions,
                   [(p)-[:TAGGED_AT]->(loc:Location) | loc.name][0]            AS taggedLocation
            ORDER BY p.id
            {skip_clause}
            {limit_clause}
        """, accounts=accounts or [], cutoffDate=cutoff_date_str).data()

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
                "post_id":     post.get("id", ""),
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
    cand_langs    = [detect_text_lang(c) for c in cand_captions]

    # ── Capa 2a — detección binaria BATCHEADA (modelo ligero) ────────────────
    print(f"\n  🟠 Capa 2a — detección binaria batcheada ({DET_MODEL})...")
    t0 = time.time()
    det_scores     = detect_events_batch(cand_captions, cand_langs, batch_size=batch_size)
    is_event_flags = [s >= layer2_threshold for s in det_scores]
    pos_idx        = [j for j, f in enumerate(is_event_flags) if f]
    print(f"  ✅ Capa 2a: {len(pos_idx)} positivos / {len(cand_posts)} candidatos"
          f"  ({time.time() - t0:.1f}s, batch={batch_size})")

    # NOTA: la vieja Capa 2b (tipificación multi-label vía zero-shot NLI,
    # mDeBERTa/MiniLMv2 sobre EVENT_LABELS_CULTURAL) se eliminó. La
    # tipificación ahora la hace el LLM de Capa 3 directamente (campo "type"
    # en _LLM_SCHEMA_HINT, misma taxonomía de 16 labels) — un modelo menos
    # que correr, y sin el sesgo de embeddings que ya vimos en Capa 1/2a.

    # Dedup por post_id: cuando el mismo post está co-publicado por varias
    # cuentas (RETURN de la query trae una fila por cada (Account,Post) —
    # confirmado en la eval de 100: 3 pares duplicados), sin esto Capa 3 se
    # llamaría dos veces por el mismo texto exacto. Cache por corrida.
    llm_result_cache: dict = {}

    # ── Persistencia + NER (solo eventos, o todo en dry-run) ─────────────────
    since_last_mark      = 0
    skipped_llm_gate     = 0
    skipped_no_text_date = 0  # DD-036 update: candidatos que ni siquiera llaman
                               # a Capa 3 por falta de fecha real en el texto
    for j in tqdm(range(len(cand_posts)), desc="  Eventos"):
        post, emb, l1 = cand_posts[j], cand_embs[j], cand_l1[j]
        det_score = det_scores[j]
        is_event  = is_event_flags[j]

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
        has_text_date = False
        if is_event or dry_run:
            lang       = detect_text_lang(post["caption"])
            ner        = extract_ner(post["caption"], lang)
            org_name   = ner["orgs"][0] if ner["orgs"] else None
            # FIX 2: fechas ancladas al timestamp del post, no a datetime.now()
            event_date = extract_dates(post["caption"], post.get("timestamp", "") or "")
            # DD-036: señal de fecha REAL extraída del texto (regex/dateparser,
            # ya blindado contra falsa precisión por DD-034 vía _has_day_signal).
            # Se guarda ANTES de que Capa 3 pueda sobreescribir event_date con
            # su propio clean_date "razonado por contexto" más abajo — sirve de
            # gate independiente del LLM, ver should_create. El objetivo es
            # cerrar el patrón dominante de falsos positivos (eval 201-500:
            # ~14 de 22) donde Capa 3 acepta un post como evento sin que el
            # texto contenga ninguna fecha ancorable (listados de temporada,
            # apertura de venta sin fecha del evento, rutas itinerantes).
            has_text_date = event_date is not None

        hotness     = compute_hotness(
            post.get("likes", 0) or 0,
            post.get("comments", 0) or 0,
            post.get("timestamp", "") or "",
        )

        # Capa 3 — LLM, solo sobre candidatos que ya pasaron Capas 1+2 (is_event=True)
        # Y que además tienen fecha real en el texto (has_text_date, DD-036
        # update). Antes se llamaba al LLM primero y el gate de fecha se
        # aplicaba después sobre el veredicto — pero el gate es determinístico
        # e independiente de lo que diga el LLM, así que si ya sabemos que
        # should_create va a ser False por falta de fecha, no tiene sentido
        # gastar la llamada. Medido sobre los 425 candidatos de la muestra de
        # 495 posts: 173 (40.7%) no tienen fecha en el texto — esas llamadas
        # se ahorran sin cambiar ninguna decisión final (ver docs/decisions_es.md DD-036).
        # Ahora también tipifica el evento (reemplaza Capa 2b) y da price_range.
        is_public_invitation = is_upcoming = clean_description = llm_reasoning = None
        llm_title = llm_price_range = top_label = None
        llm_description_fr = llm_title_fr = None
        llm_city = llm_exact_address = None
        llm_art_tags = []
        llm_art_tags_fr = []
        llm_penalty = 1.0
        if is_event and has_text_date:
            pid = post.get("id")
            if pid in llm_result_cache:
                llm_out = llm_result_cache[pid]
            else:
                llm_out = llm_enrich_event(
                    post["caption"], post.get("timestamp", "") or "",
                    label=f"@{post.get('author', '?')}/{post.get('id', '?')}",
                    include_reasoning=dry_run,
                )
                llm_result_cache[pid] = llm_out
            top_label         = llm_out.get("type")
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
                # DD-039: el except de abajo tragaba CUALQUIER fallo de parseo
                # sin clampear — si post["timestamp"] venía vacío/malformado,
                # pd nunca se calculaba, la comparación de clamp nunca corría,
                # y clean_date del LLM pasaba sin validar en absoluto. Así se
                # colaron 3 eventos reales con fechas 2036/2052/2090 (ninguna
                # con respaldo real en el texto — "10 años de aniversario",
                # "menores de 26 años", "dura 90 minutos" mal interpretados).
                # Ahora: si ed no parsea como ISO, se descarta directo (no es
                # una fecha válida, punto). Si pd falla, se usa STUDY_CUTOFF
                # como ancla de respaldo en vez de abortar el chequeo entero.
                try:
                    ed = datetime.fromisoformat(event_date.replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, TypeError, AttributeError):
                    event_date = None
                    dates_clamped += 1
                else:
                    try:
                        pd = datetime.fromisoformat(
                            (post.get("timestamp", "") or "").replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except (ValueError, TypeError):
                        pd = STUDY_CUTOFF.replace(tzinfo=None)
                    if abs((ed - pd).days) > EVENT_DATE_CLAMP_DAYS:
                        event_date = None
                        dates_clamped += 1
            is_public_invitation = llm_out.get("is_public_invitation")
            is_upcoming          = llm_out.get("is_upcoming")
            clean_description    = llm_out.get("clean_description")
            llm_reasoning         = llm_out.get("reasoning")
            llm_title            = llm_out.get("title")
            llm_description_fr   = llm_out.get("description_fr")
            llm_title_fr         = llm_out.get("title_fr")
            llm_price_range      = llm_out.get("price_range")
            llm_art_tags         = llm_out.get("art_tags") or []
            llm_art_tags_fr      = llm_out.get("art_tags_fr") or []
            # El LLM a veces devuelve una lista de largo distinto para
            # art_tags_fr (p.ej. se olvida de traducir el último tag) — como
            # el frontend empareja por posición (evTags en app.js), un
            # desalineamiento silencioso mostraría una traducción para el tag
            # equivocado. Se trunca al más corto de los dos en vez de
            # confiar en que siempre vienen parejos.
            if len(llm_art_tags_fr) != len(llm_art_tags):
                n = min(len(llm_art_tags), len(llm_art_tags_fr))
                llm_art_tags, llm_art_tags_fr = llm_art_tags[:n], llm_art_tags_fr[:n]
            if is_public_invitation is None or is_upcoming is None:
                # LLM falló tras agotar reintentos — verdicto incierto, no
                # confianza ciega (DD-033-update): penalización intermedia.
                llm_penalty = LLM_UNKNOWN_PENALTY
            else:
                llm_penalty = 1.0 if (is_public_invitation and is_upcoming) else LLM_REJECT_PENALTY

        # category/penalty: top_label es None cuando is_event=False O cuando
        # is_event=True pero Capa 3 no devolvió type (falló/sin cuota) — en
        # ese segundo caso NO usamos penalty=0.0 (eso significaría "no es
        # cultural", una afirmación que no hicimos), usamos 1.0 (neutral,
        # "no sabemos" != "no es válido"). Cuando el LLM SÍ corrió y calificó
        # el post como una de las categorías "nulo" de la taxonomía (ver
        # _LABEL_META), ese 0.0 real se sigue respetando vía _PEN_MAP.
        category = _CAT_MAP.get(top_label, "sin_clasificar") if top_label else "sin_clasificar"
        penalty  = _PEN_MAP.get(top_label, 1.0)              if top_label else 1.0

        # Gate de creación (antes solo bajaba el score, ahora también decide
        # si se escribe el nodo — ver eval de 100 posts: 58% -> 86% de
        # acuerdo con este cambio). Si Capa 3 corrió y dijo explícitamente
        # que NO es invitación pública futura, no se crea el evento. Si Capa
        # 3 falló del todo (sin veredicto), se mantiene el comportamiento
        # viejo — crear igual, con LLM_UNKNOWN_PENALTY — por resiliencia.
        # DD-036: además, exige has_text_date — el texto del caption tiene que
        # traer una fecha real (regex/dateparser), no basta con que el LLM lo
        # "sienta" como invitación pública. No se aplica excepción para
        # eventos del mismo día (p.ej. "hoy a las 16h") a propósito: el script
        # corre como máximo cada 2 días, así que un evento de hoy ya sería
        # irrelevante para el momento en que se procese.
        # DD-039: category en NULL_CATS ("nulo") significa que Capa 3 SÍ
        # calificó el post con una etiqueta de la taxonomía, y esa etiqueta
        # es explícitamente "esto no es un evento cultural real" (contenido
        # personal, promoción comercial, etc. — ver _LABEL_META). Antes
        # should_create ignoraba esto por completo: penalty caía a 0.0 y
        # event_score quedaba en 0.0, pero el nodo :Event se creaba igual
        # si is_public_invitation/is_upcoming venían en True — 3 casos
        # reales en la corrida de producción (deporte, reapertura de café,
        # feria comercial) confirman que esta contradicción SÍ ocurre, no
        # es solo teórica. category="sin_clasificar" (top_label ausente,
        # el LLM no pudo tipificar) NO cuenta como nulo — solo bloquea si
        # el LLM tipificó explícitamente como no-cultural.
        llm_ran_ok    = is_public_invitation is not None and is_upcoming is not None
        should_create = (
            is_event
            and (not llm_ran_ok or (is_public_invitation and is_upcoming))
            and has_text_date
            and category not in NULL_CATS
        )

        event_score = compute_event_score(det_score, hotness, penalty * llm_penalty) if is_event else 0.0

        if is_event and not has_text_date:
            # DD-036 update: nunca se llamó a Capa 3 — se sabe de antemano que
            # should_create es False por falta de fecha real en el texto.
            decision_label = "no evento (sin fecha en texto, LLM omitido)"
        elif is_event and category in NULL_CATS:
            # DD-039: Capa 3 SÍ corrió y tipificó el post como no-cultural
            # (category="nulo") — distinto de "rechazado por LLM" (que
            # significa que is_public_invitation/is_upcoming vinieron en
            # False). Acá el LLM puede haber dicho is_public_invitation=True
            # y aun así el post no califica, porque no es un evento cultural.
            decision_label = "no evento (categoría nula, DD-039)"
        elif is_event and llm_ran_ok and not should_create:
            decision_label = "no evento (rechazado por LLM)"
        elif is_event:
            decision_label = "EVENTO" if should_create else "no evento"
        else:
            decision_label = "no evento"

        record = {
            "post_id":     post.get("id", ""),
            "caption":     post["caption"],
            "author":      post.get("author", ""),
            "layer1":      l1,
            "layer2":      det_score,     # score de detección 2a
            "hotness":     hotness,
            "event_score": event_score,
            "category":    category,
            "decision":    decision_label,
            "loc_name":    loc_name or "",
            "raw_date":    event_date or "",
            "top3":        [(top_label, 1.0)] if top_label else [],  # compat diagnóstico
            "is_public_invitation": is_public_invitation,
            "is_upcoming":          is_upcoming,
            "clean_description":    clean_description or "",
            "title":                llm_title or "",
            "description_fr":       llm_description_fr or "",
            "title_fr":             llm_title_fr or "",
            "price_range":          llm_price_range or "",
            "city":                 llm_city or "",
            "exact_address":        llm_exact_address or "",
            "art_tags":             ", ".join(llm_art_tags) if llm_art_tags else "",
            "art_tags_fr":          ", ".join(llm_art_tags_fr) if llm_art_tags_fr else "",
        }
        diag_all.append(record)
        diag_cands.append(record)

        if should_create:
            dry_counts[category] += 1

        if not is_event:
            skipped_l2 += 1
            processed_ids.append(post["id"])
            since_last_mark += 1
            continue

        if is_event and not has_text_date:
            skipped_no_text_date += 1
            processed_ids.append(post["id"])
            since_last_mark += 1
            continue

        if is_event and not should_create:
            skipped_llm_gate += 1
            processed_ids.append(post["id"])
            since_last_mark += 1
            continue

        if dry_run:
            processed_ids.append(post["id"])
            continue

        type_for_id = top_label or "evento cultural"
        emb_text  = f"{post['caption'][:200]} {type_for_id} {event_date or ''} {loc_name or ''}"
        event_emb = st_model.encode([emb_text], normalize_embeddings=True, show_progress_bar=False)[0].tolist()
        # FIX make_event_id usa event_date ISO, no raw_date
        event_id  = make_event_id(type_for_id, event_date or "", loc_name or "")

        candidate = {
            "id":           event_id,
            # Título editorial del LLM si existe; si no (LLM falló y aun así
            # se creó por resiliencia) cae a un genérico.
            "title":        llm_title or type_for_id.title(),
            "type":         type_for_id,
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
            # DD-038: createdAt ya no se genera acá — se pasó a datetime()
            # nativo de Cypher en upsert_event (server-side), igual que
            # firstSeenAt en 2_build_graph.py. Antes se guardaba como string
            # ISO (datetime.now().isoformat()), lo que rompía en silencio
            # cualquier comparación temporal nativa en Cypher (WHERE
            # e.createdAt >= datetime() - duration(...) evalúa a null contra
            # un string, sin error, sin filas — así se perdió el rastro de
            # esta misma corrida al intentar auditarla).
            # Capa 3 (LLM) — description/flags/reasoning; source* solo se
            # fijan al crear el evento, nunca se sobreescriben al fusionar
            # (representan la publicación ORIGINAL, ver DD-033).
            "description":        clean_description or "",
            # Traducción al francés (2026-08-24, DD-051) — mismo criterio que
            # description/title de arriba: solo se fijan al CREAR el evento,
            # nunca se sobreescriben al fusionar (ver upsert_event). Eventos
            # creados antes de este cambio simplemente no tienen estas dos
            # propiedades — el sitio cae al español si faltan (sin backfill).
            "titleFr":             llm_title_fr or "",
            "descriptionFr":       llm_description_fr or "",
            "isPublicInvitation":  is_public_invitation,
            "isUpcoming":          is_upcoming,
            "priceRange":          llm_price_range,
            # DD-042: eventArtTags es NUEVO y distinto de artType (abajo) —
            # artType es heredado de la :Account curada a mano (un solo string
            # libre, describe qué hace la cuenta en general, ej. una cuenta de
            # sede multiuso trae "Música, Danza, Circo, Teatro, Artes visuales"
            # pegados). eventArtTags lo genera el LLM por EVENTO puntual, es
            # una lista corta (máx 3) de tags sin comas/paréntesis dentro de
            # cada uno — pensado como filtro de menú confiable, sin el
            # problema de parseo que tiene el artType de cuenta.
            "eventArtTags":        llm_art_tags,
            # Traducción al francés de eventArtTags, mismo criterio que
            # titleFr/descriptionFr arriba: creation-only, sin backfill acá
            # (ver backfill_art_tags_fr.py para el vocabulario ya existente,
            # DD-054). Alineado por posición con eventArtTags.
            "eventArtTagsFr":      llm_art_tags_fr,
            "llmReasoning":        llm_reasoning or "",
            "sourcePostUrl":       post.get("url"),
            # DD-057: la foto real del post (Apify ya la trae como
            # p.displayUrl desde 2_build_graph.py, nunca había llegado hasta
            # acá). Creation-only como el resto de estos campos "source*" --
            # es la imagen de la publicación ORIGINAL que generó el evento,
            # no algo que tenga sentido pisar al fusionar con un post nuevo.
            # OJO: son URLs firmadas de la CDN de Instagram, pueden expirar
            # con el tiempo -- el frontend tiene que degradar con gracia si
            # la carga falla, no asumir que siempre van a servir.
            "imageUrl":            post.get("displayUrl") or "",
            "sourceAuthor":        post.get("author"),
            "sourcePostDate":      post.get("timestamp"),
            # Heredados de :Account (curación manual) — sin costo LLM, se
            # copian tal cual al crear el evento. Solo existen si la cuenta
            # pasó por load_manual_account_categorization.py.
            "artType":            post.get("artType"),
            "institutionType":    post.get("institutionType"),
            "culturalIdentity":   post.get("culturalIdentity"),
            "geoZone":            post.get("geoZone"),
            "parentInstitution":  post.get("parentInstitution"),
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

    # ── Export CSV de diagnóstico (--diag-csv) ───────────────────────────────
    # Los prints de consola de abajo solo muestran ejemplos (top-5 falsos
    # negativos, 10 al azar) — para comparar sistemáticamente estas
    # decisiones contra una clasificación externa (p.ej. hecha por un LLM
    # aparte sobre el mismo lote) hace falta el listado COMPLETO en forma
    # estructurada. diag_all incluye tanto los descartados en Capa 1 como
    # los candidatos que llegaron a Capa 2/3.
    # DD-038: antes exigía dry_run — en una corrida real, --diag-csv se
    # ignoraba en silencio (sin error, sin aviso) y no quedaba ningún
    # rastro auditable de qué se rechazó y por qué. diag_all/diag_cands se
    # llenan igual en modo real (no dependen de dry_run en ningún punto de
    # arriba), así que exportar acá no tiene ningún costo ni riesgo nuevo.
    if diag_csv:
        fieldnames = [
            "post_id", "author", "decision", "category", "layer1", "layer2",
            "event_score", "hotness", "loc_name", "raw_date", "top3",
            "is_public_invitation", "is_upcoming", "clean_description",
            "title", "price_range", "city", "exact_address", "art_tags", "art_tags_fr", "caption",
        ]
        with open(diag_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in diag_all:
                row = dict(r)
                row["top3"] = "; ".join(f"{lbl}:{sc:.3f}" for lbl, sc in row.get("top3", []))
                writer.writerow(row)
        print(f"\n  💾 Diagnóstico completo exportado a {diag_csv} ({len(diag_all)} posts)")

    # ── Resumen ───────────────────────────────────────────────────────────────
    detected = [r for r in diag_cands if r["decision"] == "EVENTO"]
    print(f"\n{'═'*60}")
    print(f"  ⏱️  Tiempo total        : {time.time() - t_start:.1f}s")
    print(f"  ✅ Posts procesados    : {len(posts)}")
    print(f"  🔵 Descartados Capa 1  : {skipped_l1}  (L1<{layer1_threshold})")
    print(f"  🟠 Candidatos Capa 2   : {len(cand_posts)}")
    print(f"  ⏭️  Descartados Capa 2  : {skipped_l2}")
    print(f"  📅 Sin fecha en texto  : {skipped_no_text_date}  (LLM omitido del todo, DD-036 — ahorro directo de tokens)")
    print(f"  🚫 Rechazados por LLM  : {skipped_llm_gate}  (Capa 3 dijo que no es invitación pública futura)")
    print(f"  💰 Llamadas a Capa 3   : {skipped_llm_gate + len(detected)}  (vs. {skipped_llm_gate + skipped_no_text_date + len(detected)} sin el gate previo)")
    print(f"  🗓️  Fechas clampeadas   : {dates_clamped}  (>{EVENT_DATE_CLAMP_DAYS}d del post)")
    print(f"  🎭 Eventos detectados  : {len(detected)}")
    if not dry_run:
        print(f"  🆕 Eventos creados     : {created}")
        print(f"  🔄 Eventos enriquecidos: {enriched}")
    if dry_counts:
        print("\n  Por categoría:")
        for cat, n in sorted(dry_counts.items(), key=lambda x: -x[1]):
            print(f"    {n:>4}  {cat}")

    # Cuántos quedan pendientes tras esta corrida: misma query de candidatos
    # (mismos filtros de edad/origen/cuenta), sin SKIP/LIMIT, contando en vez
    # de traer filas. En modo real esto ya refleja el eventExtracted=true que
    # se acaba de marcar en este batch (se corre después de esas escrituras).
    with driver.session() as session:
        pending = session.run(f"""
            MATCH (a:Account)-[:PUBLISHED]->(p:Post)
            WHERE p.caption IS NOT NULL
              AND size(p.caption) >= {MIN_CAPTION_LEN}
              AND (p.eventExtracted IS NULL OR p.eventExtracted = false)
              {age_filter}
              {source_filter}
              {account_filter}
            RETURN count(p) AS n
        """, accounts=accounts or [], cutoffDate=cutoff_date_str).single()["n"]
    print(f"  ⏳ Posts pendientes aún (mismo filtro, sin contar este batch): {pending}")

    if not diag_cands:
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
    skip_posts: int = typer.Option(
        0, "--skip",
        help="Salta los primeros N posts pendientes antes de aplicar --max-posts — útil en --dry-run para pedir un lote NUEVO (dry-run nunca marca eventExtracted, así que sin --skip siempre se repite el mismo lote).",
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
    diag_csv: Optional[str] = typer.Option(
        None, "--diag-csv",
        help="Solo con --dry-run: exporta el diagnóstico completo (todos los posts, no solo ejemplos) a este CSV.",
    ),
    max_post_age_days: int = typer.Option(
        20, "--max-post-age-days",
        help="Ignora posts con p.timestamp más viejo que N días (0 = sin filtro). Existe porque los posts "
             "embebidos en profile_<username>.json (latestPosts) NUNCA pasan por la ventana onlyPostsNewerThan "
             "verificada del scraper dedicado de posts — pueden ser arbitrariamente viejos. Default 20 (un poco "
             "más laxo que los 10 días de --days en 1_harvest_ig_posts.py, de margen).",
    ),
    only_dedicated_scraper: bool = typer.Option(
        True, "--only-dedicated-scraper/--include-profile-embed",
        help="Por defecto solo procesa posts con p.sourceDedicatedScraper=true (vinieron de "
             "1_harvest_ig_posts.py, con ventana de días verificada). --include-profile-embed también "
             "admite posts que solo llegaron embebidos en el perfil (latestPosts), sin esa garantía.",
    ),
):
    """
    Fase 4-B: extracción de eventos en 3 capas.

    Capa 1: sentence-transformers filtra candidatos por similitud coseno
    máxima contra ~100 frases de referencia (--threshold).

    Capa 2a: NLI multilingüe ligero batcheado — detección binaria
             (--layer2-threshold), hipótesis en el idioma del caption.
    Capa 3:  Ollama local (modelo configurable vía OLLAMA_MODEL, default
             qwen2.5:7b) por defecto — o un proveedor cloud si LLM_PROVIDER
             es "groq" (GROQ_API_KEY), "google" (GOOGLE_API_KEY), "deepseek"
             (DEEPSEEK_API_KEY) o "cerebras" (CEREBRAS_API_KEY). Fallback
             automático entre los cuatro en ese orden si el preferido falla.
             Limpia fecha/ubicación, TIPIFICA
             el evento (reemplaza la vieja Capa 2b de embeddings), da
             price_range, redacta descripción y filtra noticias
             institucionales sin invitación real — si el LLM corrió y dice
             que no es invitación pública futura, el evento no se crea.
             Solo corre sobre los positivos de 2a.

    eventScore = (layer2_score × 0.6 + hotness_norm × 0.4) × category_penalty × llm_penalty
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    accounts_list = [a.strip() for a in accounts.split(",")] if accounts else None

    run_extraction(
        layer1_threshold=threshold,
        layer2_threshold=layer2_threshold,
        max_posts=max_posts,
        skip_posts=skip_posts,
        batch_size=batch_size,
        date_window=date_window,
        sim_threshold=sim_threshold,
        dry_run=dry_run,
        accounts=accounts_list,
        diag_csv=diag_csv,
        max_post_age_days=max_post_age_days,
        only_dedicated_scraper=only_dedicated_scraper,
    )

    driver.close()
    print("\n✅ Extracción de eventos completa.")


if __name__ == "__main__":
    app()
