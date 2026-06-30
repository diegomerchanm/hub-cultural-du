"""
Fase 4-A — Enriquecimiento NLP de nodos Account y Post.

Propiedades escritas:
  Account → bioLanguage, bioEntities, bioKeywords, bioEmbedding (opt)
  Post    → captionLanguage, captionEntities, captionKeywords, captionEmbedding (opt)

Idempotente: solo procesa nodos cuya propiedad de idioma es NULL.
"""

import math
import os
from typing import Optional

import typer
from dotenv import load_dotenv
from langdetect import LangDetectException, detect
from neo4j import GraphDatabase
from tqdm import tqdm

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# ── 2. spaCy — carga perezosa por idioma ──────────────────────────────────────
LANG_TO_MODEL = {
    "es": "es_core_news_lg",
    "en": "en_core_web_sm",
    "fr": "fr_core_news_lg",
}
_NLP: dict = {}


def get_nlp(lang: str):
    if lang not in _NLP:
        model_name = LANG_TO_MODEL.get(lang)
        if not model_name:
            return None
        import spacy
        print(f"  📦 Cargando spaCy: {model_name}")
        _NLP[lang] = spacy.load(model_name, disable=["senter"])
    return _NLP[lang]


# ── 3. Helpers ────────────────────────────────────────────────────────────────
def detect_lang(text: str) -> str:
    if not text or len(text.strip()) < 10:
        return "unknown"
    try:
        lang = detect(text)
        return lang if lang in LANG_TO_MODEL else "unknown"
    except LangDetectException:
        return "unknown"


def _is_valid_date_entity(text: str) -> bool:
    """True si el texto es parseable como fecha real (no "siglo XX", "años 90", etc.)."""
    try:
        from dateutil import parser as dp
        dp.parse(text, fuzzy=False)
        return True
    except Exception:
        return False


_URL_RE    = __import__("re").compile(r"https?://\S+|www\.\S+")
_EMOJI_RE  = __import__("re").compile(
    "[\U00010000-\U0010ffff"
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251]+",
    flags=__import__("re").UNICODE,
)


def _is_noise_keyword(text: str) -> bool:
    """True si el chunk es solo emojis, puntuación o una URL."""
    clean = _URL_RE.sub("", text).strip()
    clean = _EMOJI_RE.sub("", clean).strip()
    # Queda vacío o es solo puntuación/espacios
    return not clean or all(not c.isalnum() for c in clean)


def extract_features(text: str, lang: str) -> dict:
    """Extrae entidades NER (tipo:texto) y noun chunks como keywords."""
    nlp = get_nlp(lang)
    if not nlp or not text:
        return {"entities": [], "keywords": []}

    doc = nlp(text[:5000])

    ALLOWED_ENT_TYPES = {"PER", "LOC", "GPE", "ORG", "DATE", "FAC"}

    entities = list({
        f"{ent.label_}:{ent.text.strip()}"
        for ent in doc.ents
        if (
            ent.label_ in ALLOWED_ENT_TYPES                # whitelist de tipos
            and len(ent.text.strip()) > 1
            and len(ent.text.strip().split()) <= 6         # máx 6 palabras
            and len(ent.text.strip()) <= 60                # máx 60 caracteres
            and "http" not in ent.text                     # sin URLs
            and "/" not in ent.text                        # sin paths/URLs
            and "\n" not in ent.text                       # sin saltos de línea
            and (                                          # DATE parseable
                ent.label_ != "DATE"
                or _is_valid_date_entity(ent.text.strip())
            )
        )
    })
    keywords = list({
        chunk.text.lower().strip()
        for chunk in doc.noun_chunks
        if (
            len(chunk.text.strip()) > 2
            and not chunk.text.startswith("#")
            and not _is_noise_keyword(chunk.text)         # 2. sin emojis/URLs/puntuación
        )
    })
    return {"entities": entities, "keywords": keywords}


# ── 4. Account.biography ──────────────────────────────────────────────────────
def process_biographies(batch_size: int = 50) -> int:
    print("\n📖 Enriqueciendo Account.biography")
    print("=" * 55)

    with driver.session() as session:
        records = session.run("""
            MATCH (a:Account)
            WHERE a.biography IS NOT NULL AND a.biography <> ''
              AND a.bioLanguage IS NULL
            RETURN a.username AS username, a.biography AS bio
        """).data()

    if not records:
        print("  ✅ Todas las biografías ya procesadas.")
        return 0

    print(f"  🔍 {len(records)} pendientes")

    for i in tqdm(range(0, len(records), batch_size), desc="  bio"):
        batch = records[i: i + batch_size]
        writes = []
        for r in batch:
            lang  = detect_lang(r["bio"])
            feats = extract_features(r["bio"], lang) if lang != "unknown" else {"entities": [], "keywords": []}
            writes.append({
                "username": r["username"],
                "lang":     lang,
                "entities": feats["entities"],
                "keywords": feats["keywords"],
            })
        with driver.session() as session:
            session.run("""
                UNWIND $rows AS row
                MATCH (a:Account {username: row.username})
                SET a.bioLanguage = row.lang,
                    a.bioEntities = row.entities,
                    a.bioKeywords = row.keywords
            """, rows=writes)

    print(f"  ✅ {len(records)} biografías enriquecidas")
    return len(records)


