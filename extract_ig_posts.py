import os
import json
from apify_client import ApifyClient
from dotenv import load_dotenv

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

if not APIFY_TOKEN:
    raise ValueError("Error: APIFY_TOKEN not found in .env file")

client = ApifyClient(APIFY_TOKEN)

# ── 2. Target usernames ───────────────────────────────────────────────────────
TARGET_USERNAMES = [
    "dichaparis",
    "el_man_de_los_chorizos",
    "elcafetal.paris",
    "ivan_argote",
    "calisabor_salsa_calena",
    "alianzafrancesademedellin",
    "educulturaco",
]

RESULTS_LIMIT = 50
ACTOR_ID      = "apify/instagram-post-scraper"

# ── 3. FinOps — calibración de costo ─────────────────────────────────────────
COST_LOG_PATH = ".apify_cost_log.json"


def get_calibrated_cost() -> float:
    if os.path.exists(COST_LOG_PATH):
        with open(COST_LOG_PATH, "r") as f:
            log = json.load(f)
        costs = [r["cost_per_profile"] for r in log.get("runs", []) if r.get("cost_per_profile", 0) > 0]
        if costs:
            avg = sum(costs) / len(costs)
            print(f"  📊 Costo calibrado desde historial: ${avg:.4f} USD/cuenta")
            return avg
    print(f"  📊 Sin historial — usando precio de catálogo Apify: $0.0005 USD/cuenta")
    return 0.0005


def log_run_cost(n_accounts: int, total_cost: float):
    log = {"runs": []}
    if os.path.exists(COST_LOG_PATH):
        with open(COST_LOG_PATH, "r") as f:
            log = json.load(f)
    if n_accounts > 0:
        log["runs"].append({
            "profiles":         n_accounts,
            "total_cost":       round(total_cost, 6),
            "cost_per_profile": round(total_cost / n_accounts, 6),
        })
        log["runs"] = log["runs"][-10:]
    with open(COST_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


# ── 4. Estimación y confirmación ──────────────────────────────────────────────
os.makedirs("data_raw", exist_ok=True)

pending = [u for u in TARGET_USERNAMES
           if not os.path.exists(f"data_raw/posts_{u}.json")]
skipped = len(TARGET_USERNAMES) - len(pending)

print(f"\n📋 {len(TARGET_USERNAMES)} cuentas configuradas")
if skipped:
    print(f"⏭️  {skipped} ya tienen archivo local — se saltarán")
print(f"🎯 {len(pending)} cuentas a scrapear ({RESULTS_LIMIT} posts/cuenta)\n")

if not pending:
    print("✅ Todos los posts ya están descargados.")
    exit(0)

cost_per = get_calibrated_cost()
est_min  = cost_per * len(pending)
est_max  = est_min * 2.5

print(f"┌─────────────────────────────────────────┐")
print(f"│  🎯 Cuentas a scrapear : {len(pending):>4}            │")
print(f"│  💰 Estimado mínimo    : ${est_min:>8.4f} USD    │")
print(f"│  💰 Estimado máximo    : ${est_max:>8.4f} USD    │")
print(f"│  ⚠️  Estimación aproximada — puede variar  │")
print(f"└─────────────────────────────────────────┘\n")

confirm = input(f"¿Confirmar scraping de {len(pending)} cuentas? (s/n): ").strip().lower()
if confirm != "s":
    print("❌ Operación cancelada.")
    exit(0)

# ── 5. Scraping por cuenta ────────────────────────────────────────────────────
total_cost   = 0.0
total_posts  = 0
cost_log_entries = []

for username in pending:
    filepath = f"data_raw/posts_{username}.json"
    print(f"\n🚀 Scraping @{username}...")

    run_input = {
        "username":     [username],
        "resultsLimit": RESULTS_LIMIT,
    }

    try:
        run = client.actor(ACTOR_ID).call(run_input=run_input)

        dataset_items = client.dataset(run.default_dataset_id).list_items().items
        run_cost      = run.usage_total_usd or 0.0
        total_cost   += run_cost

        print(f"  💰 Costo: ${run_cost:.4f} USD")

        if not dataset_items:
            print(f"  ⚠️  Sin posts para @{username} — cuenta privada o sin contenido")
            cost_log_entries.append({"username": username, "posts": 0, "cost": run_cost})
            continue

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(dataset_items, f, ensure_ascii=False, indent=4)

        total_posts += len(dataset_items)
        cost_log_entries.append({"username": username, "posts": len(dataset_items), "cost": run_cost})
        print(f"  ✅ {len(dataset_items)} posts guardados en '{filepath}'")

    except Exception as e:
        print(f"  ❌ Error scrapeando @{username}: {e}")
        continue

# ── 6. Resumen FinOps ──────────────────────────────────────────────────────────
print(f"\n{'─'*50}")
print(f"  {'Username':<35} {'Posts':>5}  {'Costo':>8}")
print(f"  {'─'*48}")
for entry in cost_log_entries:
    print(f"  @{entry['username']:<34} {entry['posts']:>5}  ${entry['cost']:>7.4f}")
print(f"  {'─'*48}")
print(f"  {'TOTAL':<35} {total_posts:>5}  ${total_cost:>7.4f}")
print(f"{'─'*50}")

log_run_cost(len(pending), total_cost)
print(f"\n💾 Costo registrado en {COST_LOG_PATH}")
print(f"✅ Pipeline completo.")
