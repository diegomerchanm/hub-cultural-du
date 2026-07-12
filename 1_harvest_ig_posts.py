"""
1_harvest_ig_posts.py — Harvest de posts recientes de Instagram vía Apify.

Lista de cuentas generalizada desde data_processed/account_scores.csv
(keep=True), con exclusión manual de falsos positivos del clasificador
(DD-028). Filtra por recencia (onlyPostsNewerThan) en vez de solo un tope
de cantidad fijo: DD-028 documenta por qué V2 prioriza actividad cultural
vigente sobre densidad histórica del grafo (contraste con DD-020 en V1).
"""

import csv
import json
import os

import typer
from apify_client import ApifyClient
from dotenv import load_dotenv

# ── 1. Credenciales ────────────────────────────────────────────────────────
load_dotenv()
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

if not APIFY_TOKEN:
    raise ValueError("Error: APIFY_TOKEN not found in .env file")

ACCOUNT_SCORES_CSV = "data_processed/account_scores.csv"
EXCLUDED_USERNAMES = {"williamsanchezinmobiliaria"}  # falso positivo del clasificador — exclusión manual (DD-028)

RESULTS_LIMIT = 50  # techo de seguridad, no el objetivo — ver DD-028
ACTOR_ID      = "apify/instagram-post-scraper"
COST_LOG_PATH = ".apify_cost_log.json"

app = typer.Typer(add_completion=False)


# ── 2. Fuente de usernames ─────────────────────────────────────────────────

def load_target_usernames() -> list[str]:
    """keep=True en account_scores.csv, menos exclusiones manuales (DD-028)."""
    with open(ACCOUNT_SCORES_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    usernames = [r["username"] for r in rows
                 if r.get("keep") == "True" and r["username"] not in EXCLUDED_USERNAMES]
    excluded_present = sum(1 for r in rows if r["username"] in EXCLUDED_USERNAMES)

    print(f"📋 {len(rows)} filas en '{ACCOUNT_SCORES_CSV}' — {len(usernames)} con keep=True "
          f"({excluded_present} excluidas manualmente)")
    return usernames


# ── 3. FinOps — calibración de costo ───────────────────────────────────────

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


# ── 4. Diagnóstico de "sin posts" ──────────────────────────────────────────

def diagnose_empty(username: str, days: int) -> tuple[str, str]:
    """
    Distingue por qué el actor devolvió 0 posts: cuenta privada/sin
    contenido histórico vs. simplemente sin actividad en la ventana de
    recencia. Usa data_raw/profile_<username>.json (si existe) como
    referencia — no hace una llamada adicional a Apify.

    Retorna (categoria, mensaje) con categoria en
    {"window", "no_content", "unknown"}.
    """
    profile_path = f"data_raw/profile_{username}.json"
    if not os.path.exists(profile_path):
        return "unknown", "sin posts — causa desconocida (sin perfil local para comparar)"

    try:
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
        if isinstance(profile, list):
            profile = profile[0] if profile else {}
    except (json.JSONDecodeError, OSError):
        return "unknown", "sin posts — causa desconocida (perfil local ilegible)"

    if profile.get("private"):
        return "no_content", "cuenta privada"

    posts_count = profile.get("postsCount", 0)
    if not posts_count:
        return "no_content", "sin contenido (0 posts históricos)"

    return "window", (f"sin posts en los últimos {days} días "
                      f"(tiene {posts_count} históricos — ventana corta)")


# ── 5. Scraping ─────────────────────────────────────────────────────────────

def scrape_posts(usernames: list[str], days: int, client: ApifyClient) -> dict:
    total_cost, total_posts = 0.0, 0
    cost_log_entries = []
    window_empty = 0   # tiene historial pero nada en la ventana de recencia
    no_content   = 0   # privada o sin contenido histórico
    unknown      = 0   # sin perfil local para diagnosticar la causa

    for username in usernames:
        filepath = f"data_raw/posts_{username}.json"
        print(f"\n🚀 Scraping @{username}...")

        run_input = {
            "username":           [username],
            "resultsLimit":       RESULTS_LIMIT,
            "onlyPostsNewerThan": f"{days} days",
            "skipPinnedPosts":    True,
        }

        try:
            run = client.actor(ACTOR_ID).call(run_input=run_input)

            dataset_items = client.dataset(run.default_dataset_id).list_items().items
            run_cost      = run.usage_total_usd or 0.0
            total_cost   += run_cost

            print(f"  💰 Costo: ${run_cost:.4f} USD")

            if not dataset_items:
                category, reason = diagnose_empty(username, days)
                if category == "window":
                    window_empty += 1
                elif category == "no_content":
                    no_content += 1
                else:
                    unknown += 1
                print(f"  ⚠️  Sin posts para @{username} — {reason}")
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

    return {
        "total_cost": total_cost,
        "total_posts": total_posts,
        "cost_log_entries": cost_log_entries,
        "window_empty": window_empty,
        "no_content": no_content,
        "unknown": unknown,
    }


# ── 6. CLI ──────────────────────────────────────────────────────────────────

@app.command()
def main(
    days: int = typer.Option(10, "--days",
                             help="Ventana de recencia en días (onlyPostsNewerThan)."),
):
    os.makedirs("data_raw", exist_ok=True)

    target_usernames = load_target_usernames()
    if not target_usernames:
        print("✅ No hay cuentas con keep=True para scrapear.")
        return

    pending = [u for u in target_usernames
               if not os.path.exists(f"data_raw/posts_{u}.json")]
    skipped = len(target_usernames) - len(pending)

    print(f"\n📋 {len(target_usernames)} cuentas objetivo")
    if skipped:
        print(f"⏭️  {skipped} ya tienen archivo local — se saltarán")
    print(f"🎯 {len(pending)} cuentas a scrapear (últimos {days} días, "
          f"tope de seguridad {RESULTS_LIMIT} posts/cuenta)\n")

    if not pending:
        print("✅ Todos los posts ya están descargados.")
        return

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
        return

    result = scrape_posts(pending, days, ApifyClient(APIFY_TOKEN))

    print(f"\n{'─'*50}")
    print(f"  {'Username':<35} {'Posts':>5}  {'Costo':>8}")
    print(f"  {'─'*48}")
    for entry in result["cost_log_entries"]:
        print(f"  @{entry['username']:<34} {entry['posts']:>5}  ${entry['cost']:>7.4f}")
    print(f"  {'─'*48}")
    print(f"  {'TOTAL':<35} {result['total_posts']:>5}  ${result['total_cost']:>7.4f}")
    print(f"{'─'*50}")

    if result["window_empty"] or result["no_content"] or result["unknown"]:
        print(f"\n🔍 Diagnóstico de cuentas sin posts:")
        print(f"   · Sin actividad en ventana de {days} días (con historial): {result['window_empty']}")
        print(f"   · Privadas o sin contenido histórico: {result['no_content']}")
        if result["unknown"]:
            print(f"   · Causa desconocida (sin perfil local): {result['unknown']}")

    log_run_cost(len(pending), result["total_cost"])
    print(f"\n💾 Costo registrado en {COST_LOG_PATH}")
    print(f"✅ Pipeline completo.")


if __name__ == "__main__":
    app()
