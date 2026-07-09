"""
1_harvest_ig_profiles.py — Harvest de perfiles Instagram vía Apify.

Tres formas de elegir los usernames a scrapear (mutuamente excluyentes):

  --seeds config/seeds_v2.json
      Primera pasada (DD-022). Trae el perfil completo de cada seed del
      Bloque A (consulados/embajadas) + Bloque B (instituciones culturales).
      2_build_graph.py ya genera nodos vacíos a partir de relatedProfiles/
      menciones al ingestar estos perfiles — ese comportamiento no cambia aquí.

  --from-classifier data_processed/account_scores.csv
      Segunda pasada (DD-025). Trae el perfil completo de las cuentas que
      1_harvest_account_classifier.py marcó keep=True tras la primera
      expansión, excluyendo explícitamente role=seed_source (las seeds del
      Bloque A ya son keep=False por DD-026 — esta condición es una
      salvaguarda adicional, no la única barrera).

  (sin flags)
      Fallback al TARGET_USERNAME hardcodeado original (@consuladocolparis)
      para no romper la reproducibilidad de runs anteriores documentados en
      docs/runs_log.md.

En los tres modos el scraping es incremental: se salta cualquier username
que ya tenga data_raw/profile_<username>.json en disco.
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

ACTOR_ID = "apify/instagram-profile-scraper"
COST_LOG_PATH = ".apify_cost_log.json"
FALLBACK_TARGET_USERNAME = "consuladocolparis"

app = typer.Typer(add_completion=False)


# ── 2. Fuentes de usernames ────────────────────────────────────────────────

def usernames_from_seeds(path: str) -> list[str]:
    """Lee el bloque A + B de config/seeds_v2.json (DD-022). Salta handles vacíos/null."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    seeds = data.get("seeds", [])
    usernames, skipped = [], 0
    for s in seeds:
        handle = (s.get("handle") or "").strip()
        if not handle:
            skipped += 1
            continue
        usernames.append(handle)

    print(f"📋 {len(seeds)} seeds en '{path}' — {len(usernames)} con handle, "
          f"{skipped} saltados (sin handle confirmado)")
    return usernames


