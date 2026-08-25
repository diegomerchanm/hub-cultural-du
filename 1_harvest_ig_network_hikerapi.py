"""
1_harvest_ig_network_hikerapi.py — Harvest de listas "following" completas
vía HikerAPI (alternativa pay-per-use a Apify, sin mensualidad fija).

Confirmado en calibración real (2026-08-17, @sorbonne_lettres_culture):
  - Auth: header x-access-key
  - Resolver username → user_id: GET /v1/user/by/username?username=...
  - Following: GET /v1/user/following/chunk?user_id=...&max_id=...
  - La respuesta NO es {"users": [...], "next_max_id": ...} como decía la
    doc pública — es una lista [lista_usuarios, next_max_id_o_null].
  - Cada página trae 25 cuentas.
  - Costo real: $0.0006 USD/request, ~20 cuentas por request → proyección
    de ~$1.13 USD para las 126 semillas x 300 cuentas (vs. ~$29+ con Apify).
  - Cada cuenta devuelta trae: pk, id, username, full_name, profile_pic_url,
    is_private, is_verified, account_badges. NO trae biografía ni categoría
    de negocio — eso requiere un enriquecimiento aparte por cuenta
    candidata, más adelante, sobre la lista ya deduplicada.

Dos subcomandos:

  harvest   Pide la lista "following" de cada username semilla y la guarda
            en data_raw/hikerapi_following_<username>.json (incremental —
            salta los que ya tengan archivo).

  merge     Lee todos los data_raw/hikerapi_following_*.json descargados y
            arma data_processed/candidate_accounts.csv — un candidato por
            fila, con seen_by_count (cuántas semillas distintas lo siguen).

Requiere:
    pip install requests python-dotenv openpyxl
    Registrarte en https://hikerapi.com, generar API key, y ponerla en
    .env como HIKERAPI_ACCESS_KEY=tu_key_aca

Uso:
    python 1_harvest_ig_network_hikerapi.py harvest --usernames-file cuentas_instagram_completo_v4.xlsx --results-limit 300
    python 1_harvest_ig_network_hikerapi.py merge
"""

import csv
import json
import os
import time
from pathlib import Path

import requests
import typer
from dotenv import load_dotenv

# ── 1. Credenciales ────────────────────────────────────────────────────────
load_dotenv()
ACCESS_KEY = os.getenv("HIKERAPI_ACCESS_KEY")
if not ACCESS_KEY:
    raise ValueError(
        "Falta HIKERAPI_ACCESS_KEY en .env — registrate en hikerapi.com, "
        "generá una API key en tu dashboard, y agregala al .env."
    )

BASE_URL = "https://api.hikerapi.com"
HEADERS = {"x-access-key": ACCESS_KEY}
PRICE_PER_REQUEST = 0.0006  # confirmado en la calibración real

COST_LOG_PATH = ".hikerapi_cost_log.json"
DATA_RAW_DIR = "data_raw"
DATA_PROCESSED_DIR = "data_processed"
FILE_PREFIX = "hikerapi_following_"

app = typer.Typer(add_completion=False)

# ── 2. Fuentes de usernames ────────────────────────────────────────────────

