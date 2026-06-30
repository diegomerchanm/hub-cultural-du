import os
import math
from typing import Optional
import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase
from tqdm import tqdm
from langdetect import detect, LangDetectException

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# ── 2. Modelos spaCy — carga perezosa por idioma ──────────────────────────────
_NLP_MODELS: dict = {}

LANG_TO_MODEL = {
    "es": "es_core_news_lg",
    "en": "en_core_web_sm",
    "fr": "fr_core_news_lg",
}


def get_nlp(lang: str):
    """Carga el modelo spaCy para el idioma dado, cacheándolo en memoria."""
    if lang not in _NLP_MODELS:
        model_name = LANG_TO_MODEL.get(lang)
        if not model_name:
            return None
        import spacy
        print(f"  📦 Cargando modelo spaCy: {model_name}...")
        _NLP_MODELS[lang] = spacy.load(model_name, disable=["parser"])
    return _NLP_MODELS[lang]


# ── 3. Detección de idioma ─────────────────────────────────────────────────────
def detect_language(text: str) -> str:
    """Devuelve 'es', 'en', 'fr', u 'unknown'. Solo considera idiomas soportados."""
    if not text or len(text.strip()) < 10:
        return "unknown"
    try:
        lang = detect(text)
        return lang if lang in LANG_TO_MODEL else "unknown"
    except LangDetectException:
        return "unknown"


# ── 4. Extracción de entidades y palabras clave ───────────────────────────────
def extract_features(text: str, lang: str) -> dict:
    """
    Retorna:
      - entities: lista de strings "TIPO:texto" deduplicada (NER)
      - keywords: lista de noun chunks filtrados, únicos, lowercase
    """
    nlp = get_nlp(lang)
    if not nlp or not text:
        return {"entities": [], "keywords": []}

    doc = nlp(text[:5000])  # límite para evitar textos excesivamente largos

    entities = list({
        f"{ent.label_}:{ent.text.strip()}"
        for ent in doc.ents
        if len(ent.text.strip()) > 1
    })

    keywords = list({
        chunk.text.lower().strip()
        for chunk in doc.noun_chunks
        if len(chunk.text.strip()) > 2 and not chunk.text.startswith("#")
    })

    return {"entities": entities, "keywords": keywords}


# ── 5. Procesar biografías de Account ─────────────────────────────────────────
def process_biographies(batch_size: int = 50):
    print("\n📖 Fase NLP — Biografías de Account")
    print("=" * 55)

    with driver.session() as session:
        records = session.run("""
            MATCH (a:Account)
            WHERE a.biography IS NOT NULL
              AND a.biography <> ''
              AND a.bioLanguage IS NULL
            RETURN a.username AS username, a.biography AS bio
        """).data()

    if not records:
        print("  ✅ Todas las biografías ya procesadas.")
        return

    print(f"  🔍 {len(records)} cuentas con biografía pendiente")

    updated = 0
    for i in tqdm(range(0, len(records), batch_size), desc="  Biografías"):
        batch = records[i : i + batch_size]
        writes = []
        for r in batch:
            lang = detect_language(r["bio"])
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
                SET a.bioLanguage  = row.lang,
                    a.bioEntities  = row.entities,
                    a.bioKeywords  = row.keywords
            """, rows=writes)

        updated += len(batch)

    print(f"\n  ✅ {updated} biografías enriquecidas")


# ── 6. Procesar captions de Post ──────────────────────────────────────────────
def process_captions(batch_size: int = 100):
    print("\n📸 Fase NLP — Captions de Post")
    print("=" * 55)

    with driver.session() as session:
        records = session.run("""
            MATCH (p:Post)
            WHERE p.caption IS NOT NULL
              AND p.caption <> ''
              AND p.captionLanguage IS NULL
            RETURN p.id AS id, p.caption AS caption
        """).data()

    if not records:
        print("  ✅ Todos los captions ya procesados.")
        return

    print(f"  🔍 {len(records)} posts con caption pendiente")

    updated = 0
    for i in tqdm(range(0, len(records), batch_size), desc="  Captions"):
        batch = records[i : i + batch_size]
        writes = []
        for r in batch:
            lang = detect_language(r["caption"])
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
                SET p.captionLanguage  = row.lang,
                    p.captionEntities  = row.entities,
                    p.captionKeywords  = row.keywords
            """, rows=writes)

        updated += len(batch)

    print(f"\n  ✅ {updated} captions enriquecidos")


