"""
report_approved_field_coverage.py — Hub Cultural DU

Por qué existe: Diego preguntó cuántas cuentas con eventos APROBADOS (ya
pasaron por review_events.py — no :PendingReview, no :Rejected) tienen
geoZone vacío, y sospechaba que las demás columnas heredadas de la
curación manual (artType, institutionType, culturalIdentity,
parentInstitution, photoPermission) probablemente tengan el mismo problema.

Por qué esto es casi binario por CUENTA, no por evento: todos estos campos
se copian una sola vez desde el :Account curado hacia cada :Event al
crearlo (ver 4_enrich_events_extract.py, post.get("geoZone") etc., líneas
~2083-2092) — nunca se recalculan por evento. Así que si una cuenta nunca
pasó por load_manual_account_categorization.py, TODOS sus eventos van a
tener estos campos vacíos por igual; si sí pasó, pueden faltar campos
sueltos (p.ej. parentInstitution en blanco porque de verdad no aplica).
Por eso el reporte da dos vistas: por cuenta (qué fracción de las cuentas
detrás del catálogo en vivo está sin curar del todo) y por evento (qué
fracción de lo que el visitante ve en el sitio le falta cada dato).

100% lectura -- no escribe nada en Neo4j ni en ningún archivo.

"Aprobado" = lo que hoy sale en el sitio en potencia: sin :PendingReview
ni :Rejected. Esto es más amplio que el propio site/data.json (que además
aplica la ventana de retención de --past-days, ver 5_export_dashboard_data.py) --
aquí interesa todo lo aprobado alguna vez, no solo lo que cabe en la
ventana de fechas recientes.

Uso:
    python report_approved_field_coverage.py
    python report_approved_field_coverage.py --csv out.csv   # detalle por cuenta a CSV
"""

import csv as csv_module
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

FIELDS = ["geoZone", "artType", "institutionType", "culturalIdentity", "parentInstitution"]

APPROVED_EVENTS_QUERY = """
MATCH (e:Event)
WHERE NOT 'PendingReview' IN labels(e) AND NOT 'Rejected' IN labels(e)
OPTIONAL MATCH (a:Account)-[:PUBLISHED]->(p:Post)-[:MENTIONS_EVENT]->(e)
RETURN coalesce(a.username, e.sourceAuthor) AS username,
       a.manualDataCuratedAt AS curatedAt,
       e.geoZone AS geoZone, e.artType AS artType, e.institutionType AS institutionType,
       e.culturalIdentity AS culturalIdentity, e.parentInstitution AS parentInstitution,
       e.photoPermission AS photoPermission
"""


@app.command()
def main(
    csv_out: str = typer.Option(None, "--csv", help="Ruta opcional para exportar el detalle por cuenta a CSV"),
):
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    with driver.session() as session:
        rows = session.run(APPROVED_EVENTS_QUERY).data()
    driver.close()

    if not rows:
        print("No hay eventos aprobados (ni :PendingReview ni :Rejected) en la base.")
        return

    total_events = len(rows)
    accounts = {}  # username -> {"curatedAt": ..., "events": [rows]}
    for r in rows:
        username = r["username"] or "(sin sourceAuthor)"
        acc = accounts.setdefault(username, {"curatedAt": r["curatedAt"], "events": []})
        acc["events"].append(r)

    total_accounts = len(accounts)
    curated_accounts = sum(1 for a in accounts.values() if a["curatedAt"])
    uncurated_accounts = total_accounts - curated_accounts

    print("=" * 78)
    print(f"EVENTOS APROBADOS: {total_events}   ·   CUENTAS DISTINTAS DETRÁS: {total_accounts}")
    print("=" * 78)
    print(f"  Cuentas SIN curar (manualDataCuratedAt IS NULL): {uncurated_accounts} "
          f"({uncurated_accounts / total_accounts * 100:.1f}% de las cuentas)")
    print(f"  Cuentas curadas:                                  {curated_accounts} "
          f"({curated_accounts / total_accounts * 100:.1f}% de las cuentas)")
    uncurated_event_count = sum(len(a["events"]) for u, a in accounts.items() if not a["curatedAt"])
    print(f"  Eventos que caen bajo una cuenta SIN curar:       {uncurated_event_count} "
          f"({uncurated_event_count / total_events * 100:.1f}% de los eventos aprobados)\n")

    print("-" * 78)
    print("POR CAMPO (geoZone y las demás heredadas de la curación manual):")
    print("-" * 78)
    print(f"{'campo':<20} {'eventos sin dato':>18} {'% eventos':>10}   {'cuentas sin dato':>17} {'% cuentas':>10}")
    for field in FIELDS:
        events_missing = sum(1 for r in rows if not r.get(field))
        accounts_missing = sum(1 for a in accounts.values() if all(not e.get(field) for e in a["events"]))
        print(f"{field:<20} {events_missing:>18} {events_missing / total_events * 100:>9.1f}%   "
              f"{accounts_missing:>17} {accounts_missing / total_accounts * 100:>9.1f}%")

    # photoPermission aparte: None (nunca se preguntó) y False (dijo que no)
    # se TRATAN igual en el sitio (nunca se muestra la foto real, ver DD-060),
    # pero son cosas distintas y vale la pena no mezclarlas en el conteo.
    photo_none = sum(1 for r in rows if r.get("photoPermission") is None)
    photo_false = sum(1 for r in rows if r.get("photoPermission") is False)
    photo_true = sum(1 for r in rows if r.get("photoPermission") is True)
    print(f"\nphotoPermission (aparte -- None y False se comportan igual en el sitio, DD-060):")
    print(f"  None (nunca se preguntó): {photo_none} eventos ({photo_none / total_events * 100:.1f}%)")
    print(f"  False (dijo que no):      {photo_false} eventos ({photo_false / total_events * 100:.1f}%)")
    print(f"  True (autorizó foto):     {photo_true} eventos ({photo_true / total_events * 100:.1f}%)")

    print(
        "\nNota: como estos campos se heredan de la cuenta una sola vez al crear el\n"
        "evento (nunca se recalculan), 'cuentas sin dato' de la tabla de arriba es\n"
        "casi siempre = cuentas sin curar del todo (ver el bloque de arriba) --\n"
        "salvo que la planilla curada haya dejado esa columna puntual en blanco\n"
        "para una cuenta que sí está curada (ej. parentInstitution en None porque\n"
        "de verdad no aplica a esa cuenta)."
    )

    if csv_out:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["username", "curada", "n_eventos_aprobados"] + FIELDS + ["photoPermission"]
            writer = csv_module.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for username, acc in sorted(accounts.items(), key=lambda kv: -len(kv[1]["events"])):
                row = {
                    "username": username, "curada": bool(acc["curatedAt"]),
                    "n_eventos_aprobados": len(acc["events"]),
                }
                for field in FIELDS:
                    row[field] = "vacío" if all(not e.get(field) for e in acc["events"]) else "con dato"
                photo_vals = {e.get("photoPermission") for e in acc["events"]}
                row["photoPermission"] = "/".join(str(v) for v in photo_vals)
                writer.writerow(row)
        print(f"\n💾 CSV exportado: {csv_out} ({total_accounts} cuenta(s))")


if __name__ == "__main__":
    app()
