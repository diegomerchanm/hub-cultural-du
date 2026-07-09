"""
Fase 1-B — Clasificador de cuentas Instagram: geografía × cultura.

Dos dimensiones simultáneas:
  geography_score (0-1)  ¿La cuenta está radicada o activa en Francia?
  cultural_score  (0-1)  ¿Publica cultura/comunidad/arte/eventos de la diáspora?

  final_score = 0.5 * geography_score + 0.5 * cultural_score
  keep        = final_score >= 0.6   (organizaciones/negocios)
              = final_score >= 0.75  (individuos — barra más alta, filtra
                                      influencers sin conexión comunitaria)

Señales:
  - Embeddings semánticos (paraphrase-multilingual-MiniLM-L12-v2) contra
    frases de referencia positivas Y negativas (anti-embeddings):
        cultural_score = sim_positiva - sim_negativa * 0.3
  - businessCategoryName → tier (config/account_tiers.json) como señal directa
  - businessAddress, locationName de posts, hashtags geográficos
  - Cuentas políticas: POLITICAL_ACCOUNTS leída de run_gds_algorithms.py (DD-012)
  - Seeds V2 (config/seeds_v2.json, DD-022): rol 'seed_source' — fuente de
    descubrimiento de red, NO objetivo cultural. Bloque A (diplomáticas):
    keep=False siempre. Bloque B (instituciones culturales): scores deciden.
  - Para cuentas SIN perfil scrapeado: heurísticas + embedding del username

Columna `role`: seed_source | target (keep=True) | context (keep=False)

Input:   data_raw/profile_*.json · data_processed/nodes.csv · config/account_tiers.json
Output:  data_processed/account_scores.csv (+ propiedades en Neo4j con --write-neo4j)

Validación:  python 1_harvest_account_classifier.py --diagnose
Neo4j:       python 1_harvest_account_classifier.py --write-neo4j
             (opcional — el puerto 7687 suele estar bloqueado, ver DD-013)
"""

import csv
import glob
import json
import os
import random
import re
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np
import typer
from dotenv import load_dotenv

# ── 1. Rutas y constantes ─────────────────────────────────────────────────────
DATA_RAW       = Path("data_raw")
DATA_PROCESSED = Path("data_processed")
TIERS_FILE     = Path("config/account_tiers.json")
SEEDS_FILE     = Path("config/seeds_v2.json")
OUTPUT_CSV     = DATA_PROCESSED / "account_scores.csv"

MODEL_NAME       = "paraphrase-multilingual-MiniLM-L12-v2"
NEG_WEIGHT       = 0.30   # peso de los anti-embeddings
THRESHOLD_ORG    = 0.60   # organizaciones / negocios
THRESHOLD_PERSON = 0.75   # individuos (barra más alta)
USERNAME_CONF    = 0.60   # factor de confianza para señales solo-username

# Calibración de similitud coseno → score [0,1].
# MiniLM produce cosenos ~0.20 para texto no relacionado y ~0.60+ para
# texto claramente afín; el rescale evita que el ruido de fondo puntúe.
SIM_LO, SIM_HI = 0.25, 0.62