# ── 7. Embeddings semánticos (opcional) ───────────────────────────────────────
def generate_bio_embeddings(model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
                            batch_size: int = 32):
    """
    Genera embeddings de las biografías con sentence-transformers y los escribe
    en Account.bioEmbedding como lista de floats.
    Compatible con índices vectoriales de Neo4j (VECTOR INDEX).
    """
    print(f"\n🧠 Fase NLP — Embeddings ({model_name})")
    print("=" * 55)

    from sentence_transformers import SentenceTransformer

    with driver.session() as session:
        records = session.run("""
            MATCH (a:Account)
            WHERE a.biography IS NOT NULL
              AND a.biography <> ''
              AND a.bioEmbedding IS NULL
            RETURN a.username AS username, a.biography AS bio
        """).data()

    if not records:
        print("  ✅ Todos los embeddings ya generados.")
        return

    print(f"  🔍 {len(records)} cuentas sin embedding")
    print(f"  📦 Cargando modelo: {model_name}...")
    model = SentenceTransformer(model_name)

    total_batches = math.ceil(len(records) / batch_size)
    for i in tqdm(range(0, len(records), batch_size), total=total_batches, desc="  Embeddings"):
        batch   = records[i : i + batch_size]
        texts   = [r["bio"] for r in batch]
        vectors = model.encode(texts, show_progress_bar=False).tolist()

        writes = [
            {"username": r["username"], "embedding": v}
            for r, v in zip(batch, vectors)
        ]
        with driver.session() as session:
            session.run("""
                UNWIND $rows AS row
                MATCH (a:Account {username: row.username})
                SET a.bioEmbedding = row.embedding
            """, rows=writes)

    print(f"\n  ✅ {len(records)} embeddings almacenados en bioEmbedding")


# ── 8. Resumen de cobertura ────────────────────────────────────────────────────
def print_coverage():
    print("\n📊 Cobertura NLP actual")
    print("=" * 55)
    with driver.session() as session:
        bio_stats = session.run("""
            MATCH (a:Account)
            WHERE a.biography IS NOT NULL AND a.biography <> ''
            RETURN
              count(a) AS total,
              count(a.bioLanguage) AS processed,
              count(a.bioEmbedding) AS withEmbedding
        """).single()

        post_stats = session.run("""
            MATCH (p:Post)
            WHERE p.caption IS NOT NULL AND p.caption <> ''
            RETURN
              count(p) AS total,
              count(p.captionLanguage) AS processed
        """).single()

        lang_dist = session.run("""
            MATCH (a:Account)
            WHERE a.bioLanguage IS NOT NULL
            RETURN a.bioLanguage AS lang, count(*) AS n
            ORDER BY n DESC
        """).data()

    print(f"\n  Account.biography")
    print(f"    Total con bio    : {bio_stats['total']}")
    print(f"    NLP procesados   : {bio_stats['processed']}")
    print(f"    Con embedding    : {bio_stats['withEmbedding']}")

    print(f"\n  Post.caption")
    print(f"    Total con caption: {post_stats['total']}")
    print(f"    NLP procesados   : {post_stats['processed']}")

    if lang_dist:
        print(f"\n  Distribución de idiomas (biografías):")
        for row in lang_dist:
            print(f"    {row['lang']:<10} {row['n']:>5}")


# ── 9. CLI ────────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    only: Optional[str] = typer.Option(
        None,
        help="Procesar solo 'bio' o solo 'posts'. Sin valor: ambos.",
    ),
    embeddings: bool = typer.Option(
        False,
        "--embeddings",
        help="Generar embeddings semánticos para las biografías.",
    ),
    embedding_model: str = typer.Option(
        "paraphrase-multilingual-MiniLM-L12-v2",
        "--embedding-model",
        help="Modelo sentence-transformers a usar.",
    ),
    batch_size: int = typer.Option(50, "--batch-size", help="Tamaño de lote para escritura en Neo4j."),
):
    """
    Fase 4 del pipeline: enriquecimiento NLP de biografías y captions.

    Detecta idioma (ES/EN/FR), extrae entidades (NER) y palabras clave,
    y opcionalmente genera embeddings semánticos multilingual.

    Propiedades escritas en Neo4j:
      Account → bioLanguage, bioEntities, bioKeywords, bioEmbedding (opcional)
      Post    → captionLanguage, captionEntities, captionKeywords
    """
    driver.verify_connectivity()
    print("✅ Conexión exitosa a Neo4j Aura\n")

    if only == "bio":
        process_biographies(batch_size=batch_size)
    elif only == "posts":
        process_captions(batch_size=batch_size)
    else:
        process_biographies(batch_size=batch_size)
        process_captions(batch_size=batch_size)

    if embeddings:
        generate_bio_embeddings(model_name=embedding_model, batch_size=32)

    print_coverage()
    driver.close()
    print("\n✅ Pipeline NLP completo.")


if __name__ == "__main__":
    app()