# ── 5. Post.caption ───────────────────────────────────────────────────────────
def process_captions(batch_size: int = 100) -> int:
    print("\n📸 Enriqueciendo Post.caption")
    print("=" * 55)

    with driver.session() as session:
        records = session.run("""
            MATCH (p:Post)
            WHERE p.caption IS NOT NULL AND p.caption <> ''
              AND p.captionLanguage IS NULL
            RETURN p.id AS id, p.caption AS caption
        """).data()

    if not records:
        print("  ✅ Todos los captions ya procesados.")
        return 0

    print(f"  🔍 {len(records)} pendientes")

    for i in tqdm(range(0, len(records), batch_size), desc="  captions"):
        batch = records[i: i + batch_size]
        writes = []
        for r in batch:
            lang  = detect_lang(r["caption"])
            feats = extract_features(r["caption"], lang) if lang != "unknown" else {"entities": [], "keywords": []}
            writes.append({
                "id":       r["id"],
                "lang":     lang,
                "entities": feats["entities"],
                "keywords": feats["keywords"],
            })
        with driver.session() as session:
            session.run("""
                UNWIND $rows AS row
                MATCH (p:Post {id: row.id})
                SET p.captionLanguage = row.lang,
                    p.captionEntities = row.entities,
                    p.captionKeywords = row.keywords
            """, rows=writes)

    print(f"  ✅ {len(records)} captions enriquecidos")
    return len(records)


# ── 6. Embeddings semánticos ──────────────────────────────────────────────────
def generate_embeddings(
    target: str = "bio",
    model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
    batch_size: int = 32,
) -> int:
    """
    Genera embeddings con sentence-transformers.
    target: 'bio' → Account.bioEmbedding | 'posts' → Post.captionEmbedding
    """
    if target == "bio":
        cypher_fetch = """
            MATCH (a:Account)
            WHERE a.biography IS NOT NULL AND a.biography <> ''
              AND a.bioEmbedding IS NULL
            RETURN a.username AS id, a.biography AS text
        """
        cypher_write = """
            UNWIND $rows AS row
            MATCH (a:Account {username: row.id})
            SET a.bioEmbedding = row.emb
        """
        label = "Account.bioEmbedding"
    else:
        cypher_fetch = """
            MATCH (p:Post)
            WHERE p.caption IS NOT NULL AND p.caption <> ''
              AND p.captionEmbedding IS NULL
            RETURN p.id AS id, p.caption AS text
        """
        cypher_write = """
            UNWIND $rows AS row
            MATCH (p:Post {id: row.id})
            SET p.captionEmbedding = row.emb
        """
        label = "Post.captionEmbedding"

    print(f"\n🧠 Embeddings — {label}")
    print("=" * 55)

    with driver.session() as session:
        records = session.run(cypher_fetch).data()

    if not records:
        print(f"  ✅ Embeddings ya generados para {label}.")
        return 0

    print(f"  🔍 {len(records)} registros sin embedding")
    print(f"  📦 Cargando modelo: {model_name}")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)

    n_batches = math.ceil(len(records) / batch_size)
    for i in tqdm(range(0, len(records), batch_size), total=n_batches, desc=f"  emb-{target}"):
        batch   = records[i: i + batch_size]
        vectors = model.encode([r["text"][:2000] for r in batch], show_progress_bar=False).tolist()
        writes  = [{"id": r["id"], "emb": v} for r, v in zip(batch, vectors)]
        with driver.session() as session:
            session.run(cypher_write, rows=writes)

    print(f"  ✅ {len(records)} embeddings almacenados en {label}")
    return len(records)


# ── 7. Cobertura ──────────────────────────────────────────────────────────────
def print_coverage():
    print("\n📊 Cobertura NLP")
    print("=" * 55)
    with driver.session() as session:
        bio = session.run("""
            MATCH (a:Account) WHERE a.biography IS NOT NULL AND a.biography <> ''
            RETURN count(a) AS total,
                   count(a.bioLanguage)  AS nlp,
                   count(a.bioEmbedding) AS emb
        """).single()
        post = session.run("""
            MATCH (p:Post) WHERE p.caption IS NOT NULL AND p.caption <> ''
            RETURN count(p) AS total,
                   count(p.captionLanguage)  AS nlp,
                   count(p.captionEmbedding) AS emb
        """).single()
        langs = session.run("""
            MATCH (a:Account) WHERE a.bioLanguage IS NOT NULL
            RETURN a.bioLanguage AS lang, count(*) AS n ORDER BY n DESC
        """).data()

    print(f"\n  Account.biography   total={bio['total']}  nlp={bio['nlp']}  emb={bio['emb']}")
    print(f"  Post.caption        total={post['total']}  nlp={post['nlp']}  emb={post['emb']}")
    if langs:
        print("\n  Idiomas (bio):")
        for row in langs:
            print(f"    {row['lang']:<10} {row['n']:>5}")


# ── 8. CLI ────────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    only: Optional[str] = typer.Option(
        None, help="Limitar a 'bio' o 'posts'. Sin valor: ambos."
    ),
    embeddings: bool = typer.Option(
        False, "--embeddings", help="Generar embeddings semánticos."
    ),
    emb_target: str = typer.Option(
        "bio", "--emb-target", help="'bio' | 'posts' | 'both'"
    ),
    embedding_model: str = typer.Option(
        "paraphrase-multilingual-MiniLM-L12-v2", "--embedding-model"
    ),
    batch_size: int = typer.Option(50, "--batch-size"),
):
    """
    Fase 4-A: idioma + NER + keywords (+ embeddings opcionales).

    Ejecutar ANTES de nlp_extract_events.py para pre-enriquecer captions.
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    if only in (None, "bio"):
        process_biographies(batch_size=batch_size)
    if only in (None, "posts"):
        process_captions(batch_size=batch_size)

    if embeddings:
        targets = ["bio", "posts"] if emb_target == "both" else [emb_target]
        for t in targets:
            generate_embeddings(target=t, model_name=embedding_model)

    print_coverage()
    driver.close()
    print("\n✅ Enriquecimiento NLP completo.")


if __name__ == "__main__":
    app()