FRENCH_CITIES = [
    "paris", "lyon", "marseille", "bordeaux", "strasbourg",
    "nantes", "toulouse", "lille", "montpellier", "nice",
    "rennes", "grenoble", "saint-denis", "montreuil", "aubervilliers",
]
FRANCE_MARKERS = FRENCH_CITIES + [
    "france", "francia", "ile-de-france", "île-de-france",
    "hexagone", "hexagono", "hexágono",
]
GEO_HASHTAGS = {
    "paris", "parigi", "france", "francia", "parisfrance",
    "parislatino", "latinosenparis", "latinosenfrancia",
    "colombianosenparis", "colombianosenfrancia", "latinoamericanosenfrancia",
    "iledefrance", "parisjetaime", "vivreaparis", "parismaville",
}
# tokens en username/fullName (cuentas sin perfil scrapeado)
USERNAME_GEO_TOKENS = FRANCE_MARKERS + ["fr", "paname"]
USERNAME_CULT_TOKENS = [
    "salsa", "cumbia", "vallenato", "bachata", "tango", "folclor", "folklor",
    "arte", "art", "arts", "gallery", "galeria", "galerie", "atelier",
    "musica", "music", "musique", "danza", "dance", "danse", "baile",
    "teatro", "theatre", "cine", "cinema", "film", "foto", "photo",
    "cultura", "cultural", "culture", "colectivo", "collectif", "asociacion",
    "association", "comunidad", "community", "festival", "fiesta", "evento",
    "arepa", "empanada", "cafe", "cocina", "gastronomia", "resto",
    "podcast", "radio", "libro", "libreria", "tertulia",
]
USERNAME_LATAM_TOKENS = [
    "colombia", "colombiano", "colombiana", "latino", "latina", "latam",
    "latinoamerica", "bogota", "medellin", "cali", "barranquilla",
    "mexico", "mexicano", "peru", "peruano", "chile", "chileno",
    "argentina", "argentino", "venezuela", "venezolano",
    "ecuador", "ecuatoriano", "brasil", "brazil", "brasileiro", "brasileira",
    "cuba", "cubano", "cubana", "uruguay", "uruguayo", "panama",
    "guatemala", "honduras", "dominicana", "dominicano", "quisqueya",
    "bolivia", "boliviano", "costarica", "tico", "salvador", "nicaragua",
    "paraguay", "caribe", "andino", "sudamerica", "suramerica",
]
USERNAME_NEG_TOKENS = [
    "invest", "inmobiliaria", "immobilier", "realestate", "finance",
    "credit", "credito", "remesas", "envios", "cambio", "divisas",
    "seguros", "insurance", "trading", "crypto", "casino", "apuestas",
    "gobierno", "gov", "oficial", "senado", "alcaldia", "registraduria",
]

# ── 2. Frases de referencia (embeddings) ──────────────────────────────────────
# El concepto importa más que las palabras: se cubren ES/FR/EN, registro
# formal y coloquial de la diáspora ("el hexágono", "la ciudad luz",
# "entre el Sena y el Magdalena"), y varias formas de expresar presencia.

GEO_REFERENCES = [
    # — residencia declarada
    "vivo en París", "radicada en Francia", "radicado en París hace años",
    "instalada en Lyon", "viviendo en Marsella", "resido en Burdeos",
    "based in Paris", "living in France", "installé à Paris",
    "basée à Paris", "je vis en France", "vivo en el hexágono",
    # — coloquial / poético de la diáspora
    "el hexágono es mi nueva casa", "en tierras galas",
    "la ciudad luz me adoptó", "entre el Sena y el Magdalena",
    "del trópico a la ciudad luz", "de Bogotá a París",
    "de Colombia para Francia", "una paisa en París",
    "colombiana perdida en París", "latinos por las calles de Paname",
    "mi corazón entre dos orillas, Colombia y Francia",
    "aterricé en Francia con una maleta y muchos sueños",
    # — comunidad / actividad local
    "colombianos en Francia", "latinos en París",
    "comunidad latinoamericana en Francia",
    "eventos latinos en París", "agenda cultural latina en Francia",
    "encuéntranos en el barrio latino de París",
    "nos vemos en el 11ème arrondissement",
    "au cœur de Paris", "dans le Marais", "sur les bords de la Seine",
    "rendez-vous à Belleville", "métro, boulot, salsa à Paris",
    "la diáspora colombiana en la región parisina",
    # — pan-latino / portugués (seeds V2, DD-022)
    "moro em Paris", "brasileiros na França", "vivendo na França",
    "latinos na cidade luz", "mexicanos en Francia", "peruanos en París",
    "argentinos en Francia", "la comunidad latina del hexágono",
]

