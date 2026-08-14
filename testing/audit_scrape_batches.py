"""
testing/audit_scrape_batches.py — Hub Cultural DU

Contexto: cleanup_legacy_accounts.py filtra por manualDataCuratedAt +
RELATED_TO (candidate pool) — no tiene forma de distinguir "post de la
scrapeada más reciente" de "post viejo que sigue ahí porque nadie lo
borró". lastUpdatedAt tampoco sirve para eso: 2_build_graph.py lo
reescribe en TODO lo que toca cada vez que corre, viejo y nuevo por
igual (confirmado en sesión: los 1099 posts pendientes tenían el MISMO
lastUpdatedAt, sin importar si el post era de mayo o de agosto).

Este script cruza la fecha de modificación real de data_raw/posts_*.json
(la fuente de verdad de cuándo se scrapeó cada cuenta) contra Neo4j, para
saber cuántos posts pendientes (eventExtracted IS NULL) pertenecen a la
tanda de scraping más reciente vs. a tandas anteriores.

Uso:
    python testing/audit_scrape_batches.py
    python testing/audit_scrape_batches.py --batch-date 2026-08-07
"""

import os
from collections import defaultdict
from datetime import date, datetime

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

app = typer.Typer()

DATA_DIR = "data_raw"

# Mismo criterio de parseo de username que 2_build_graph.py (línea ~389),
# para que "pluralcafe.fr" y demás usernames con puntos se lean idéntico.
def _usernames_by_scrape_date() -> dict[date, list[str]]:
    by_date: dict[date, list[str]] = defaultdict(list)
    for fname in os.listdir(DATA_DIR):
        if not (fname.startswith("posts_") and fname.endswith(".json")):
            continue
        username = fname.replace("posts_", "").replace(".json", "")
        mtime = os.path.getmtime(os.path.join(DATA_DIR, fname))
        scrape_date = datetime.fromtimestamp(mtime).date()
        by_date[scrape_date].append(username)
    return by_date


PENDING_FOR_USERNAMES = """
    UNWIND $usernames AS uname
    MATCH (a:Account {username: uname})-[:PUBLISHED]->(p:Post)
    RETURN
        count(p) AS total_posts,
        count(CASE WHEN p.eventExtracted IS NULL THEN 1 END) AS pendientes,
        count(CASE WHEN p.eventExtracted IS NOT NULL THEN 1 END) AS ya_procesados
"""

# ¿Por qué siguen existiendo cuentas de tandas viejas? cleanup_legacy_accounts.py
# solo borra manualDataCuratedAt IS NULL Y no-candidata (RELATED_TO desde
# curada) — esto responde, para CUALQUIER grupo de usernames, cuántas caen en
# cada uno de esos 3 baldes, sin necesidad de asumir que es "lo mismo que
# Alianza Francesa" — se mide, no se infiere.
CURATION_STATUS_FOR_USERNAMES = """
    UNWIND $usernames AS uname
    MATCH (a:Account {username: uname})
    RETURN
        count(CASE WHEN a.manualDataCuratedAt IS NOT NULL THEN 1 END) AS curadas,
        count(CASE WHEN a.manualDataCuratedAt IS NULL
                    AND a.discoveredViaCuratedAccount = true THEN 1 END) AS candidatas_protegidas,
        count(CASE WHEN a.manualDataCuratedAt IS NULL
                    AND coalesce(a.discoveredViaCuratedAccount, false) = false
                   THEN 1 END) AS sin_curar_ni_proteccion
"""

# El mtime de posts_<username>.json dice cuándo se TOCÓ el archivo, no cuándo
# se creó cada post individual — DD-029 fusiona (merge_and_cap) con lo que ya
# existía y recorta a los 50 más recientes, así que un archivo "de la última
# tanda" puede traer posts viejos re-persistidos. p.firstSeenAt (ON CREATE,
# 2_build_graph.py línea 122) SÍ es confiable a nivel de post individual: si
# el post ya existía de una corrida anterior, firstSeenAt no se mueve aunque
# el archivo se reescriba hoy.
NOVELTY_FOR_USERNAMES = """
    UNWIND $usernames AS uname
    MATCH (a:Account {username: uname})-[:PUBLISHED]->(p:Post)
    WHERE p.eventExtracted IS NULL
    RETURN
        count(CASE WHEN p.firstSeenAt IS NOT NULL
                    AND date(p.firstSeenAt) >= date($batch_date) THEN 1 END) AS genuinamente_nuevos,
        count(CASE WHEN p.firstSeenAt IS NOT NULL
                    AND date(p.firstSeenAt) < date($batch_date) THEN 1 END) AS reciclados_de_antes,
        count(CASE WHEN p.firstSeenAt IS NULL THEN 1 END) AS sin_firstSeenAt
"""

# CURATION_STATUS_FOR_USERNAMES usa MATCH (no OPTIONAL) — si un username local
# no tiene :Account en Neo4j, simplemente no aporta fila y el conteo total
# queda por debajo de len(usernames) sin avisar. Esto lo hace explícito.
ACCOUNTS_FOUND_FOR_USERNAMES = """
    UNWIND $usernames AS uname
    OPTIONAL MATCH (a:Account {username: uname})
    RETURN count(DISTINCT uname) AS total_usernames, count(DISTINCT a) AS cuentas_encontradas
"""


