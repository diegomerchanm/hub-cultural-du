"""
Geocodificación de nodos (:Location) y creación de jerarquía geográfica.

Para cada Location sin lat/lon:
  1. Nominatim geocode (geopy, 1 req/s — ToS)
  2. Extraer lat, lon, ciudad, país, arrondissement (Paris), quartier
  3. SET propiedades en el nodo Location
  4. MERGE nodos intermedios y relaciones [:LOCATED_IN]:
       Location -[:LOCATED_IN]-> Arrondissement (si Paris)
                                  └─[:LOCATED_IN]-> City -[:LOCATED_IN]-> Country

Idempotente: solo procesa Location donde lat IS NULL.
FinOps: registra número de requests Nominatim en .geocoding_log.json.
"""

import json
import os
import time
from datetime import datetime
from typing import Optional

import typer
from dotenv import load_dotenv
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
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

# ── 2. FinOps log ─────────────────────────────────────────────────────────────
GEOCODING_LOG = ".geocoding_log.json"


def log_session(n_requested: int, n_found: int, n_failed: int):
    log = {"sessions": []}
    if os.path.exists(GEOCODING_LOG):
        with open(GEOCODING_LOG, "r", encoding="utf-8") as f:
            log = json.load(f)
    log["sessions"].append({
        "date":        datetime.now().isoformat(timespec="seconds"),
        "requested":   n_requested,
        "found":       n_found,
        "failed":      n_failed,
        "note":        "Nominatim (gratuito, rate-limit 1 req/s)",
    })
    log["sessions"] = log["sessions"][-20:]  # mantener últimas 20 sesiones
    with open(GEOCODING_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ── 3. Helpers de direcciones ─────────────────────────────────────────────────
def get_arrondissement(address: dict) -> Optional[str]:
    """Detecta arrondissement parisino desde código postal (75001-75020)."""
    postcode = address.get("postcode", "")
    if postcode.startswith("750") and len(postcode) == 5:
        try:
            arr_num = int(postcode[3:])
            if 1 <= arr_num <= 20:
                return f"Paris 1er" if arr_num == 1 else f"Paris {arr_num}e"
        except ValueError:
            pass
    # Fallback: city_district o suburb nominatim
    return address.get("city_district") or address.get("suburb") or None


def parse_address(raw: dict) -> dict:
    """
    Extrae componentes relevantes del dict `address` devuelto por Nominatim.
    """
    address = raw.get("address", {})
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("county")
        or ""
    )
    country     = address.get("country", "")
    country_code = address.get("country_code", "").upper()
    quartier    = address.get("quarter") or address.get("neighbourhood") or ""
    arrondissement = get_arrondissement(address)

    return {
        "lat":            float(raw.get("lat", 0) or 0),
        "lon":            float(raw.get("lon", 0) or 0),
        "city":           city,
        "country":        country,
        "countryCode":    country_code,
        "quartier":       quartier,
        "arrondissement": arrondissement or "",
        "displayName":    raw.get("display_name", ""),
    }


# ── 4. Geocodificación ────────────────────────────────────────────────────────
def geocode_location(
    geocoder,
    name: str,
    city_hint: str,
    pause_sec: float,
) -> Optional[dict]:
    """
    Intenta geocodificar `name` con hasta 3 queries progresivamente más amplias.
    Respeta el rate limit de Nominatim con `pause_sec` entre llamadas.
    """
    queries = [
        name,
        f"{name}, {city_hint}",
        city_hint,  # último recurso: solo la ciudad hint
    ]

    for query in queries:
        time.sleep(pause_sec)
        try:
            result = geocoder.geocode(query, language="es", addressdetails=True, timeout=10)
            if result:
                return parse_address(result.raw)
        except (GeocoderTimedOut, GeocoderServiceError):
            time.sleep(pause_sec * 2)

    return None


# ── 5. Neo4j — escribir geocoordinadas ───────────────────────────────────────
def write_location_geo(session, loc_name: str, geo: dict):
    session.run("""
        MATCH (l:Location {name: $name})
        SET l.lat            = $lat,
            l.lon            = $lon,
            l.city           = $city,
            l.country        = $country,
            l.countryCode    = $countryCode,
            l.quartier       = $quartier,
            l.arrondissement = $arrondissement,
            l.displayName    = $displayName,
            l.geocodedAt     = $geocodedAt
    """,
        name            = loc_name,
        geocodedAt      = datetime.now().isoformat(timespec="seconds"),
        **{k: geo[k] for k in ["lat", "lon", "city", "country", "countryCode",
                                "quartier", "arrondissement", "displayName"]},
    )


# ── 6. Neo4j — jerarquía [:LOCATED_IN] ───────────────────────────────────────
def write_hierarchy(session, loc_name: str, geo: dict):
    """
    Crea (si no existen) nodos City y Country, y los encadena con [:LOCATED_IN].

    Paris: Location → Arrondissement → City(Paris) → Country(France)
    Otros: Location → City → Country
    """
    city    = geo.get("city", "")
    country = geo.get("country", "")
    arr     = geo.get("arrondissement", "")

    if not city and not country:
        return

    # Country
    if country:
        session.run("""
            MERGE (co:Country {name: $country})
            SET co.countryCode = $code
        """, country=country, code=geo.get("countryCode", ""))

    # City → Country
    if city and country:
        session.run("""
            MERGE (ci:City {name: $city})
            SET ci.country = $country
            WITH ci
            MATCH (co:Country {name: $country})
            MERGE (ci)-[:LOCATED_IN]->(co)
        """, city=city, country=country)
    elif city:
        session.run("MERGE (:City {name: $city})", city=city)

    # Para Paris: arrondissement → City
    if arr and city:
        session.run("""
            MERGE (ar:Arrondissement {name: $arr})
            WITH ar
            MATCH (ci:City {name: $city})
            MERGE (ar)-[:LOCATED_IN]->(ci)
        """, arr=arr, city=city)

    # Location → Arrondissement (si Paris) o City (si no)
    if arr:
        session.run("""
            MATCH (l:Location {name: $loc})
            MERGE (ar:Arrondissement {name: $arr})
            MERGE (l)-[:LOCATED_IN]->(ar)
        """, loc=loc_name, arr=arr)
    elif city:
        session.run("""
            MATCH (l:Location {name: $loc})
            MATCH (ci:City {name: $city})
            MERGE (l)-[:LOCATED_IN]->(ci)
        """, loc=loc_name, city=city)


