"""
find_offscope_accounts.py — Hub Cultural DU

Por qué existe: Diego revisando eventos en review_events.py (staging)
nota a mano que varios vienen de fuera de Francia. Ya existe un mecanismo
para tagear cuentas fuera de alcance en Neo4j (exclude_accounts.py, lee
config/excluded_accounts.json) — pero ese archivo solo tenía las 4 cuentas
de Alianzas Francesas en Colombia encontradas en DD-045/2026-08-15, y
nada en el proyecto hasta ahora RASTREABA cuáles cuentas seed siguen
produciendo eventos fuera de Francia para poder sacarlas también de
config/seeds_*.json (y así dejar de pagar por re-scrapearlas).

Qué hace (100% lectura, no escribe nada en Neo4j ni en ningún archivo):

  1. Bucket A — "confirmado por geocodificación": eventos cuyo
     (e:Event)-[:LOCATED_AT]->(:Location) ya fue geocodificado por
     4_enrich_locations.py y su countryCode (ISO, vía Nominatim) no es
     "FR". Esta es la señal más confiable disponible hoy en el grafo.
     Sesgo conocido: solo cubre eventos con Location ya geocodificado
     con éxito — nunca genera falsos positivos por el propio diseño de
     4_enrich_locations.py (si falló, lat/countryCode quedan NULL, no
     una coordenada inventada — ver DD-045), pero sí puede tener falsos
     negativos (eventos fuera de Francia que Nominatim aún no procesó,
     o para los que solo pudo resolver con el hint genérico y por lo
     tanto cayó "dentro" de Francia por defecto).

  2. Bucket B — "sin geocodificar, revisar a mano": eventos sin
     Location geocodificada con countryCode='FR' confirmado (ni siquiera
     tienen Location, o la tienen pero sin countryCode aún) cuyo texto
     (locationName / cityName / exactAddress, extraído por el LLM en
     4_enrich_events_extract.py) contiene una palabra de una lista corta
     de países/ciudades no-francesas conocidas por aparecer en este
     proyecto (Colombia y alrededores, por el origen de la diáspora, más
     algunos ejemplos ya documentados en DD-072: Madrid/Berlín/Houston).
     Esto es una heurística de texto, NUNCA una conclusión — se imprime
     para que Diego lo confirme con sus propios ojos, mismo espíritu que
     el resto del pipeline ("preferible null a inventar", nunca decidir
     solo).

  Para cada cuenta encontrada en cualquiera de los dos buckets, se
  reporta además:
    - en qué archivo(s) config/seeds_*.json aparece como seed literal
      (si aparece) — porque sacarla de ahí es lo que de verdad detiene
      el gasto de volver a scrapearla
    - si ya está en config/excluded_accounts.json
    - manualDataCuratedAt / geoZone de la cuenta (contexto: una cuenta
      curada y con base en Francia puede anunciar legítimamente un
      evento puntual en el extranjero — eso no la vuelve "fuera de
      alcance" como cuenta, solo ese evento puntual).

Al final imprime un bloque JSON listo para copiar dentro de
config/excluded_accounts.json con las cuentas del Bucket A que aún no
estén ahí (las del Bucket B hay que confirmarlas a mano primero, por
ser heurística de texto).

Uso:
    python find_offscope_accounts.py                  # reporte CORTO (1 línea por cuenta, top 20 por bucket)
    python find_offscope_accounts.py --verbose         # reporte largo, con muestras de eventos por cuenta (el formato original)
    python find_offscope_accounts.py --top 0           # sin límite de filas (--top 0 = todas)
    python find_offscope_accounts.py --csv out.csv     # además exporta el detalle COMPLETO a CSV, sin importar --top
    python find_offscope_accounts.py --min-events 2    # solo cuentas con >= N eventos marcados (default 1)
    python find_offscope_accounts.py --keep elcafetal.paris --write-excluded
        # mergea TODO el Bucket A (menos las de --keep) directo en
        # config/excluded_accounts.json, sin duplicar lo que ya estaba ahí.
        # Sigue sin tocar Neo4j -- ese paso sigue siendo exclude_accounts.py.

No requiere --dry-run: no escribe nada, nunca. El modo corto es el default
porque el reporte --verbose se comía la terminal en la primera corrida real
(Diego, 2026-09-07) — el CSV sigue siendo la forma de ver TODO sin recortar.
"""

