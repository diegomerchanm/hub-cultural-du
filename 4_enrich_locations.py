"""
Geocodificación de nodos (:Location) y creación de jerarquía geográfica.

Para cada Location sin lat/lon (o sin geocodeConfidence — ver nota de
idempotencia abajo):
  1. Nominatim geocode (geopy, 1 req/s — ToS)
  2. Extraer lat, lon, ciudad, país, arrondissement (Paris), quartier
  3. SET propiedades en el nodo Location
  4. MERGE nodos intermedios y relaciones [:LOCATED_IN]:
       Location -[:LOCATED_IN]-> Arrondissement (si Paris)
                                  └─[:LOCATED_IN]-> City -[:LOCATED_IN]-> Country

Idempotente: procesa Location donde `lat IS NULL OR geocodeConfidence IS NULL`
(ver DD-045 punto 6 — el criterio viejo, solo `lat IS NULL`, congelaba para
siempre las Location geocodificadas por versiones anteriores y peores del
script; nunca se revisitaban aunque la lógica mejorara).

CAVEAT conocido (auditoría 2026-08-17, DD-045): esta condición es
auto-limitante solo para los ÉXITOS. Una Location que falle la
geocodificación no recibe ninguna escritura (ni geocodeConfidence, ni
limpieza de su lat/lon viejo), así que vuelve a entrar en la cola en cada
corrida futura y, si tenía coordenadas malas de la lógica vieja, las
conserva. Sin decidir todavía si conviene escribir un centinela
(geocodeConfidence='not_found' + lat/lon=null): borraría coordenadas buenas
ante un fallo transitorio de Nominatim. Decisión pendiente de Diego.

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
    Intenta geocodificar `name` con hasta 2 queries progresivamente más amplias.
    Respeta el rate limit de Nominatim con `pause_sec` entre llamadas.

    Devuelve además `geo["confidence"]` indicando qué query tuvo éxito:
    "city_combined" (nombre + hint) o "name_only" (el nombre solo, sin hint).

    ORDEN (fix 2026-08-17, DD-045): se prueba PRIMERO `city_combined` y
    recién si falla se cae a `name_only` — al revés del orden original.
    Motivo, confirmado con un dry-run real de 780 Location: con el orden
    viejo (nombre solo primero), Nominatim resuelve casi CUALQUIER string
    contra algún lugar del mundo entero sin restricción geográfica alguna
    — "Consulado" cae en Ciudad de México pese a hint="Accra", "Cartier" en
    Nueva York, "DE LA" en Bari (Italia), "Embajada Argentina" en Beijing,
    "IHEAL" (instituto real de París) en Reino Unido — y como ese tier va
    primero, el hint por-evento (que en 210/780 casos SÍ es correcto)
    nunca llegaba a probarse: 0 de ~500+ resultados exitosos en ese
    dry-run usaron `city_combined`. Este reorden no elimina el ruido de
    nombres genéricos sin ningún candidato plausible ni siquiera con
    hint (esos van a seguir caminando hasta `name_only` y pueden seguir
    fallando mal) — ataca el caso, mayoritario, en el que el hint sí
    tenía la respuesta y nunca se le daba la oportunidad de usarse.

    NOTA (DD-045, rediseño 2026-08-15): existía un tercer tier,
    "city_hint_only", que si `name` y `name+hint` fallaban, geocodificaba
    literalmente el hint solo (ej. "Paris, France") y lo devolvía como si
    fuera un resultado válido — Nominatim casi siempre encuentra algo para
    un hint así de genérico, así que este tier "encontraba" coordenadas con
    apariencia correcta pero sin ninguna relación real con `name`. Se
    confirmó en datos reales: docenas de Location con nombres completamente
    distintos (un teatro en Medellín, un handle de Instagram, una dirección
    real de París) todas cayendo en la MISMA coordenada — el centro de
    "Paris, France" geocodificado a secas. Se retiró el tier: si `name` y
    `name+hint` fallan, la Location se queda sin lat/lon (null) en vez de
    una coordenada inventada — es preferible no saber a inventar, mismo
    criterio que ya usa el prompt del LLM en 4_enrich_events_extract.py
    para city/exact_address ("preferible null a una ubicación adivinada").
    """
    queries = [
        ("city_combined", f"{name}, {city_hint}" if city_hint else None),
        ("name_only", name),
    ]

    for tier, query in queries:
        if not query:
            continue
        time.sleep(pause_sec)
        try:
            result = geocoder.geocode(query, language="es", addressdetails=True, timeout=10)
            if result:
                geo = parse_address(result.raw)
                geo["confidence"] = tier
                return geo
        except (GeocoderTimedOut, GeocoderServiceError):
            time.sleep(pause_sec * 2)

    return None