def usernames_from_seeds_json(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    seeds = data.get("seeds", [])
    usernames = [s["handle"].strip() for s in seeds if (s.get("handle") or "").strip()]
    print(f"📋 {len(usernames)} usernames desde '{path}'")
    return usernames

def usernames_from_csv(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        col = "username" if "username" in (reader.fieldnames or []) else reader.fieldnames[0]
        usernames = [row[col].strip() for row in reader if row.get(col, "").strip()]
    print(f"📋 {len(usernames)} usernames desde '{path}' (columna '{col}')")
    return usernames

def usernames_from_txt(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        usernames = [line.strip().lstrip("@") for line in f if line.strip()]
    print(f"📋 {len(usernames)} usernames desde '{path}'")
    return usernames

def usernames_from_xlsx(path: str, column: str = None) -> list[str]:
    """Compatible con cuentas_instagram_completo_v4.xlsx — patrón de 2 filas
    por cuenta (fila real + fila de solo-nombre-visible, ej. 'Maritza Salsa'
    debajo de 'academiamaritzaarizala'). Las filas de solo-nombre se saltan:
    no son usernames válidos."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else "" for h in rows[0]]
    col_idx = header.index(column) if column else 0

    usernames, skipped = [], 0
    for row in rows[1:]:
        val = row[col_idx]
        if not val:
            continue
        has_other_data = any(c not in (None, "") for i, c in enumerate(row) if i != col_idx)
        if not has_other_data:
            skipped += 1
            continue
        usernames.append(str(val).strip().lstrip("@"))

    print(f"📋 {len(usernames)} usernames desde '{path}' (columna '{header[col_idx]}')")
    if skipped:
        print(f"⏭️  {skipped} filas de 'solo nombre visible' saltadas (no son usernames reales)")
    return usernames

def load_usernames(path: str, column: str = None) -> list[str]:
    if path.endswith(".json"):
        return usernames_from_seeds_json(path)
    if path.endswith(".csv"):
        return usernames_from_csv(path)
    if path.endswith(".xlsx"):
        return usernames_from_xlsx(path, column)
    return usernames_from_txt(path)

# ── 3. Cliente HikerAPI ─────────────────────────────────────────────────────

def call(path: str, params: dict) -> dict:
    resp = requests.get(f"{BASE_URL}{path}", params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()

def resolve_user_id(username: str) -> str:
    data = call("/v1/user/by/username", {"username": username})
    return data["pk"]

def fetch_following(user_id: str, limit: int) -> tuple[list, int]:
    """Devuelve (lista_de_cuentas, requests_usados)."""
    all_items, max_id, requests_used = [], None, 0
    while len(all_items) < limit:
        params = {"user_id": user_id}
        if max_id:
            params["max_id"] = max_id
        data = call("/v1/user/following/chunk", params)
        requests_used += 1

        if isinstance(data, dict):
            batch = data.get("users") or data.get("items") or []
            max_id = data.get("next_max_id")
        elif isinstance(data, list) and len(data) == 2 and isinstance(data[0], list):
            batch, max_id = data[0], data[1]
        elif isinstance(data, list):
            batch, max_id = data, None
        else:
            print(f"    ⚠️  Forma de respuesta inesperada: {type(data)} — frenando esta cuenta.")
            break

        if not batch:
            break
        all_items.extend(batch)
        if not max_id:
            break
    return all_items[:limit], requests_used

# ── 4. FinOps ────────────────────────────────────────────────────────────

def log_run_cost(n_requests: int, n_accounts_target: int):
    log = {"runs": []}
    if os.path.exists(COST_LOG_PATH):
        with open(COST_LOG_PATH, "r") as f:
            log = json.load(f)
    log["runs"].append({
        "requests": n_requests,
        "accounts_target": n_accounts_target,
        "cost": round(n_requests * PRICE_PER_REQUEST, 6),
        "accounts_per_request": round(n_accounts_target / n_requests, 2) if n_requests else 0,
    })
    log["runs"] = log["runs"][-20:]
    with open(COST_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)

# ── 5. Harvest ──────────────────────────────────────────────────────────────

def harvest_network(usernames: list[str], results_limit: int) -> tuple[float, int, int]:
    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    total_requests, total_accounts = 0, 0

    for username in usernames:
        filepath = f"{DATA_RAW_DIR}/{FILE_PREFIX}{username}.json"
        if os.path.exists(filepath):
            print(f"⏭️  Skipping @{username} — archivo ya existe")
            continue

        print(f"🚀 Descargando following de @{username} (límite {results_limit})...")
        try:
            user_id = resolve_user_id(username)
            total_requests += 1
            items, req_used = fetch_following(user_id, results_limit)
            total_requests += req_used
            total_accounts += len(items)

            if not items:
                print(f"  ⚠️  Sin datos para @{username} — cuenta privada, inexistente, o sin following")
                continue

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({"source_username": username, "user_id": user_id, "items": items},
                          f, ensure_ascii=False, indent=2)
            print(f"  ✅ {len(items)} cuentas ({req_used + 1} requests) → '{filepath}'")

        except requests.exceptions.HTTPError as e:
            print(f"  ❌ Error HTTP con @{username}: {e}")
            continue
        except Exception as e:
            print(f"  ❌ Error con @{username}: {e}")
            continue

        time.sleep(0.2)

    return total_requests * PRICE_PER_REQUEST, total_requests, total_accounts

# ── 6. Merge → candidate_accounts.csv ───────────────────────────────────────

def merge_candidates() -> str:
    candidates: dict[str, dict] = {}

    for path in Path(DATA_RAW_DIR).glob(f"{FILE_PREFIX}*.json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        source = data.get("source_username", path.stem.replace(FILE_PREFIX, ""))
        for item in data.get("items", []):
            username = item.get("username")
            if not username:
                continue
            if username not in candidates:
                candidates[username] = {
                    "username": username,
                    "full_name": item.get("full_name", ""),
                    "is_verified": item.get("is_verified", ""),
                    "is_private": item.get("is_private", ""),
                    "seen_by_count": 0,
                    "seen_by_seeds": set(),
                }
            candidates[username]["seen_by_count"] += 1
            candidates[username]["seen_by_seeds"].add(source)

    os.makedirs(DATA_PROCESSED_DIR, exist_ok=True)
    out_path = f"{DATA_PROCESSED_DIR}/candidate_accounts.csv"
    rows = sorted(candidates.values(), key=lambda r: (-r["seen_by_count"], r["username"]))

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["username", "full_name", "is_verified", "is_private",
                          "seen_by_count", "seen_by_seeds", "review_status"])
        for r in rows:
            writer.writerow([r["username"], r["full_name"], r["is_verified"], r["is_private"],
                              r["seen_by_count"], "|".join(sorted(r["seen_by_seeds"])), "pending"])

    print(f"✅ {len(rows)} cuentas únicas → '{out_path}'")
    print(f"   {sum(1 for r in rows if r['seen_by_count'] > 1)} vistas por más de una semilla (señal más fuerte)")
    return out_path

# ── 7. CLI ────────────────────────────────────────────────────────────────

@app.command()
def harvest(
    usernames_file: str = typer.Option(..., "--usernames-file",
                                        help="cuentas_instagram_completo_v4.xlsx, config/seeds_v2.json, un .csv, o un .txt."),
    column: str = typer.Option(None, "--column", help="Solo para .xlsx: nombre de columna (default: primera)."),
    results_limit: int = typer.Option(300, "--results-limit", help="Tope de cuentas 'following' por semilla."),
    yes: bool = typer.Option(False, "--yes", help="Saltar la confirmación interactiva."),
):
    target_usernames = load_usernames(usernames_file, column)
    if not target_usernames:
        print("✅ No hay usernames para procesar.")
        return

    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    pending = [u for u in target_usernames if not os.path.exists(f"{DATA_RAW_DIR}/{FILE_PREFIX}{u}.json")]
    skipped_local = len(target_usernames) - len(pending)

    print(f"\n📋 {len(target_usernames)} semillas objetivo")
    if skipped_local:
        print(f"⏭️  {skipped_local} ya tienen archivo local — se saltarán")
    print(f"🎯 {len(pending)} semillas a procesar (hasta {results_limit} cuentas cada una)\n")

    if not pending:
        print("✅ Todas las semillas ya están descargadas.")
        return

    est_requests = len(pending) * ((results_limit / 25) + 1)
    est_cost = est_requests * PRICE_PER_REQUEST

    print(f"┌─────────────────────────────────────────────────┐")
    print(f"│  🎯 Semillas a procesar   : {len(pending):>4}                    │")
    print(f"│  📦 Tope de resultados    : {results_limit:>4} por semilla        │")
    print(f"│  💰 Estimado (calibrado)  : ${est_cost:>8.2f} USD              │")
    print(f"│  ⚠️  Aproximado — depende de cuántas cuentas       │")
    print(f"│     siga realmente cada semilla                   │")
    print(f"└─────────────────────────────────────────────────┘\n")

    if not yes:
        confirm = input(f"¿Confirmar descarga de {len(pending)} semillas? (y/n): ").strip().lower()
        if confirm not in ("y", "s"):
            print("❌ Operación cancelada.")
            return

    total_cost, total_requests, total_accounts = harvest_network(pending, results_limit)

    print(f"\n💰 FINOPS — {total_requests} requests, ${total_cost:.4f} USD, {total_accounts} cuentas descubiertas")
    log_run_cost(total_requests, total_accounts)
    print(f"\n✅ Harvest de red completo. Corré 'merge' para consolidar candidatos.")

@app.command()
def merge():
    merge_candidates()

if __name__ == "__main__":
    app()
