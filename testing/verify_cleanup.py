"""
testing/verify_cleanup.py — Hub Cultural DU

Corre esto DESPUÉS de `python cleanup_legacy_accounts.py` (sin --dry-run)
para confirmar el estado final del grafo: qué quedó, y que quedó limpio.

Chequea:
    1. Conteo de nodos por label — panorama general de lo que sobrevivió.
    2. Todas las :Account restantes tienen manualDataCuratedAt (si total ==
       curadas, ninguna cuenta "legacy" sobrevivió).
    3. Ningún :Hashtag/:Location/:Track/:Event/:Arrondissement/:City/
       :Country/:Comment quedó huérfano (0 relaciones) — si el barrido
       funcionó bien, esto debe dar 0 en todas las filas.
    4. Desglose de cuentas restantes por geoZone — chequeo de cordura
       rápido para confirmar que las curadas (incluidas las 83 de IDF)
       siguen ahí.
    5. Cuántas cuentas quedaron como "candidatas a revisar"
       (discoveredViaCuratedAccount=true, cleanup_legacy_accounts.py las
       preserva en vez de borrarlas) y su candidateReviewStatus.

Uso:
    python testing/verify_cleanup.py
"""

import os

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

app = typer.Typer()

COUNTS_BY_LABEL = """
    MATCH (n)
    UNWIND labels(n) AS lbl
    RETURN lbl, count(n) AS n
    ORDER BY n DESC
"""

ACCOUNTS_CURATED_CHECK = """
    MATCH (a:Account)
    RETURN count(a) AS total, count(a.manualDataCuratedAt) AS curated
"""

ORPHAN_CHECK = """
    MATCH (n)
    WHERE (n:Hashtag OR n:Location OR n:Track OR n:Event
           OR n:Arrondissement OR n:City OR n:Country OR n:Comment)
      AND NOT (n)--()
    UNWIND labels(n) AS lbl
    RETURN lbl, count(n) AS n
    ORDER BY n DESC
"""

ACCOUNTS_BY_GEOZONE = """
    MATCH (a:Account)
    RETURN coalesce(a.geoZone, '(sin geoZone)') AS geoZone, count(a) AS n
    ORDER BY n DESC
"""

CANDIDATES_CHECK = """
    MATCH (a:Account)
    WHERE a.discoveredViaCuratedAccount = true
    RETURN coalesce(a.candidateReviewStatus, '(sin status)') AS status, count(a) AS n
    ORDER BY n DESC
"""


@app.command()
def main():
    if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
        raise ValueError("Error: credenciales Neo4j ausentes en .env")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()

    with driver.session() as session:
        print("📊 Nodos por label:")
        for row in session.run(COUNTS_BY_LABEL):
            print(f"   · {row['lbl']}: {row['n']}")

        check = session.run(ACCOUNTS_CURATED_CHECK).single()
        total, curated = check["total"], check["curated"]
        status = "✅" if total == curated else "⚠️ "
        print(f"\n{status} Accounts: {total} total, {curated} con manualDataCuratedAt")
        if total != curated:
            print(f"   → {total - curated} cuentas SIN categorización manual sobrevivieron (revisar)")

        orphans = list(session.run(ORPHAN_CHECK))
        if not orphans:
            print("\n✅ Sin nodos huérfanos (Hashtag/Location/Track/Event/Arrondissement/City/Country/Comment)")
        else:
            print("\n⚠️  Huérfanos restantes:")
            for row in orphans:
                print(f"   · {row['lbl']}: {row['n']}")

        print("\n📍 Cuentas restantes por geoZone:")
        for row in session.run(ACCOUNTS_BY_GEOZONE):
            print(f"   · {row['geoZone']}: {row['n']}")

        candidates = list(session.run(CANDIDATES_CHECK))
        total_candidates = sum(row["n"] for row in candidates)
        print(f"\n🔎 {total_candidates} cuentas candidatas a revisar (descubiertas vía cuenta curada, preservadas por cleanup_legacy_accounts.py):")
        for row in candidates:
            print(f"   · {row['status']}: {row['n']}")

    driver.close()


if __name__ == "__main__":
    app()