# ── 5. Neo4j — escribir geocoordinadas ───────────────────────────────────────
def write_location_geo(session, loc_name: str, geo: dict):
    session.run("""
        MATCH (l:Location {name: $name})
        SET l.lat              = $lat,
            l.lon              = $lon,
            l.city             = $city,
            l.country          = $country,
            l.countryCode      = $countryCode,
            l.quartier         = $quartier,
            l.arrondissement   = $arrondissement,
            l.displayName      = $displayName,
            l.geocodeConfidence = $confidence,
            l.geocodedAt       = $geocodedAt
    """,
        name            = loc_name,
        geocodedAt      = datetime.now().isoformat(timespec="seconds"),
        confidence      = geo.get("confidence", ""),
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
        # Hint por-location: la ciudad que Capa 3 (LLM) extrajo del evento
        # que originó ese Location, en vez de un city_hint global fijo
        # (ver DD-033 update 6 — antes TODO se pistaba hacia "Paris" por
        # defecto, forzando resultados franceses para eventos colombianos
        # cuando el nombre exacto no matcheaba). Si ningún evento asociado
        # tiene cityName, cae al --city-hint de la CLI como antes.
        # WHERE l.lat IS NULL OR l.geocodeConfidence IS NULL (no solo lat IS
        # NULL): l.geocodeConfidence nunca se pobló en la práctica en toda la
        # base (ver DD-045) — cualquier Location con lat/lon pero sin
        # geocodeConfidence fue geocodificada por una versión anterior del
        # script, antes de este hint por-evento (o de otras mejoras futuras),
        # y JAMÁS se reprocesa bajo el criterio viejo `lat IS NULL`. Esta
        # condición fuerza un backfill una sola vez: una vez que esta corrida
        # deje geocodeConfidence poblado en todos, las corridas siguientes
        # vuelven a ser tan baratas como antes (equivalente a `lat IS NULL`).
        locations = session.run("""
            MATCH (l:Location)
            WHERE l.lat IS NULL OR l.geocodeConfidence IS NULL
            OPTIONAL MATCH (e:Event)-[:LOCATED_AT]->(l)
            WHERE e.cityName IS NOT NULL AND e.cityName <> ''
            WITH l, collect(DISTINCT e.cityName) AS cityHints
            RETURN l.name AS name, cityHints[0] AS eventCityHint
            ORDER BY l.name
        """).data()

    if not locations:
        print("  ✅ Todas las localizaciones ya geocodificadas.")
        return

    rows = [(r["name"], r.get("eventCityHint")) for r in locations if r.get("name")]
    n_with_own_hint = sum(1 for _, h in rows if h)
    print(f"  🔍 {len(rows)} localizaciones pendientes")
    print(f"  ⏱️  Rate limit: {pause_sec}s/req → ~{len(rows) * pause_sec / 60:.1f} min estimado")
    print(f"  🏙️  City hint por defecto (fallback): {city_hint}")
    print(f"  🎯 Con hint propio de su evento: {n_with_own_hint}/{len(rows)}\n")

    geocoder = Nominatim(user_agent="hub-cultural-du/1.0 (research)")

    n_found  = 0
    n_failed = 0

    for i in tqdm(range(0, len(rows), batch_size), desc="  geocoding"):
        batch = rows[i: i + batch_size]

        for name, own_hint in batch:
            effective_hint = own_hint or city_hint
            geo = geocode_location(geocoder, name, effective_hint, pause_sec)

            if not geo or (geo["lat"] == 0 and geo["lon"] == 0):
                n_failed += 1
                if dry_run:
                    print(f"  [dry-run] ❌ No encontrado: {name!r} (hint={effective_hint!r})")
                continue

            n_found += 1
            if dry_run:
                print(
                    f"  [dry-run] ✅ {name!r} (hint={effective_hint!r}) → "
                    f"lat={geo['lat']:.4f} lon={geo['lon']:.4f} "
                    f"city={geo['city']!r} arr={geo['arrondissement']!r} "
                    f"confidence={geo.get('confidence')!r}"
                )
                continue

            with driver.session() as session:
                write_location_geo(session, name, geo)
                write_hierarchy(session, name, geo)

    if not dry_run:
        log_session(len(rows), n_found, n_failed)

    print(f"\n  ✅ Geocodificadas : {n_found}")
    print(f"  ❌ No encontradas : {n_failed}")
    print(f"  💰 FinOps — {len(rows)} requests Nominatim (gratuito, ~1 req/s)")


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