@app.command()
def main(
    batch_date: str = typer.Option(
        None, "--batch-date",
        help="Fecha YYYY-MM-DD a tratar como 'última tanda' (default: la fecha "
             "de archivo posts_*.json más reciente encontrada en data_raw/)."
    ),
):
    if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
        raise ValueError("Error: credenciales Neo4j ausentes en .env")

    by_date = _usernames_by_scrape_date()
    if not by_date:
        print("⚠️  No se encontraron archivos posts_*.json en data_raw/")
        return

    latest = max(by_date.keys())
    target_date = date.fromisoformat(batch_date) if batch_date else latest

    print(f"📁 {sum(len(v) for v in by_date.values())} archivos posts_*.json en {DATA_DIR}/, "
          f"agrupados en {len(by_date)} fechas de scrape distintas:\n")
    for d in sorted(by_date.keys(), reverse=True):
        marker = "  ← última tanda" if d == target_date else ""
        print(f"   · {d}: {len(by_date[d])} cuentas{marker}")

    target_usernames = by_date[target_date]
    other_usernames = [u for d, us in by_date.items() if d != target_date for u in us]

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()

    with driver.session() as session:
        target_stats = session.run(PENDING_FOR_USERNAMES, usernames=target_usernames).single()
        other_stats = session.run(PENDING_FOR_USERNAMES, usernames=other_usernames).single()
        target_curation = session.run(CURATION_STATUS_FOR_USERNAMES, usernames=target_usernames).single()
        other_curation = session.run(CURATION_STATUS_FOR_USERNAMES, usernames=other_usernames).single()
        target_novelty = session.run(
            NOVELTY_FOR_USERNAMES, usernames=target_usernames, batch_date=target_date.isoformat()
        ).single()
        target_found = session.run(ACCOUNTS_FOUND_FOR_USERNAMES, usernames=target_usernames).single()
        other_found = session.run(ACCOUNTS_FOUND_FOR_USERNAMES, usernames=other_usernames).single()

    driver.close()

    print(f"\n{'═'*60}")
    print(f"🆕 Última tanda ({target_date}, {len(target_usernames)} cuentas):")
    print(f"   · posts totales en Neo4j : {target_stats['total_posts']}")
    print(f"   · pendientes (sin procesar): {target_stats['pendientes']}")
    print(f"   · ya procesados            : {target_stats['ya_procesados']}")
    print(f"   · cuentas curadas manualmente   : {target_curation['curadas']}")
    print(f"   · cuentas candidatas protegidas : {target_curation['candidatas_protegidas']}")
    print(f"   · cuentas sin curar NI proteger : {target_curation['sin_curar_ni_proteccion']}")
    if target_found['cuentas_encontradas'] < target_found['total_usernames']:
        print(f"     ⚠️  {target_found['total_usernames'] - target_found['cuentas_encontradas']} de "
              f"{target_found['total_usernames']} usernames locales NO tienen :Account en Neo4j")
    print(f"   · de los {target_stats['pendientes']} pendientes, ¿son posts nuevos de verdad?")
    print(f"       - firstSeenAt >= {target_date} (genuinamente nuevos): {target_novelty['genuinamente_nuevos']}")
    print(f"       - firstSeenAt anterior (reciclados por merge_and_cap): {target_novelty['reciclados_de_antes']}")
    print(f"       - sin firstSeenAt (nodo pre-provenance)              : {target_novelty['sin_firstSeenAt']}")

    print(f"\n🗄️  Todo lo demás ({len(by_date) - 1} fechas anteriores, {len(other_usernames)} cuentas):")
    print(f"   · posts totales en Neo4j : {other_stats['total_posts']}")
    print(f"   · pendientes (sin procesar): {other_stats['pendientes']}")
    print(f"   · ya procesados            : {other_stats['ya_procesados']}")
    print(f"   · cuentas curadas manualmente   : {other_curation['curadas']}")
    print(f"   · cuentas candidatas protegidas : {other_curation['candidatas_protegidas']}")
    print(f"   · cuentas sin curar NI proteger : {other_curation['sin_curar_ni_proteccion']}")
    if other_found['cuentas_encontradas'] < other_found['total_usernames']:
        print(f"     ⚠️  {other_found['total_usernames'] - other_found['cuentas_encontradas']} de "
              f"{other_found['total_usernames']} usernames locales NO tienen :Account en Neo4j "
              f"(cuenta borrada, renombrada, o nunca ingestada — no son 'legacy', son otra cosa)")
    if other_curation['sin_curar_ni_proteccion'] > 0:
        print(f"     ⚠️  estas {other_curation['sin_curar_ni_proteccion']} cuentas NO deberían sobrevivir un "
              f"cleanup_legacy_accounts.py — si el --dry-run de esa corrida dio 0, revisar por qué no las agarró.")

    print(f"\n{'═'*60}")
    print(f"👉 Backlog real de la última tanda: {target_stats['pendientes']} posts pendientes "
          f"({target_novelty['genuinamente_nuevos']} genuinamente nuevos).")
    print(f"   (vs. {other_stats['pendientes']} pendientes de tandas anteriores, que no son el foco ahora)")


if __name__ == "__main__":
    app()
