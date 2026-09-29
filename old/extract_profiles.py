import os
import json
from apify_client import ApifyClient
from neo4j import GraphDatabase
from dotenv import load_dotenv

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
APIFY_TOKEN    = os.getenv("APIFY_TOKEN")
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not APIFY_TOKEN:
    raise ValueError("Error: APIFY_TOKEN not found in .env file")
if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: Neo4j credentials missing in .env file")

# ── 2. Obtener usernames pendientes desde Neo4j ───────────────────────────────
def get_pending_usernames():
    """
    Devuelve Accounts que existen en el grafo pero aún no tienen
    followersCount — es decir, nunca fueron scrapeados como perfil.
    """
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()

    with driver.session() as session:
        result = session.run("""
            MATCH (a:Account)
            WHERE a.followersCount IS NULL OR a.followersCount = 0
            AND a.username IS NOT NULL
            RETURN a.username AS username
            ORDER BY a.username
        """)
        usernames = [record["username"] for record in result]

    driver.close()
    return usernames

# ── 3. Scraper de perfil puro ─────────────────────────────────────────────────
def scrape_profiles(usernames: list[str]):
    """
    Dado una lista de usernames, extrae solo metadatos de perfil
    sin posts ni contenido — controlando costos.
    """
    client = ApifyClient(APIFY_TOKEN)
    os.makedirs("data_raw", exist_ok=True)

    total_cost = 0.0

    for username in usernames:
        filepath = f"data_raw/profile_{username}.json"

        # Skip si ya tenemos el archivo
        if os.path.exists(filepath):
            print(f"⏭️  Skipping @{username} — archivo ya existe")
            continue

        print(f"🚀 Scraping profile: @{username}...")

        run_input = {
            "usernames": [username],
            "resultsType": "details",
        }

        try:
            run = client.actor("apify/instagram-profile-scraper").call(run_input=run_input)

            dataset_id    = run.default_dataset_id
            dataset_items = client.dataset(dataset_id).list_items().items

            run_cost   = run.usage_total_usd or 0.0
            total_cost += run_cost
            print(f"  💰 Costo: ${run_cost:.4f} USD")

            if not dataset_items:
                print(f"  ⚠️  Sin datos para @{username} — cuenta privada o inexistente")
                continue

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(dataset_items, f, ensure_ascii=False, indent=4)

            print(f"  ✅ Guardado en '{filepath}'")

        except Exception as e:
            print(f"  ❌ Error scrapeando @{username}: {e}")
            continue

    print(f"\n💰 FINOPS — Costo total de esta sesión: ${total_cost:.4f} USD")
    log_run_cost(len(usernames), total_cost)
    return total_cost


# ── 4. Estimación de costo ────────────────────────────────────────────────────
COST_PER_PROFILE_USD = None  # Se calibra automáticamente desde historial

def get_calibrated_cost() -> float:
    """
    Intenta leer el costo real del último run guardado en .apify_cost_log.json.
    Si no existe, usa el precio de catálogo de Apify (~$0.0005 por perfil).
    """
    log_path = ".apify_cost_log.json"
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            log = json.load(f)
        if log.get("runs"):
            costs = [r["cost_per_profile"] for r in log["runs"] if r.get("cost_per_profile", 0) > 0]
            if costs:
                avg = sum(costs) / len(costs)
                print(f"  📊 Costo calibrado desde {len(costs)} run(s) histórico(s): ${avg:.4f} USD/perfil")
                return avg
    # Fallback: precio de catálogo Apify
    print(f"  📊 Sin historial — usando precio de catálogo Apify: $0.0005 USD/perfil")
    return 0.0005

def log_run_cost(n_profiles: int, total_cost: float):
    """Guarda el costo real del run para calibrar estimaciones futuras."""
    log_path = ".apify_cost_log.json"
    log = {"runs": []}
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            log = json.load(f)

    if n_profiles > 0:
        log["runs"].append({
            "profiles": n_profiles,
            "total_cost": round(total_cost, 6),
            "cost_per_profile": round(total_cost / n_profiles, 6),
        })
        # Mantener solo los últimos 10 runs
        log["runs"] = log["runs"][-10:]

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)


# ── 5. Main ───────────────────────────────────────────────────────────────────
def main():
    print("🔍 Consultando Neo4j para Accounts pendientes...\n")
    pending = get_pending_usernames()

    if not pending:
        print("✅ No hay Accounts pendientes de scrapear.")
        return

    # Filtrar los que ya tienen archivo local
    pending_real = [u for u in pending if not os.path.exists(f"data_raw/profile_{u}.json")]
    skipped      = len(pending) - len(pending_real)

    print(f"📋 {len(pending)} Accounts pendientes en Neo4j")
    if skipped:
        print(f"⏭️  {skipped} ya tienen archivo local — se saltarán")
    print(f"🎯 {len(pending_real)} perfiles a scrapear\n")

    if not pending_real:
        print("✅ Todos los perfiles ya están descargados.")
        return

    # Estimación de costo
    print("💸 Estimando costo...")
    cost_per_profile = get_calibrated_cost()
    estimated_min    = cost_per_profile * len(pending_real)
    estimated_max    = estimated_min * 2.5  # margen por variabilidad de compute

    print(f"\n┌─────────────────────────────────────────┐")
    print(f"│  🎯 Perfiles a scrapear : {len(pending_real):>4}            │")
    print(f"│  💰 Estimado mínimo     : ${estimated_min:>8.4f} USD    │")
    print(f"│  💰 Estimado máximo     : ${estimated_max:>8.4f} USD    │")
    print(f"│  ⚠️  Estimación aproximada — puede variar  │")
    print(f"└─────────────────────────────────────────┘\n")

    confirm = input(f"¿Confirmar scraping de {len(pending_real)} perfiles? (s/n): ").strip().lower()
    if confirm != "s":
        print("❌ Operación cancelada.")
        return

    scrape_profiles(pending_real)

    # Registrar costo real para calibrar próxima vez
    # (scrape_profiles retorna el total — ajustamos para capturarlo)
    print("\n✅ Pipeline de extracción de perfiles completo.")


if __name__ == "__main__":
    main()