import csv
import glob
import json
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

SEEDS_GLOB = "config/seeds_*.json"
EXCLUDED_PATH = "config/excluded_accounts.json"

# Heurística de texto para el Bucket B — deliberadamente acotada a lo que
# este proyecto ya documentó como patrón real (DD-032, DD-045, DD-072),
# no una lista exhaustiva de geografía mundial. Ampliar si aparecen casos
# nuevos que esta lista no atrapa.
NON_FRANCE_KEYWORDS = [
    "colombia", "bogotá", "bogota", "medellín", "medellin", "cali",
    "manizales", "pereira", "barranquilla", "cartagena",
    "méxico", "mexico", "cdmx",
    "españa", "espana", "madrid", "barcelona",
    "argentina", "buenos aires",
    "chile", "santiago de chile",
    "perú", "peru", "lima",
    "venezuela", "caracas",
    "ecuador", "quito",
    "estados unidos", "houston", "usa",
    "alemania", "berlín", "berlin",
    "bélgica", "belgica", "bruselas",
]


def load_seeds_index() -> dict[str, list[str]]:
    """username -> [archivo_seeds, ...] en los que aparece como handle."""
    index: dict[str, list[str]] = {}
    for path in glob.glob(SEEDS_GLOB):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for entry in data.get("seeds", []):
            handle = (entry.get("handle") or "").strip().lower()
            if handle:
                index.setdefault(handle, []).append(os.path.basename(path))
    return index