# ── 7. Pipeline principal ─────────────────────────────────────────────────────
def run_geocoding(
    city_hint: str  = "Paris",
    pause_sec: float = 1.1,
    dry_run: bool   = False,
    batch_size: int = 50,
):
    print("\n🌍 Geocodificación de Location nodes")
    print("=" * 55)

    with driver.session() as session:
        locations = session.run("""
            MATCH (l:Location)
            WHERE l.lat IS NULL
            RETURN l.name AS name
            ORDER BY l.name
        """).data()

    if not locations:
        print("  ✅ Todas las localizaciones ya geocodificadas.")
        return

    names = [r["name"] for r in locations if r.get("name")]
    print(f"  🔍 {len(names)} localizaciones pendientes")
    print(f"  ⏱️  Rate limit: {pause_sec}s/req → ~{len(names) * pause_sec / 60:.1f} min estimado")
    print(f"  🏙️  City hint: {city_hint}\n")

    geocoder = Nominatim(user_agent="hub-cultural-du/1.0 (research)")

    n_found  = 0
    n_failed = 0

    for i in tqdm(range(0, len(names), batch_size), desc="  geocoding"):
        batch = names[i: i + batch_size]

        for name in batch:
            geo = geocode_location(geocoder, name, city_hint, pause_sec)

            if not geo or (geo["lat"] == 0 and geo["lon"] == 0):
                n_failed += 1
                if dry_run:
                    print(f"  [dry-run] ❌ No encontrado: {name!r}")
                continue

            n_found += 1
            if dry_run:
                print(
                    f"  [dry-run] ✅ {name!r} → "
                    f"lat={geo['lat']:.4f} lon={geo['lon']:.4f} "
                    f"city={geo['city']!r} arr={geo['arrondissement']!r}"
                )
                continue

            with driver.session() as session:
                write_location_geo(session, name, geo)
                write_hierarchy(session, name, geo)

    if not dry_run:
        log_session(len(names), n_found, n_failed)

    print(f"\n  ✅ Geocodificadas : {n_found}")
    print(f"  ❌ No encontradas : {n_failed}")
    print(f"  💰 FinOps — {len(names)} requests Nominatim (gratuito, ~1 req/s)")


# ── 8. Resumen ────────────────────────────────────────────────────────────────
def print_coverage():
    print("\n📊 Cobertura de geocodificación")
    print("=" * 55)
    with driver.session() as session:
        stats = session.run("""
            MATCH (l:Location)
            RETURN count(l) AS total, count(l.lat) AS geocoded
        """).single()
        countries = session.run("""
            MATCH (l:Location) WHERE l.country IS NOT NULL
            RETURN l.country AS country, count(*) AS n ORDER BY n DESC LIMIT 8
        """).data()
        cities = session.run("""
            MATCH (ci:City)
            RETURN count(ci) AS n
        """).single()
        hierarchy = session.run("""
            MATCH ()-[:LOCATED_IN]->() RETURN count(*) AS n
        """).single()

    total    = stats["total"]
    geocoded = stats["geocoded"]
    pct      = round(geocoded / total * 100, 1) if total else 0

    print(f"\n  Location total    : {total}")
    print(f"  Geocodificadas    : {geocoded}  ({pct}%)")
    print(f"  Nodos City        : {cities['n']}")
    print(f"  Relaciones LOCATED_IN: {hierarchy['n']}")

    if countries:
        print("\n  Por país:")
        for row in countries:
            print(f"    {row['country']:<30} {row['n']:>4}")


# ── 9. CLI ────────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    city_hint: str = typer.Option(
        "Paris, France", "--city-hint",
        help="Ciudad de fallback cuando el nombre de Location es ambiguo."
    ),
    pause_sec: float = typer.Option(
        1.1, "--pause-sec",
        help="Segundos de espera entre requests Nominatim (ToS: mín. 1s)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Mostrar resultados sin escribir en Neo4j."
    ),
    batch_size: int = typer.Option(
        50, "--batch-size",
        help="Tamaño de lote para tqdm (no afecta requests)."
    ),
    summary: bool = typer.Option(
        True, "--summary/--no-summary",
        help="Mostrar resumen de cobertura al final."
    ),
):
    """
    Geocodifica Location nodes con Nominatim y construye jerarquía geográfica.

    Crea nodos :City, :Country y :Arrondissement (para París) conectados con
    relaciones [:LOCATED_IN]. Respeta el rate-limit de Nominatim (1 req/s).

    Registra sesiones en .geocoding_log.json (gitignoreado).
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    run_geocoding(
        city_hint=city_hint,
        pause_sec=pause_sec,
        dry_run=dry_run,
        batch_size=batch_size,
    )

    if summary:
        print_coverage()

    driver.close()
    print("\n✅ Geocodificación completa.")


if __name__ == "__main__":
    app()