def usernames_from_classifier(path: str) -> list[str]:
    """
    Lee data_processed/account_scores.csv (1_harvest_account_classifier.py).
    Filtra keep=True y role != 'seed_source' (DD-025 segunda pasada;
    DD-026 salvaguarda explícita contra re-scrapear seeds del Bloque A).
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    usernames = [r["username"] for r in rows
                 if r.get("keep") == "True" and r.get("role") != "seed_source"]

    print(f"📋 {len(rows)} filas en '{path}' — {len(usernames)} cumplen "
          f"keep=True y role!=seed_source")
    return usernames


# ── 3. FinOps — calibración de costo (misma lógica que extract_profiles.py) ─

def get_calibrated_cost() -> float:
    if os.path.exists(COST_LOG_PATH):
        with open(COST_LOG_PATH, "r") as f:
            log = json.load(f)
        costs = [r["cost_per_profile"] for r in log.get("runs", []) if r.get("cost_per_profile", 0) > 0]
        if costs:
            avg = sum(costs) / len(costs)
            print(f"  📊 Costo calibrado desde {len(costs)} run(s) histórico(s): ${avg:.4f} USD/perfil")
            return avg
    print(f"  📊 Sin historial — usando precio de catálogo Apify: $0.0005 USD/perfil")
    return 0.0005


def log_run_cost(n_profiles: int, total_cost: float):
    log = {"runs": []}
    if os.path.exists(COST_LOG_PATH):
        with open(COST_LOG_PATH, "r") as f:
            log = json.load(f)
    if n_profiles > 0:
        log["runs"].append({
            "profiles": n_profiles,
            "total_cost": round(total_cost, 6),
            "cost_per_profile": round(total_cost / n_profiles, 6),
        })
        log["runs"] = log["runs"][-10:]
    with open(COST_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


# ── 4. Scraper de perfil puro ──────────────────────────────────────────────

def scrape_profiles(usernames: list[str], client: ApifyClient) -> float:
    os.makedirs("data_raw", exist_ok=True)
    total_cost = 0.0

    for username in usernames:
        filepath = f"data_raw/profile_{username}.json"
        if os.path.exists(filepath):
            print(f"⏭️  Skipping @{username} — archivo ya existe")
            continue

        print(f"🚀 Scraping profile: @{username}...")
        run_input = {"usernames": [username], "resultsType": "details"}

        try:
            run = client.actor(ACTOR_ID).call(run_input=run_input)
            dataset_items = client.dataset(run.default_dataset_id).list_items().items

            run_cost = run.usage_total_usd or 0.0
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

    return total_cost


# ── 5. CLI ──────────────────────────────────────────────────────────────────

@app.command()
def main(
    seeds: str = typer.Option(None, "--seeds",
                               help="Ruta a config/seeds_v2.json — primera pasada (DD-022)."),
    from_classifier: str = typer.Option(None, "--from-classifier",
                                        help="Ruta a data_processed/account_scores.csv — segunda pasada (DD-025)."),
    yes: bool = typer.Option(False, "--yes",
                             help="Saltar la confirmación interactiva."),
):
    if seeds and from_classifier:
        raise typer.BadParameter("Usa solo uno de --seeds / --from-classifier, no ambos.")

    if seeds:
        target_usernames = usernames_from_seeds(seeds)
    elif from_classifier:
        target_usernames = usernames_from_classifier(from_classifier)
    else:
        print(f"⚠️  Sin --seeds ni --from-classifier — usando TARGET_USERNAME "
              f"hardcodeado (@{FALLBACK_TARGET_USERNAME}) para preservar la "
              f"reproducibilidad de runs anteriores (docs/runs_log.md).")
        target_usernames = [FALLBACK_TARGET_USERNAME]

    if not target_usernames:
        print("✅ No hay usernames para scrapear.")
        return

    os.makedirs("data_raw", exist_ok=True)
    pending = [u for u in target_usernames if not os.path.exists(f"data_raw/profile_{u}.json")]
    skipped_local = len(target_usernames) - len(pending)

    print(f"\n📋 {len(target_usernames)} usernames objetivo")
    if skipped_local:
        print(f"⏭️  {skipped_local} ya tienen archivo local — se saltarán")
    print(f"🎯 {len(pending)} perfiles a scrapear\n")

    if not pending:
        print("✅ Todos los perfiles ya están descargados.")
        return

    # 6. Estimación de costo — antes de cualquier llamada real a Apify
    print("💸 Estimando costo...")
    cost_per_profile = get_calibrated_cost()
    estimated_min = cost_per_profile * len(pending)
    estimated_max = estimated_min * 2.5  # margen por variabilidad de compute

    print(f"\n┌─────────────────────────────────────────┐")
    print(f"│  🎯 Perfiles a scrapear : {len(pending):>4}            │")
    print(f"│  💰 Estimado mínimo     : ${estimated_min:>8.4f} USD    │")
    print(f"│  💰 Estimado máximo     : ${estimated_max:>8.4f} USD    │")
    print(f"│  ⚠️  Estimación aproximada — puede variar  │")
    print(f"└─────────────────────────────────────────┘\n")

    if not yes:
        confirm = input(f"¿Confirmar scraping de {len(pending)} perfiles? (y/n): ").strip().lower()
        if confirm not in ("y", "s"):
            print("❌ Operación cancelada.")
            return

    # 7. Ejecutar
    client = ApifyClient(APIFY_TOKEN)
    total_cost = scrape_profiles(pending, client)

    print(f"\n💰 FINOPS — Costo total de esta sesión: ${total_cost:.4f} USD")
    log_run_cost(len(pending), total_cost)
    print(f"\n✅ Pipeline de extracción de perfiles completo.")


if __name__ == "__main__":
    app()
