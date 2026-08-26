"""
Backfill único — copia la foto real del post de Instagram (`:Post.displayUrl`,
ya capturada por `2_build_graph.py` desde siempre) a `:Event.imageUrl` para
eventos creados ANTES de que `4_enrich_events_extract.py` empezara a
guardarla directamente (ver docs/decisions_es.md DD-057).

Sin LLM: es una copia de una propiedad que ya está en Neo4j, vía la
relación `(:Post)-[:MENTIONS_EVENT]->(:Event)` que ya existe para todo
evento. Un evento puede tener más de un post mencionándolo (co-publicado o
enriquecido después) — se toma cualquiera de las URLs no vacías, no
importa cuál, solo hace falta UNA foto representativa.

Idempotente: solo toca eventos con `imageUrl` ausente o vacío.

Uso:
    python backfill_event_images.py --dry-run   # ver cuántos eventos se tocarían
    python backfill_event_images.py             # escribir imageUrl en Neo4j
"""

import os

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# "Necesita backfill" es el mismo criterio en el conteo del --dry-run y en
# la escritura real, para que el número que se muestra antes sea exacto.
NEEDS_BACKFILL_WHERE = "e.imageUrl IS NULL OR e.imageUrl = ''"


def count_candidates(session) -> int:
    return session.run(f"""
        MATCH (p:Post)-[:MENTIONS_EVENT]->(e:Event)
        WHERE ({NEEDS_BACKFILL_WHERE})
          AND p.displayUrl IS NOT NULL AND p.displayUrl <> ''
        RETURN count(DISTINCT e) AS n
    """).single()["n"]


def apply_backfill(session) -> int:
    result = session.run(f"""
        MATCH (p:Post)-[:MENTIONS_EVENT]->(e:Event)
        WHERE ({NEEDS_BACKFILL_WHERE})
          AND p.displayUrl IS NOT NULL AND p.displayUrl <> ''
        WITH e, collect(p.displayUrl)[0] AS img
        SET e.imageUrl = img
        RETURN count(e) AS n
    """)
    return result.single()["n"]


app = typer.Typer(add_completion=False)


@app.command()
def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Mostrar cuántos eventos recibirían imageUrl, sin escribir en Neo4j."
    ),
):
    with driver.session() as session:
        n = count_candidates(session)
        if not n:
            print("  ✅ Nada que hacer — todos los eventos con post asociado ya tienen imageUrl, o ningún post candidato tiene displayUrl.")
            return

        print(f"  📸 {n} eventos recibirían una foto de su post original.")

        if dry_run:
            print("  🔎 --dry-run: nada escrito.")
            return

        print("  💾 Escribiendo imageUrl en Neo4j...")
        written = apply_backfill(session)
        print(f"  ✅ {written} eventos actualizados.")


if __name__ == "__main__":
    app()
