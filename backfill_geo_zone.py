"""
Backfill único — copia `geoZone` de `:Account` a `:Event` para eventos que
quedaron sin ese dato porque la cuenta se curó DESPUÉS de que el evento ya
existiera (herencia cuenta→evento en `4_enrich_events_extract.py` es
"solo al crear", nunca retroactiva — mismo patrón/limitación que ya tienen
`artType`/`institutionType`/`culturalIdentity`/`photoPermission`, ver
CLAUDE.md "Manual categorization"). Pieza A del plan de limpieza de datos
geográficos discutido con Diego el 2026-08-28 (ver docs/decisions_es.md
DD-070): sobre 751 eventos, 309 no tienen `geoZone` ni `cityName`; de esos,
40 son exactamente este caso — la cuenta YA tiene `geoZone` en la planilla,
el evento nunca lo heredó porque es viejo. Este script los recupera sin
tocar la planilla ni curar nada nuevo.

Sin LLM: es una copia de una propiedad que ya está en Neo4j (`Account.
geoZone`, cargada por `load_manual_account_categorization.py`), vía
`e.sourceAuthor = a.username` — el mismo join que ya usa `5_export_
dashboard_data.py` (`OPTIONAL MATCH (src:Account {username: e.sourceAuthor})`).

Idempotente: solo toca eventos con `geoZone` ausente o vacío, y solo cuando
la cuenta correspondiente sí tiene `geoZone`. Complementa (no reemplaza) la
Pieza B del mismo plan: eventos con dirección/lugar en texto pero sin
geocodificar se resuelven corriendo `4_enrich_locations.py` de nuevo, sin
necesitar este script ni ningún otro.

Uso:
    python backfill_geo_zone.py --dry-run   # ver cuántos eventos se tocarían
    python backfill_geo_zone.py             # escribir geoZone en Neo4j
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
NEEDS_BACKFILL_WHERE = "(e.geoZone IS NULL OR e.geoZone = '')"
ACCOUNT_HAS_GEO_WHERE = "(a.geoZone IS NOT NULL AND a.geoZone <> '')"


def count_candidates(session) -> int:
    return session.run(f"""
        MATCH (e:Event)
        MATCH (a:Account {{username: e.sourceAuthor}})
        WHERE {NEEDS_BACKFILL_WHERE} AND {ACCOUNT_HAS_GEO_WHERE}
        RETURN count(e) AS n
    """).single()["n"]


def apply_backfill(session) -> int:
    result = session.run(f"""
        MATCH (e:Event)
        MATCH (a:Account {{username: e.sourceAuthor}})
        WHERE {NEEDS_BACKFILL_WHERE} AND {ACCOUNT_HAS_GEO_WHERE}
        SET e.geoZone = a.geoZone
        RETURN count(e) AS n
    """)
    return result.single()["n"]


app = typer.Typer(add_completion=False)


@app.command()
def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Mostrar cuántos eventos recibirían geoZone, sin escribir en Neo4j."
    ),
):
    with driver.session() as session:
        n = count_candidates(session)
        if not n:
            print("  ✅ Nada que hacer — todos los eventos con cuenta curada ya tienen geoZone.")
            return

        print(f"  🌍 {n} eventos recibirían geoZone heredado de su cuenta ya curada.")

        if dry_run:
            print("  🔎 --dry-run: nada escrito.")
            return

        print("  💾 Escribiendo geoZone en Neo4j...")
        written = apply_backfill(session)
        print(f"  ✅ {written} eventos actualizados.")


if __name__ == "__main__":
    app()