CULTURAL_REFERENCES = [
    # — arte y colectivos
    "colectivo de artistas latinoamericanos",
    "exposición de arte contemporáneo latinoamericano",
    "galería de arte y residencias para artistas migrantes",
    "artiste plasticienne colombienne", "atelier ouvert au public",
    "muralismo y arte urbano de la diáspora",
    "Latin American artists collective exhibition",
    # — música y baile
    "noche de salsa y cumbia", "clases de baile latino",
    "concierto de música andina", "orquesta de salsa en vivo",
    "soirée latino avec DJ et orchestre", "milonga y tango al aire libre",
    "toque de vallenato con acordeón", "fiesta latina este sábado",
    "live Latin music and dancing",
    # — eventos y comunidad
    "festival de cine colombiano", "ciclo de cine latinoamericano",
    "encuentro de la comunidad colombiana", "asociación cultural de la diáspora",
    "tertulia literaria en español", "club de lectura latinoamericano",
    "feria de artesanías colombianas", "mercado navideño latino",
    "célébration de la fête nationale colombienne",
    "talleres culturales para niños de la diáspora",
    "red de apoyo entre migrantes latinoamericanos",
    "community event for the Latin American diaspora",
    # — gastronomía cultural
    "arepas y empanadas caseras", "sabores de Colombia",
    "cocina tradicional colombiana", "restaurant colombien traditionnel",
    "café de origen colombiano", "brunch latino con música en vivo",
    "gastronomía como memoria del territorio",
    # — patrimonio e identidad
    "memoria e identidad migrante", "raíces afrocolombianas",
    "patrimonio cultural inmaterial", "lenguas y saberes indígenas",
    "el folclor colombiano viaja por el mundo",
    # — pan-latino / portugués (seeds V2, DD-022)
    "roda de samba e forró", "feijoada da comunidade brasileira",
    "festa junina em Paris", "capoeira e cultura afro-brasileira",
    "noche de mariachi y ranchera", "altar de día de muertos",
    "ceviche y pisco, sabores del Perú", "peña folclórica andina",
    "son cubano y rumba en vivo", "asado argentino entre amigos",
    "carnaval latinoamericano en las calles", "candombe y murga uruguaya",
    "cine mexicano contemporáneo", "literatura latinoamericana en traducción",
]

# Anti-embeddings — el concepto de lo que NO queremos.
NEGATIVE_REFERENCES = [
    # — comercial / financiero
    "envía dinero a tu familia con las mejores tasas",
    "remesas rápidas y seguras a Colombia",
    "invierte en finca raíz desde el exterior",
    "crédito hipotecario para colombianos en el extranjero",
    "asesoría financiera y seguros de vida",
    "compra y venta de divisas al mejor cambio",
    "trading y criptomonedas, resultados garantizados",
    "gana dinero desde casa con nuestro método",
    "investissement immobilier rentable", "agence immobilière",
    "best exchange rates, send money now",
    "franquicias y oportunidades de negocio",
    # — político / electoral / gubernamental
    "vota por nuestro candidato a la presidencia",
    "campaña electoral, únete al cambio",
    "partido político oficial", "jornada de votación en el consulado",
    "comunicado oficial del gobierno",
    "trámites de la registraduría y cédulas",
    "el senador anunció su nueva propuesta de ley",
    "debate presidencial en vivo",
    # — influencer sin conexión comunitaria
    "código de descuento en mi biografía",
    "gran sorteo, sigue, comenta y etiqueta a tres amigos",
    "outfit del día, link en mi bio",
    "rutina fitness para quemar grasa",
    "coach de vida y mentalidad de abundancia",
    "unboxing y reseña de productos patrocinados",
    "colaboraciones y publicidad por interno",
    "daily vlog, like and subscribe",
    "tips de belleza y skincare patrocinado",
]