def load_excluded_set() -> set[str]:
    if not os.path.exists(EXCLUDED_PATH):
        return set()
    with open(EXCLUDED_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {e["username"].strip().lower() for e in data.get("accounts", [])}


BUCKET_A_QUERY = """
MATCH (e:Event)-[:LOCATED_AT]->(l:Location)
WHERE l.countryCode IS NOT NULL AND l.countryCode <> 'FR'
OPTIONAL MATCH (a:Account)-[:PUBLISHED]->(p:Post)-[:MENTIONS_EVENT]->(e)
WITH coalesce(a.username, e.sourceAuthor) AS username,
     a.manualDataCuratedAt AS curatedAt, a.geoZone AS accountGeoZone,
     e.id AS eventId, e.title AS title, e.locationName AS locationName,
     l.country AS country, l.countryCode AS countryCode, l.geocodeConfidence AS confidence
RETURN username, curatedAt, accountGeoZone,
       collect({eventId: eventId, title: title, locationName: locationName,
                country: country, countryCode: countryCode, confidence: confidence}) AS events
ORDER BY size(events) DESC
"""

BUCKET_B_QUERY = """
MATCH (e:Event)
WHERE NOT EXISTS {
    MATCH (e)-[:LOCATED_AT]->(l:Location) WHERE l.countryCode = 'FR'
}
OPTIONAL MATCH (a:Account)-[:PUBLISHED]->(p:Post)-[:MENTIONS_EVENT]->(e)
RETURN coalesce(a.username, e.sourceAuthor) AS username,
       a.manualDataCuratedAt AS curatedAt, a.geoZone AS accountGeoZone,
       e.id AS eventId, e.title AS title, e.locationName AS locationName,
       e.cityName AS cityName, e.exactAddress AS exactAddress
"""


def text_flags(ev: dict) -> str | None:
    haystack = " ".join(filter(None, [ev.get("locationName"), ev.get("cityName"), ev.get("exactAddress")])).lower()
    if not haystack.strip():
        return None
    for kw in NON_FRANCE_KEYWORDS:
        if kw in haystack:
            return kw
    return None


def _fmt_row_compact(r: dict) -> str:
    flag = "★" if r["curada"] else " "
    excl = "excl" if r["ya_excluida"] else "    "
    seed = r["seed_en"] if r["seed_en"] else "(no-seed)"
    return (f"  {r['username'][:28]:<28} {r['n_eventos']:>3}ev  {flag} {excl}  "
            f"{r['paises'][:26]:<26} seed:{seed}")


@app.command()
def main(
    csv_out: str = typer.Option(None, "--csv", help="Ruta opcional para exportar el detalle COMPLETO a CSV (sin recortar por --top)"),
    min_events: int = typer.Option(1, "--min-events", help="Solo mostrar cuentas con al menos N eventos marcados"),
    top: int = typer.Option(20, "--top", help="Máximo de cuentas a imprimir por bucket en consola (0 = sin límite). No afecta al --csv ni al bloque de exclusión."),
    verbose: bool = typer.Option(False, "--verbose", help="Reporte largo con muestras de eventos por cuenta, en vez de 1 línea por cuenta"),
    keep: str = typer.Option("", "--keep", help="Usernames a NO incluir en el bloque/--write-excluded aunque el Bucket A los marque (coma-separados, ej. elcafetal.paris)"),
    write_excluded: bool = typer.Option(
        False, "--write-excluded",
        help="En vez de solo imprimir el bloque JSON, lo mergea de una vez dentro de config/excluded_accounts.json "
             "(sin duplicar usernames ya presentes). Incluye SIEMPRE la lista completa del Bucket A menos --keep, "
             "sin importar --top. Sigue sin tocar Neo4j -- ese paso sigue siendo exclude_accounts.py, aparte.",
    ),
):
    keep_set = {u.strip().lower() for u in keep.split(",") if u.strip()}
    seeds_index = load_seeds_index()
    excluded = load_excluded_set()
    print(f"📋 {len(seeds_index)} handles distintos indexados desde {SEEDS_GLOB}")
    print(f"📋 {len(excluded)} cuenta(s) ya en {EXCLUDED_PATH}\n")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    csv_rows = []

    # ── Bucket A — recolectar (sin imprimir todavía) ────────────────────
    with driver.session() as session:
        rows_a = session.run(BUCKET_A_QUERY).data()

    bucket_a_usernames = set()
    bucket_a_rows = []  # (row, events) para el modo --verbose
    for row in rows_a:
        username = row["username"]
        events = row["events"]
        if not username or len(events) < min_events:
            continue
        bucket_a_usernames.add(username.lower())
        seed_files = seeds_index.get(username.lower(), [])
        already_excluded = username.lower() in excluded
        countries = sorted({ev["country"] or ev["countryCode"] for ev in events})
        r = {
            "bucket": "A_geocodificado", "username": username, "n_eventos": len(events),
            "paises": ", ".join(countries), "curada": bool(row["curatedAt"]), "geoZone_cuenta": row["accountGeoZone"] or "",
            "seed_en": ", ".join(seed_files), "ya_excluida": already_excluded,
        }
        csv_rows.append(r)
        bucket_a_rows.append((r, row, events))

    # ── Bucket B — recolectar (sin imprimir todavía) ────────────────────
    with driver.session() as session:
        rows_b = session.run(BUCKET_B_QUERY).data()

    by_account: dict[str, dict] = {}
    for row in rows_b:
        flag = text_flags(row)
        if not flag:
            continue
        username = row["username"]
        if not username or username.lower() in bucket_a_usernames:
            continue  # ya reportada con evidencia dura en el Bucket A
        acc = by_account.setdefault(username, {
            "curatedAt": row["curatedAt"], "accountGeoZone": row["accountGeoZone"], "events": [],
        })
        acc["events"].append({"eventId": row["eventId"], "title": row["title"], "flag": flag,
                               "locationName": row["locationName"], "cityName": row["cityName"],
                               "exactAddress": row["exactAddress"]})

    bucket_b_rows = []  # (row, acc) para el modo --verbose
    for username, acc in sorted(by_account.items(), key=lambda kv: -len(kv[1]["events"])):
        if len(acc["events"]) < min_events:
            continue
        seed_files = seeds_index.get(username.lower(), [])
        already_excluded = username.lower() in excluded
        flags = sorted({e["flag"] for e in acc["events"]})
        r = {
            "bucket": "B_heuristica_revisar", "username": username, "n_eventos": len(acc["events"]),
            "paises": ", ".join(flags), "curada": bool(acc["curatedAt"]), "geoZone_cuenta": acc["accountGeoZone"] or "",
            "seed_en": ", ".join(seed_files), "ya_excluida": already_excluded,
        }
        csv_rows.append(r)
        bucket_b_rows.append((r, username, acc))

    driver.close()

    # ── Impresión ────────────────────────────────────────────────────────
    limit = None if top == 0 else top
    print("=" * 78)
    print(f"BUCKET A — confirmado por geocodificación (countryCode ≠ 'FR')  [{len(bucket_a_rows)} cuenta(s)]")
    print("=" * 78)
    if not bucket_a_rows:
        print("(ningún evento con Location geocodificada fuera de Francia)")
    elif verbose:
        for r, row, events in bucket_a_rows[:limit]:
            print(f"\n@{r['username']} — {r['n_eventos']} evento(s) fuera de Francia — países: {r['paises']}")
            print(f"   curada: {'sí (' + str(row['curatedAt']) + ')' if row['curatedAt'] else 'NO'}"
                  f" · geoZone de cuenta: {row['accountGeoZone'] or '—'}")
            print(f"   seed en: {r['seed_en'] or '(no es seed literal — llegó por descubrimiento automático)'}")
            print(f"   ya excluida en config: {'sí' if r['ya_excluida'] else 'NO'}")
            for ev in events[:3]:
                print(f"     · {ev['title']} — \"{ev['locationName']}\" → {ev['country']} ({ev['confidence']})")
            if len(events) > 3:
                print(f"     · … y {len(events) - 3} más")
    else:
        print("  (★=cuenta curada · excl=ya en excluded_accounts.json · seed:archivo o (no-seed))")
        for r in bucket_a_rows[:limit]:
            print(_fmt_row_compact(r[0]))
    if limit is not None and len(bucket_a_rows) > limit:
        print(f"  … y {len(bucket_a_rows) - limit} cuenta(s) más — usa --top 0 o --csv para verlas todas")

    print("\n" + "=" * 78)
    print(f"BUCKET B — sin geocodificar / countryCode='FR' no confirmado — revisar a mano  [{len(bucket_b_rows)} cuenta(s)]")
    print("=" * 78)
    if not bucket_b_rows:
        print("(ningún evento sin geocodificar con texto sospechoso, según la lista de palabras actual)")
    elif verbose:
        for r, username, acc in bucket_b_rows[:limit]:
            print(f"\n@{username} — {r['n_eventos']} evento(s) con texto sospechoso — palabras: {r['paises']}")
            print(f"   curada: {'sí (' + str(acc['curatedAt']) + ')' if acc['curatedAt'] else 'NO'}"
                  f" · geoZone de cuenta: {acc['accountGeoZone'] or '—'}")
            print(f"   seed en: {r['seed_en'] or '(no es seed literal — llegó por descubrimiento automático)'}")
            print(f"   ya excluida en config: {'sí' if r['ya_excluida'] else 'NO'}")
            for ev in acc["events"][:3]:
                print(f"     · {ev['title']} — locationName=\"{ev['locationName']}\" cityName=\"{ev['cityName']}\" exactAddress=\"{ev['exactAddress']}\"")
            if len(acc["events"]) > 3:
                print(f"     · … y {len(acc['events']) - 3} más")
    else:
        print("  (★=cuenta curada · excl=ya en excluded_accounts.json · seed:archivo o (no-seed))")
        for r, _, _ in bucket_b_rows[:limit]:
            print(_fmt_row_compact(r))
    if limit is not None and len(bucket_b_rows) > limit:
        print(f"  … y {len(bucket_b_rows) - limit} cuenta(s) más — usa --top 0 o --csv para verlas todas")

    # ── CSV opcional ──────────────────────────────────────────────────
    if csv_out and csv_rows:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\n💾 CSV exportado: {csv_out} ({len(csv_rows)} fila(s))")

    # ── Bloque listo para copiar a excluded_accounts.json (o --write-excluded) ──
    to_add_all = [r for r in csv_rows if r["bucket"] == "A_geocodificado" and not r["ya_excluida"]]
    kept_out = [r for r in to_add_all if r["username"].lower() in keep_set]
    to_add = [r for r in to_add_all if r["username"].lower() not in keep_set]

    if kept_out:
        print("\n🟢 Conservadas por --keep (NO se incluyen en el bloque de exclusión): "
              + ", ".join(f"@{r['username']}" for r in kept_out))

    if to_add:
        snippet = [
            {"username": r["username"], "reason": f"Detectado por find_offscope_accounts.py — {r['n_eventos']} evento(s) geocodificados en {r['paises']}, fuera de Île-de-France."}
            for r in to_add
        ]

        if write_excluded:
            existing_entries = []
            if os.path.exists(EXCLUDED_PATH):
                with open(EXCLUDED_PATH, "r", encoding="utf-8") as f:
                    existing_entries = json.load(f).get("accounts", [])
            existing_usernames = {e["username"].strip().lower() for e in existing_entries}
            new_entries = [e for e in snippet if e["username"].lower() not in existing_usernames]
            merged = existing_entries + new_entries
            with open(EXCLUDED_PATH, "w", encoding="utf-8") as f:
                json.dump({"accounts": merged}, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"\n✍️  {EXCLUDED_PATH} actualizado: +{len(new_entries)} cuenta(s) nueva(s) "
                  f"({len(existing_entries)} → {len(merged)} en total).")
            if len(new_entries) < len(snippet):
                print(f"   ({len(snippet) - len(new_entries)} ya estaban en el archivo, no se duplicaron)")
        else:
            print("\n" + "=" * 78)
            shown = to_add[:limit] if limit is not None else to_add
            print(f"Para copiar dentro de config/excluded_accounts.json (solo Bucket A, {len(shown)}/{len(to_add)}"
                  " cuenta(s) — evidencia geocodificada, revisa igual antes de pegar):")
            print("=" * 78)
            print(json.dumps(snippet[:len(shown)], ensure_ascii=False, indent=2))
            if len(to_add) > len(shown):
                print(f"… {len(to_add) - len(shown)} más recortadas de este bloque — usa --top 0 para incluirlas "
                      "todas, o --write-excluded para escribirlas directo al archivo sin importar --top.")

        print(
            "\nDespués (ya sea que pegaste el bloque o usaste --write-excluded), corré:\n"
            "  python exclude_accounts.py --dry-run   # confirmar conteos\n"
            "  python exclude_accounts.py             # tagear de verdad en Neo4j\n"
            "Y para dejar de gastar en volver a scrapearlas, sacá el mismo\n"
            "'handle' a mano de cada config/seeds_*.json listado arriba como\n"
            "'seed en: ...' (si no dice nada ahí, no es un seed literal — entró\n"
            "por descubrimiento automático RELATED_TO y no hace falta tocar\n"
            "ningún seeds_*.json para ella)."
        )

    print(f"\nTotal cuentas reportadas: {len(csv_rows)} (Bucket A: {sum(1 for r in csv_rows if r['bucket'].startswith('A'))}, "
          f"Bucket B: {sum(1 for r in csv_rows if r['bucket'].startswith('B'))})")


if __name__ == "__main__":
    app()
