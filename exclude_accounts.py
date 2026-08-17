"""
exclude_accounts.py — Hub Cultural DU

Por qué existe: el criterio de alcance del proyecto es geográfico (área de
Île-de-France) y cultural, no de nacionalidad (ver DD-045 en
docs/decisions_es.md, punto sobre el cambio a "criterio cultural"). Algunas
cuentas se colaron en el grafo por descubrimiento automático RELATED_TO
(2_build_graph.py crea un :Account por cada relatedProfiles que sugiere
Instagram) sin pasar por curación manual, y resultaron estar físicamente
fuera del área del proyecto — el caso encontrado el 2026-08-15: varias
Alianzas Francesas en Colombia (Manizales, Medellín, Pereira, Cali), que
además tenían coordenadas de geocodificación no confiables (ver DD-045).

Qué hace: lee config/excluded_accounts.json y, para cada username, tagea
(NO borra) el :Account correspondiente con:
    outOfScope       = true
    outOfScopeReason = <texto del config>
    outOfScopeAt      = datetime()

No borra nada — ni la cuenta, ni sus :Post, ni los :Event que haya
publicado. 5_export_dashboard_data.py ya excluye del JSON los eventos y
cuentas con outOfScope=true (ver EVENTS_QUERY/ACCOUNTS_QUERY). Si en algún
momento se decide borrar de verdad, ese es un paso aparte y explícito —
mismo espíritu que cleanup_legacy_accounts.py (tagear primero, borrar
después si hace falta, nunca de una).

Idempotente: correrlo de nuevo sobre una cuenta ya tageada solo actualiza
outOfScopeAt, no rompe nada.

Limitación conocida: esto NO evita que la cuenta vuelva a aparecer como
:Account nueva si otra corrida de 2_build_graph.py la redescubre vía
RELATED_TO desde otra cuenta — ese :Account nuevo no heredaría el tag
outOfScope. Si eso pasa, hay que volver a correr este script. Blindar
2_build_graph.py para que consulte este config ANTES de crear el nodo es
mejora futura, no implementada acá (fuera de alcance de este pedido).

Uso:
    python exclude_accounts.py --dry-run   # cuenta exacto, no tagea nada (ROLLBACK)
    python exclude_accounts.py              # tagea de verdad (COMMIT)
"""

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

CONFIG_PATH = "config/excluded_accounts.json"

TAG_QUERY = """
MATCH (a:Account {username: $username})
OPTIONAL MATCH (a)-[:PUBLISHED]->(p:Post)
OPTIONAL MATCH (p)-[:MENTIONS_EVENT]->(e:Event)
WITH a, count(DISTINCT p) AS postCount, count(DISTINCT e) AS eventCount
SET a.outOfScope       = true,
    a.outOfScopeReason = $reason,
    a.outOfScopeAt     = datetime()
RETURN a.username AS username, postCount, eventCount
"""


@app.command()
def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Corre dentro de una transacción y hace ROLLBACK — cuenta exacto, no tagea nada"
    ),
):
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"No existe {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        entries = json.load(f)["accounts"]

    print(f"📋 {len(entries)} cuentas a excluir, leídas de {CONFIG_PATH}\n")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    found = 0
    not_found = []
    total_posts = 0
    total_events = 0

    with driver.session() as session:
        with session.begin_transaction() as tx:
            for entry in entries:
                username = entry["username"]
                reason = entry["reason"]
                result = tx.run(TAG_QUERY, username=username, reason=reason)
                record = result.single()
                if record is None:
                    not_found.append(username)
                    print(f"  ⚠️  @{username}: no existe ningún :Account con ese username — nada que taguear")
                    continue
                found += 1
                total_posts += record["postCount"]
                total_events += record["eventCount"]
                print(f"  ✓ @{username}: tageada (outOfScope=true) — "
                      f"{record['postCount']} posts, {record['eventCount']} eventos asociados (no se tocan)")

            if dry_run:
                tx.rollback()
                print(f"\n[dry-run] ROLLBACK — nada se guardó. "
                      f"{found}/{len(entries)} cuentas encontradas, "
                      f"{total_posts} posts y {total_events} eventos quedarían asociados a cuentas tageadas.")
            else:
                tx.commit()
                print(f"\n✅ COMMIT — {found}/{len(entries)} cuentas tageadas con outOfScope=true.")

    driver.close()

    if not_found:
        print(f"\n⚠️  {len(not_found)} username(s) del config no se encontraron en Neo4j: {', '.join(not_found)}")

    if not dry_run and found:
        print("\nPróximo paso: correr `python 5_export_dashboard_data.py` de nuevo para "
              "que site/data.json refleje la exclusión.")


if __name__ == "__main__":
    app()
