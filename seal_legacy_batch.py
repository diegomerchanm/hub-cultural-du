"""
seal_legacy_batch.py — Hub Cultural DU

Corre esto UNA SOLA VEZ, antes de volver a correr 2_build_graph.py (ya
actualizado con firstSeenAt/lastUpdatedAt) o cualquier fase 1-2 nueva.

Problema que resuelve: hasta ahora, :Account, :Post, :Hashtag, :Location,
:Track, :Comment e :IgtvVideo no tenían ningún campo de fecha de ingesta —
2_build_graph.py solo hacía MERGE + SET sin distinguir creación de
actualización. Eso significa que no hay forma de saber qué nodos son de la
carga original vs. de una carga futura.

Este script sella el estado ACTUAL del grafo (todo lo que no tiene ninguna
marca de fecha conocida: ni firstSeenAt, ni createdAt de :Event, ni
geocodedAt de :Location) con:
    legacyBatch     = true
    legacyBatchDate = fecha de hoy

A partir de ahí, cualquier nodo que 2_build_graph.py cree de aquí en
adelante va a tener firstSeenAt propio (nunca legacyBatch), así que la
distinción entre "antes de hoy" y "de aquí en adelante" queda trazada.

Idempotente: solo toca nodos que no tengan legacyBatch ni ninguna otra
marca de fecha — correrlo dos veces no hace nada la segunda vez.

Uso:
    python seal_legacy_batch.py --dry-run   # solo cuenta, no escribe
    python seal_legacy_batch.py             # sella de verdad
"""

import os
from datetime import date

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

app = typer.Typer()

# Cualquier nodo con alguna de estas propiedades ya tiene fecha propia por
# otro mecanismo (Event via 4_enrich_events_extract.py, Location geocodeada
# via 4_enrich_locations.py, cuenta curada a mano via
# load_manual_account_categorization.py, o ya sellado / ya tocado por el
# 2_build_graph.py nuevo) — no hace falta ni tiene sentido marcarlo como legacy.
UNDATED_CONDITION = """
    n.firstSeenAt IS NULL
    AND n.createdAt IS NULL
    AND n.geocodedAt IS NULL
    AND n.manualDataCuratedAt IS NULL
    AND n.legacyBatch IS NULL
"""

COUNT_QUERY = f"""
    MATCH (n)
    WHERE {UNDATED_CONDITION}
    UNWIND labels(n) AS lbl
    RETURN lbl, count(DISTINCT n) AS cnt
    ORDER BY cnt DESC
"""

TOTAL_QUERY = f"""
    MATCH (n)
    WHERE {UNDATED_CONDITION}
    RETURN count(n) AS n
"""

SEAL_QUERY = f"""
    MATCH (n)
    WHERE {UNDATED_CONDITION}
    SET n.legacyBatch = true, n.legacyBatchDate = date($today)
    RETURN count(n) AS n
"""


@app.command()
def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo cuenta, no escribe nada en Neo4j"),
):
    if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
        raise ValueError("Error: credenciales Neo4j ausentes en .env")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()

    with driver.session() as session:
        breakdown = list(session.run(COUNT_QUERY))
        total = session.run(TOTAL_QUERY).single()["n"]

        print(f"📦 {total} nodos sin ninguna fecha de ingesta conocida (candidatos a legacyBatch):")
        for row in breakdown:
            print(f"   · {row['lbl']}: {row['cnt']}")

        if total == 0:
            print("\n✅ Nada que sellar — todo el grafo ya tiene alguna fecha propia.")
            driver.close()
            return

        if dry_run:
            print("\n[dry-run] No se escribió nada. Corre sin --dry-run para sellar estos nodos.")
            driver.close()
            return

        result = session.run(SEAL_QUERY, today=date.today().isoformat())
        n = result.single()["n"]
        print(f"\n✅ {n} nodos sellados con legacyBatch=true, legacyBatchDate={date.today().isoformat()}")
        print(
            "\nAhora puedes correr 2_build_graph.py (fases 1-2) con la certeza de que todo "
            "lo nuevo va a tener firstSeenAt propio, distinto de este lote sellado."
        )

    driver.close()


if __name__ == "__main__":
    app()