# ── 3. Helpers ────────────────────────────────────────────────────────────────
def _norm(text: str) -> str:
    """minúsculas + sin acentos, para matching robusto."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _rescale(sim: float, lo: float = None, hi: float = None) -> float:
    """Coseno crudo → [0,1] calibrado (ruido de fondo ≈ 0)."""
    lo = SIM_LO if lo is None else lo
    hi = SIM_HI if hi is None else hi
    return float(min(1.0, max(0.0, (sim - lo) / (hi - lo))))


def _noisy_or(signals: list) -> float:
    """Combina evidencias positivas independientes: 1 - Π(1-s)."""
    p = 1.0
    for s in signals:
        p *= 1.0 - min(1.0, max(0.0, s))
    return 1.0 - p


def _top_k_mean(sims: np.ndarray, k: int = 5) -> float:
    """Media de las k similitudes más altas — robusta a outliers."""
    if sims.size == 0:
        return 0.0
    k = min(k, sims.size)
    return float(np.sort(sims)[-k:].mean())


def _split_username(username: str) -> str:
    """'colombianos_en.paris' → 'colombianos en paris' (texto embebible)."""
    s = re.sub(r"[._\-\d]+", " ", username)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return s.strip()


def _tokens_in(text: str, tokens: list) -> list:
    """Tokens presentes como palabra/subcadena delimitada en el texto normalizado."""
    t = _norm(text)
    found = []
    for tok in tokens:
        if re.search(rf"(?:^|[^a-z]){re.escape(_norm(tok))}(?:[^a-z]|$)", t):
            found.append(tok)
    return found


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── 4. Carga de datos ─────────────────────────────────────────────────────────
def load_tiers() -> dict:
    with open(TIERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_political_accounts() -> set:
    """
    Lee POLITICAL_ACCOUNTS desde run_gds_algorithms.py (fuente única de
    verdad, DD-012). nodes.csv no siempre refleja la marca :Political.
    """
    try:
        src = Path("run_gds_algorithms.py").read_text(encoding="utf-8")
        m = re.search(r"POLITICAL_ACCOUNTS\s*=\s*\[(.*?)\]", src, re.DOTALL)
        if m:
            return set(re.findall(r'"([^"]+)"', m.group(1)))
    except OSError:
        pass
    print("⚠️  No se pudo leer POLITICAL_ACCOUNTS de run_gds_algorithms.py")
    return set()


def load_seeds() -> dict:
    """
    handle → {tipo, bloque} desde config/seeds_v2.json (DD-022).
    Bloque A (diplomáticas): fuente de descubrimiento, nunca objetivo cultural.
    Bloque B (instituciones culturales): fuente + posible objetivo (scores deciden).
    """
    if not SEEDS_FILE.exists():
        return {}
    try:
        with open(SEEDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {s["handle"]: s for s in data.get("seeds", []) if s.get("handle")}
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  No se pudo leer {SEEDS_FILE}: {e}")
        return {}


def category_to_tier(category: Optional[str], username: str, tiers: dict) -> str:
    if username in tiers.get("manual_overrides", {}):
        return tiers["manual_overrides"][username]
    if not category or category == "None":
        return "unknown"
    for tier in ("excluded", "primary", "secondary"):
        if category in tiers.get(tier, []):
            return tier
    return "unknown"


def load_profiles() -> dict:
    """username → dict del perfil scrapeado."""
    profiles = {}
    for fp in sorted(glob.glob(str(DATA_RAW / "profile_*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(d, list):
            d = d[0] if d else {}
        if d.get("username"):
            profiles[d["username"]] = d
    return profiles


def load_nodes() -> list:
    with open(DATA_PROCESSED / "nodes.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── 5. Señales geográficas deterministas ──────────────────────────────────────
def geo_hard_signals(profile: dict) -> tuple:
    """→ (lista de señales [0,1], lista de razones)."""
    signals, reasons = [], []

    # businessAddress
    addr = profile.get("businessAddress") or ""
    if isinstance(addr, str) and addr.startswith("{"):
        try:
            addr = json.loads(addr)
        except json.JSONDecodeError:
            addr = {"raw": addr}
    if isinstance(addr, dict):
        addr = " ".join(str(v) for v in addr.values() if v)
    if addr and _tokens_in(str(addr), FRANCE_MARKERS):
        signals.append(0.95)
        reasons.append("addr:FR")

    # bio con ciudad/país francés
    bio_hits = _tokens_in(profile.get("biography") or "", FRANCE_MARKERS)
    if bio_hits:
        signals.append(0.85)
        reasons.append(f"bio:{','.join(bio_hits[:3])}")

    # locationName de posts
    posts = profile.get("latestPosts") or []
    geotagged = [p for p in posts if p.get("locationName")]
    fr_posts = [p for p in geotagged
                if _tokens_in(p["locationName"], FRANCE_MARKERS)]
    if geotagged:
        frac = len(fr_posts) / len(geotagged)
        if fr_posts:
            signals.append(0.40 + 0.50 * frac)
            reasons.append(f"posts:{len(fr_posts)}/{len(geotagged)}FR")
        elif len(geotagged) >= 3:
            reasons.append("posts:0FR")  # evidencia de ausencia (no suma)

    # hashtags geográficos
    tags = {_norm(h) for p in posts for h in (p.get("hashtags") or [])}
    geo_tags = tags & GEO_HASHTAGS
    if geo_tags:
        signals.append(0.50)
        reasons.append(f"tags:{','.join(sorted(geo_tags)[:3])}")

    return signals, reasons


# ── 5b. Completitud de datos (diagnóstico, DD-027) ────────────────────────────
# Cuenta cuántos de 5 campos están presentes: fullName, followers, public,
# verified, profilePicUrl. Solo diagnóstico — no modula geography/cultural/keep.
COMPLETENESS_FIELDS = 5


def _profile_completeness(profile: dict) -> float:
    """Cuenta con perfil scrapeado (data_raw/profile_*.json)."""
    present = [
        bool((profile.get("fullName") or "").strip()),
        _to_float(profile.get("followersCount")) > 0,
        profile.get("private") is not None,
        profile.get("verified") is not None,
        bool((profile.get("profilePicUrl") or "").strip()),
    ]
    return round(sum(present) / COMPLETENESS_FIELDS, 2)


def _node_completeness(row: dict) -> float:
    """Cuenta sin perfil scrapeado — solo lo que llegó a nodes.csv."""
    present = [
        bool((row.get("fullName") or "").strip()),
        _to_float(row.get("followers")) > 0,
        (row.get("public") or "").strip() != "",
        (row.get("verified") or "").strip() != "",
        bool((row.get("profilePicUrl") or "").strip()),
    ]
    return round(sum(present) / COMPLETENESS_FIELDS, 2)


# ── 6. Clasificador ───────────────────────────────────────────────────────────
class AccountClassifier:
    def __init__(self, neg_weight: float = NEG_WEIGHT, max_posts: int = 10):
        self.neg_weight = neg_weight
        self.max_posts = max_posts
        print(f"📦 Cargando modelo: {MODEL_NAME}")
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(MODEL_NAME)
        print("🧠 Codificando frases de referencia "
              f"(geo={len(GEO_REFERENCES)}, cult={len(CULTURAL_REFERENCES)}, "
              f"neg={len(NEGATIVE_REFERENCES)})")
        self.geo_refs = self._encode(GEO_REFERENCES)
        self.pos_refs = self._encode(CULTURAL_REFERENCES)
        self.neg_refs = self._encode(NEGATIVE_REFERENCES)
        self.political_accounts = load_political_accounts()
        self.seeds = load_seeds()
        if self.seeds:
            n_a = sum(1 for s in self.seeds.values() if s.get("bloque") == "A")
            print(f"🌱 Seeds V2: {len(self.seeds)} ({n_a} bloque A, "
                  f"{len(self.seeds) - n_a} bloque B)")

    def _encode(self, texts: list) -> np.ndarray:
        emb = np.asarray(self.model.encode(texts, show_progress_bar=False))
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return emb / norms

    def _sims(self, texts: list, refs: np.ndarray) -> float:
        """Máximo (sobre textos) de la media top-5 de similitudes con refs."""
        if not texts:
            return 0.0
        emb = self._encode([t[:1500] for t in texts])
        sim_matrix = emb @ refs.T          # (n_texts, n_refs) — normalizados
        return max(_top_k_mean(row) for row in sim_matrix)

    # ---- cuentas con perfil scrapeado ----------------------------------------
    def score_profile(self, profile: dict, tier: str) -> dict:
        username = profile["username"]
        bio = (profile.get("biography") or "").strip()
        posts = profile.get("latestPosts") or []
        captions = [p["caption"].strip() for p in posts[: self.max_posts]
                    if p.get("caption")]
        texts = ([bio] if bio else []) + captions
        reasons = []

        # — geography_score: reglas duras + capa semántica (noisy-OR)
        hard, hard_reasons = geo_hard_signals(profile)
        reasons += hard_reasons
        sem_geo = _rescale(self._sims(texts, self.geo_refs))
        if sem_geo > 0.15:
            reasons.append(f"sem_geo:{sem_geo:.2f}")
        geography = _noisy_or(hard + [sem_geo * 0.75])

        # — cultural_score: sim_positiva - sim_negativa*0.3 + señal de tier
        sim_pos = _rescale(self._sims(texts, self.pos_refs))
        sim_neg = _rescale(self._sims(texts, self.neg_refs))
        cultural = sim_pos - sim_neg * self.neg_weight
        reasons.append(f"sim+:{sim_pos:.2f}")
        if sim_neg > 0.15:
            reasons.append(f"sim-:{sim_neg:.2f}")
        if tier == "primary":
            cultural += 0.25
            reasons.append("tier:primary(+0.25)")
        elif tier == "secondary":
            cultural += 0.10
            reasons.append("tier:secondary(+0.10)")
        cultural = min(1.0, max(0.0, cultural))

        is_person = (not profile.get("isBusinessAccount")
                     and not profile.get("businessCategoryName"))
        return self._finalize(username, geography, cultural, tier,
                              is_person, reasons, has_profile=True,
                              political=username in self.political_accounts,
                              data_completeness=_profile_completeness(profile))

    # ---- cuentas SOLO con username/fullName ----------------------------------
    def score_username_batch(self, rows: list) -> list:
        """rows: dicts de nodes.csv sin perfil. Embedding + heurísticas."""
        texts, metas = [], []
        for r in rows:
            pseudo = f"{_split_username(r['username'])} {r.get('fullName') or ''}".strip()
            texts.append(pseudo if pseudo else r["username"])
            metas.append(r)

        emb = self._encode(texts)
        sims_geo = emb @ self.geo_refs.T
        sims_pos = emb @ self.pos_refs.T
        sims_neg = emb @ self.neg_refs.T

        results = []
        for i, r in enumerate(metas):
            username = r["username"]
            raw = f"{username} {r.get('fullName') or ''}"
            reasons = ["username-only"]

            geo_tok   = _tokens_in(raw, USERNAME_GEO_TOKENS)
            cult_tok  = _tokens_in(raw, USERNAME_CULT_TOKENS)
            latam_tok = _tokens_in(raw, USERNAME_LATAM_TOKENS)
            neg_tok   = _tokens_in(raw, USERNAME_NEG_TOKENS)

            geo_signals = []
            if geo_tok:
                geo_signals.append(0.55)
                reasons.append(f"u_geo:{','.join(geo_tok[:2])}")
            if geo_tok and latam_tok:   # "colombianosenparis" — señal fuerte
                geo_signals.append(0.35)
                reasons.append(f"u_latam:{','.join(latam_tok[:2])}")
            sem_geo = _rescale(_top_k_mean(sims_geo[i], k=3))
            geo_signals.append(sem_geo * 0.75 * USERNAME_CONF)
            geography = _noisy_or(geo_signals)

            sim_pos = _rescale(_top_k_mean(sims_pos[i], k=3)) * USERNAME_CONF
            sim_neg = _rescale(_top_k_mean(sims_neg[i], k=3))
            cultural = sim_pos - sim_neg * self.neg_weight
            if cult_tok:
                cultural += 0.30
                reasons.append(f"u_cult:{','.join(cult_tok[:2])}")
            if neg_tok:
                cultural -= 0.30
                reasons.append(f"u_neg:{','.join(neg_tok[:2])}")
            cultural = min(1.0, max(0.0, cultural))

            tier = r.get("tier") or "unknown"
            political = (r.get("political") == "True"
                         or username in self.political_accounts)
            results.append(self._finalize(username, geography, cultural, tier,
                                          is_person=True, reasons=reasons,
                                          has_profile=False,
                                          political=political,
                                          data_completeness=_node_completeness(r)))
        return results

    # ---- decisión final -------------------------------------------------------
    def _finalize(self, username, geography, cultural, tier, is_person,
                  reasons, has_profile, political=False,
                  data_completeness=0.0) -> dict:
        final = 0.5 * geography + 0.5 * cultural
        threshold = THRESHOLD_PERSON if is_person else THRESHOLD_ORG
        keep = final >= threshold

        if political:
            keep = False
            reasons.append("política(DD-012)")
        if tier == "excluded":
            keep = False
            reasons.append("tier:excluded")

        # Seeds V2 (DD-022): fuente de descubrimiento ≠ objetivo cultural.
        # Bloque A (diplomáticas): keep=False siempre. Bloque B: scores deciden.
        seed = self.seeds.get(username)
        if seed:
            if seed.get("bloque") == "A":
                keep = False
                reasons.append("seed_A:institucional(DD-022)")
            else:
                reasons.append("seed_B(DD-022)")
        role = "seed_source" if seed else ("target" if keep else "context")

        return {
            "username": username,
            "geography_score": round(geography, 4),
            "cultural_score": round(cultural, 4),
            "final_score": round(final, 4),
            "tier": tier,
            "keep": keep,
            "reason": "; ".join(reasons),
            "role": role,
            "has_profile": has_profile,
            "kind": "person" if is_person else "org",
            "data_completeness": data_completeness,
        }


# ── 7. Diagnóstico ────────────────────────────────────────────────────────────
def _fmt_row(r: dict) -> str:
    return (f"  {r['username']:<32} geo={r['geography_score']:.2f} "
            f"cult={r['cultural_score']:.2f} final={r['final_score']:.2f} "
            f"{'KEEP' if r['keep'] else 'drop'}  [{r['tier']}]\n"
            f"      └─ {r['reason'][:110]}")


def diagnose(results: list):
    rng = random.Random(42)
    print("\n" + "=" * 78)
    print("🔬 DIAGNÓSTICO DEL CLASIFICADOR")
    print("=" * 78)

    n_keep = sum(1 for r in results if r["keep"])
    n_prof = sum(1 for r in results if r["has_profile"])
    print(f"\nTotal: {len(results)} cuentas · con perfil: {n_prof} · "
          f"keep=True: {n_keep} ({100 * n_keep / len(results):.1f}%)")
    from collections import Counter
    roles = Counter(r["role"] for r in results)
    print("Roles: " + " · ".join(f"{k}={v}" for k, v in roles.most_common()))

    avg_completeness = sum(r["data_completeness"] for r in results) / len(results)
    band_low  = sum(1 for r in results if r["data_completeness"] <= 0.33)
    band_mid  = sum(1 for r in results if 0.33 < r["data_completeness"] <= 0.66)
    band_high = sum(1 for r in results if r["data_completeness"] > 0.66)
    print(f"data_completeness (DD-027, solo diagnóstico): promedio={avg_completeness:.2f} · "
          f"0-0.33={band_low} · 0.34-0.66={band_mid} · 0.67-1.0={band_high}")

    print("\n── 20 ejemplos aleatorios " + "─" * 50)
    for r in rng.sample(results, min(20, len(results))):
        print(_fmt_row(r))

    print("\n── Top 20 keep=True por score " + "─" * 46)
    kept = sorted((r for r in results if r["keep"]),
                  key=lambda r: -r["final_score"])
    for r in kept[:20]:
        print(_fmt_row(r))

    print("\n── Top 20 keep=False con score más alto (posibles falsos negativos) ──")
    dropped = sorted((r for r in results if not r["keep"]),
                     key=lambda r: -r["final_score"])
    for r in dropped[:20]:
        print(_fmt_row(r))

    print("\n── Distribución de final_score " + "─" * 45)
    bins = [0] * 10
    for r in results:
        bins[min(9, int(r["final_score"] * 10))] += 1
    peak = max(bins) or 1
    for i, n in enumerate(bins):
        bar = "█" * max(1 if n else 0, round(44 * n / peak))
        print(f"  {i / 10:.1f}-{(i + 1) / 10:.1f} |{bar:<44}| {n}")
    print()


# ── 8. Neo4j (opcional) ───────────────────────────────────────────────────────
def write_neo4j(results: list, batch_size: int = 500):
    load_dotenv()
    uri, user, pwd = (os.getenv("NEO4J_URI"), os.getenv("NEO4J_USERNAME"),
                      os.getenv("NEO4J_PASSWORD"))
    if not all([uri, user, pwd]):
        print("⚠️  Credenciales Neo4j ausentes en .env — omitiendo escritura.")
        return
    from neo4j import GraphDatabase
    try:
        driver = GraphDatabase.driver(uri, auth=(user, pwd))
        driver.verify_connectivity()
    except Exception as e:
        print(f"⚠️  Sin conexión a Neo4j ({e}).\n"
              "    El puerto 7687 suele estar bloqueado (DD-013) — "
              "los scores quedan en el CSV; reintenta luego con --write-neo4j.")
        return

    rows = [{
        "username": r["username"],
        "geo": r["geography_score"], "cult": r["cultural_score"],
        "final": r["final_score"], "keep": r["keep"], "reason": r["reason"],
        "role": r["role"],
    } for r in results]
    with driver.session() as session:
        for i in range(0, len(rows), batch_size):
            session.run("""
                UNWIND $rows AS row
                MATCH (a:Account {username: row.username})
                SET a.geographyScore   = row.geo,
                    a.culturalScore    = row.cult,
                    a.classifierScore  = row.final,
                    a.classifierKeep   = row.keep,
                    a.classifierReason = row.reason,
                    a.classifierRole   = row.role
            """, rows=rows[i: i + batch_size])
    driver.close()
    print(f"✅ Scores escritos en Neo4j ({len(rows)} nodos :Account)")


# ── 9. CLI ────────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    diagnose_mode: bool = typer.Option(False, "--diagnose",
                                       help="Reporte de validación (obligatorio antes de usar los scores)."),
    write_db: bool = typer.Option(False, "--write-neo4j",
                                  help="Escribir scores a Neo4j (requiere puerto 7687)."),
    skip_usernames: bool = typer.Option(False, "--skip-usernames",
                                        help="Clasificar solo cuentas con perfil scrapeado."),
    neg_weight: float = typer.Option(NEG_WEIGHT, "--neg-weight",
                                     help="Peso de los anti-embeddings."),
    max_posts: int = typer.Option(10, "--max-posts",
                                  help="Captions por cuenta para embeddings."),
):
    """
    Fase 1-B: clasifica cuentas por geografía (Francia) × cultura (diáspora).

    Genera data_processed/account_scores.csv. Correr con --diagnose y revisar
    la salida ANTES de usar los scores río abajo.
    """
    tiers = load_tiers()
    profiles = load_profiles()
    nodes = load_nodes()
    print(f"📥 {len(profiles)} perfiles scrapeados · {len(nodes)} nodos en el grafo")

    clf = AccountClassifier(neg_weight=neg_weight, max_posts=max_posts)
    results = []

    # cuentas con perfil
    node_by_user = {r["username"]: r for r in nodes}
    from tqdm import tqdm
    for username, profile in tqdm(profiles.items(), desc="perfiles"):
        tier = category_to_tier(profile.get("businessCategoryName"),
                                username, tiers)
        res = clf.score_profile(profile, tier)
        node = node_by_user.get(username, {})
        if node.get("political") == "True" and not res["reason"].endswith("DD-012)"):
            res["keep"] = False
            res["reason"] += "; política(DD-012)"
        results.append(res)

    # cuentas sin perfil (solo username/fullName)
    if not skip_usernames:
        pending = [r for r in nodes if r["username"] not in profiles]
        print(f"🔤 {len(pending)} cuentas sin perfil — heurísticas + embedding de username")
        batch = 512
        for i in tqdm(range(0, len(pending), batch), desc="usernames"):
            results += clf.score_username_batch(pending[i: i + batch])

    # CSV
    OUTPUT_CSV.parent.mkdir(exist_ok=True)
    fieldnames = ["username", "geography_score", "cultural_score",
                  "final_score", "tier", "keep", "reason", "role",
                  "has_profile", "kind", "data_completeness"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(results, key=lambda r: -r["final_score"]))
    n_keep = sum(1 for r in results if r["keep"])
    print(f"\n💾 {OUTPUT_CSV} — {len(results)} cuentas, keep=True: {n_keep}")

    if diagnose_mode:
        diagnose(results)

    if write_db:
        write_neo4j(results)


if __name__ == "__main__":
    app()
