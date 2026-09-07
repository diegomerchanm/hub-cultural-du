"""
backfill_geozone_from_geocoding.py — Hub Cultural DU

Por qué existe: 4_enrich_locations.py (2026-09-07) ahora deriva e.geoZone
del geocoding de su propia Location en vez de depender solo de la cuenta
curada (ver el bloque "geoZone por evento a partir del geocoding" en ese
archivo, y apply_geozone_from_location) -- pero eso solo corre para
Location RECIÉN geocodificadas en cada corrida futura. Las Location que ya
tenían lat/geocodeConfidence ANTES de este cambio nunca vuelven a pasar por
run_geocoding (su condición de idempotencia es `lat IS NULL OR
geocodeConfidence IS NULL`, y esas ya tienen ambas). Este script es el
backfill de una sola vez para ese backlog -- mismo patrón que
backfill_geo_zone.py / backfill_art_tags_fr.py ya usan en este proyecto
para el mismo tipo de problema (una mejora que solo se aplicó hacia
adelante, con un vacío hacia atrás).

Precedencia (idéntica a la de 4_enrich_locations.py::apply_geozone_from_location
-- si cambias una, cambiá la otra, no hay import compartido porque los
nombres de archivo con dígito inicial no son importables como módulo):
  - e.geoZone vacío                              → se llena, cualquier confianza
  - e.geoZone con valor, coincide con lo derivado → no se toca
  - e.geoZone con valor, discrepa, confidence=city_combined → se sobreescribe
  - e.geoZone con valor, discrepa, confidence=name_only     → NO se toca,
    se anota en e.geoZoneConflict para revisión manual

Idempotente: correrlo de nuevo no cambia nada si ya no hay discrepancias
pendientes (los eventos ya corregidos coinciden con lo derivado la segunda
vez). --dry-run corre en una transacción con ROLLBACK, igual que
cleanup_legacy_accounts.py / exclude_accounts.py.

Uso:
    python backfill_geozone_from_geocoding.py --dry-run   # cuenta exacto, no escribe
    python backfill_geozone_from_geocoding.py              # escribe de verdad
"""

import os

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

app = typer.Typer()

# ── Copia deliberada de 4_enrich_locations.py -- ver docstring de arriba ──────
IDF_POSTCODE_PREFIXES = ("75", "77", "78", "91", "92", "93", "94", "95")
IDF_CITIES = {
    "paris", "boulogne-billancourt", "saint-denis", "argenteuil", "montreuil",
    "nanterre", "vitry-sur-seine", "créteil", "creteil", "aubervilliers",
    "aulnay-sous-bois", "colombes", "asnières-sur-seine", "asnieres-sur-seine",
    "rueil-malmaison", "champigny-sur-marne", "saint-maur-des-fossés",
    "saint-maur-des-fosses", "drancy", "issy-les-moulineaux", "levallois-perret",
    "noisy-le-grand", "antony", "neuilly-sur-seine", "sarcelles",
    "ivry-sur-seine", "villejuif", "clichy", "pantin", "meaux", "cergy",
    "vincennes", "maisons-alfort", "versailles", "melun", "évry", "evry",
    "corbeil-essonnes", "massy", "sartrouville", "fontenay-sous-bois", "bondy",
    "bobigny", "épinay-sur-seine", "epinay-sur-seine", "le blanc-mesnil",
    "villeneuve-saint-georges", "gennevilliers", "chelles", "suresnes",
    "puteaux", "courbevoie", "montrouge", "vanves", "malakoff", "bagneux",
    "chatou", "poissy", "mantes-la-jolie", "saint-germain-en-laye",
}


def derive_geozone(country_code, city, arrondissement, postcode):
    if not country_code:
        return None
    if country_code.upper() != "FR":
        return "Fuera de Francia"
    if arrondissement:
        return "Île-de-France"
    if postcode and postcode[:2] in IDF_POSTCODE_PREFIXES:
        return "Île-de-France"
    if city and city.strip().lower() in IDF_CITIES:
        return "Île-de-France"
    return "Francia fuera IDF"


CANDIDATES_QUERY = """
MATCH (e:Event)-[:LOCATED_AT]->(l:Location)
WHERE l.lat IS NOT NULL AND l.countryCode IS NOT NULL AND l.countryCode <> ''
RETURN e.id AS id, e.geoZone AS geoZone,
       l.countryCode AS countryCode, l.city AS city,
       l.arrondissement AS arrondissement, l.postcode AS postcode,
       l.geocodeConfidence AS confidence
"""


@app.command()
def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Corre dentro de una transacción y hace ROLLBACK — cuenta exacto, no escribe nada"
    ),
):
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    filled = overridden = flagged = unchanged = skipped_no_evidence = 0

    with driver.session() as session:
        with session.begin_transaction() as tx:
            rows = tx.run(CANDIDATES_QUERY).data()
            print(f"📋 {len(rows)} par(es) evento-Location ya geocodificados a revisar\n")

            for r in rows:
                derived = derive_geozone(r["countryCode"], r["city"], r["arrondissement"], r["postcode"])
                if derived is None:
                    skipped_no_evidence += 1
                    continue
                current = r["geoZone"]
                confidence = r["confidence"] or ""

                if not current:
                    tx.run("""
                        MATCH (e:Event {id: $id})
                        SET e.geoZone = $geoZone, e.geoZoneSource = 'geocoded',
                            e.geoZoneUpdatedAt = datetime()
                        REMOVE e.geoZoneConflict
                    """, id=r["id"], geoZone=derived)
                    filled += 1
                elif current == derived:
                    unchanged += 1
                elif confidence == "city_combined":
                    tx.run("""
                        MATCH (e:Event {id: $id})
                        SET e.geoZone = $geoZone, e.geoZoneSource = 'geocoded',
                            e.geoZonePrevious = $previous, e.geoZoneUpdatedAt = datetime()
                        REMOVE e.geoZoneConflict
                    """, id=r["id"], geoZone=derived, previous=current)
                    overridden += 1
                else:
                    tx.run("""
                        MATCH (e:Event {id: $id})
                        SET e.geoZoneConflict = $derived
                    """, id=r["id"], derived=derived)
                    flagged += 1

            if dry_run:
                tx.rollback()
                print("[dry-run] ROLLBACK — nada se guardó.\n")
            else:
                tx.commit()
                print("✅ COMMIT.\n")

    driver.close()

    print(f"  Vacíos llenados       : {filled}")
    print(f"  Heredados corregidos  : {overridden}  (confidence=city_combined, discrepancia fuerte)")
    print(f"  Discrepancias flojas  : {flagged}  (confidence=name_only — quedan en e.geoZoneConflict, revisar a mano)")
    print(f"  Ya coincidían         : {unchanged}")
    print(f"  Sin evidencia (countryCode vacío pese a tener lat/lon) : {skipped_no_evidence}")
    if flagged and not dry_run:
        print(
            "\n💡 Para ver las discrepancias flojas marcadas, en Neo4j Browser:\n"
            "   MATCH (e:Event) WHERE e.geoZoneConflict IS NOT NULL\n"
            "   RETURN e.id, e.title, e.geoZone AS actual, e.geoZoneConflict AS geocodificado_dice"
        )


if __name__ == "__main__":
    app()
