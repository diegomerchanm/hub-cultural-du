"""
manual_scrape_ingest.py — Clasifica y guarda de una sola pasada las tandas
que llegan del scraper manual de consola (el snippet que acumula
usernames/texto desde el DOM de un modal de "seguidores"/"siguiendo" de
Instagram).

Modo interactivo (recomendado): corré el script sin argumentos, te
pregunta la cuenta semilla, después pegás el CSV crudo tal cual lo copiaste
del navegador, terminás con una línea que diga FIN, y hace toda la gestión
documental sola (separa username/texto, detecta verificación, marca texto
no confiable, guarda el archivo y actualiza el índice).

    python manual_scrape_ingest.py

También soporta modo por argumentos para scripting:

    python manual_scrape_ingest.py add <seed_username> <archivo.csv|->
    python manual_scrape_ingest.py merge
    python manual_scrape_ingest.py status

Qué hace la clasificación:
  - Separa el badge "Vérifié" (cuenta verificada de Instagram) del texto.
  - Marca como NO CONFIABLE (prefijo "UNRELIABLE_TEXT:") cualquier fila
    donde el texto visible del link no contiene el username — señal de que
    el script de consola agarró un elemento de navegación de Instagram
    ("Profil", "Populaire", "Publications") en vez del nombre real. No se
    descartan, solo se marcan, para que decidas vos qué hacer con ellas.
  - Deduplica por username dentro de la misma tanda.

Dónde queda todo:
  - data_raw/manual_following_<seed>.csv        — una fila por cuenta descubierta
  - data_raw/manual_following_index.json         — registro de todas las tandas
  - data_processed/manual_candidate_accounts.csv — generado por 'merge':
    todas las tandas juntas, con seen_by_count (cuántas semillas distintas
    tienen esa cuenta en su following — señal de relevancia).
"""

import csv
import io
import json
import os
import sys
from pathlib import Path

DATA_RAW_DIR = "data_raw"
DATA_PROCESSED_DIR = "data_processed"
FILE_PREFIX = "manual_following_"
INDEX_PATH = f"{DATA_RAW_DIR}/manual_following_index.json"
MERGED_OUT_PATH = f"{DATA_PROCESSED_DIR}/manual_candidate_accounts.csv"

VERIFIED_SUFFIX = "Vérifié"
STOP_WORD = "FIN"


def load_index() -> dict:
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"batches": []}


def save_index(index: dict) -> None:
    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def classify_row(username: str, texto: str) -> tuple[str, bool, bool]:
    """Devuelve (texto_limpio, is_verified, is_reliable)."""
    is_verified = texto.endswith(VERIFIED_SUFFIX) and texto != VERIFIED_SUFFIX
    cleaned = texto[: -len(VERIFIED_SUFFIX)] if is_verified else texto
    is_reliable = username.lower() in cleaned.lower() or cleaned.strip() == ""
    return cleaned, is_verified, is_reliable


def parse_rows(csv_text_or_handle):
    """Acepta un string con el CSV crudo o un file handle. Devuelve
    (username, texto) por fila."""
    if isinstance(csv_text_or_handle, str):
        handle = io.StringIO(csv_text_or_handle)
    else:
        handle = csv_text_or_handle
    reader = csv.DictReader(handle)
    fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
    if "username" not in fieldnames:
        raise ValueError(
            f"El CSV no tiene columna 'username' (columnas encontradas: {reader.fieldnames})"
        )
    for row in reader:
        row = {k.strip().lower(): v for k, v in row.items()}
        username = (row.get("username") or "").strip().lstrip("@")
        texto = (row.get("texto") or row.get("display_text") or "").strip()
        if not username:
            continue
        yield username, texto


def process_batch(seed: str, rows_iterable) -> bool:
    """Clasifica y guarda una tanda. Devuelve True si escribió algo."""
    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    out_path = f"{DATA_RAW_DIR}/{FILE_PREFIX}{seed}.csv"

    rows = []
    seen_usernames = set()
    n_verified = 0
    n_unreliable = 0

    for username, texto in rows_iterable:
        if username in seen_usernames:
            continue
        seen_usernames.add(username)
        cleaned, is_verified, is_reliable = classify_row(username, texto)
        if is_verified:
            n_verified += 1
        if not is_reliable:
            n_unreliable += 1
        rows.append(
            {
                "username": username,
                "texto": cleaned if is_reliable else f"UNRELIABLE_TEXT:{cleaned}",
                "is_verified": str(is_verified).lower(),
            }
        )

    if not rows:
        print("[AVISO] No se encontraron filas validas -- no se escribio nada.")
        return False

    rows.sort(key=lambda r: r["username"])
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["username", "texto", "is_verified"])
        writer.writeheader()
        writer.writerows(rows)

    index = load_index()
    index["batches"] = [b for b in index["batches"] if b.get("source_seed") != seed]
    index["batches"].append(
        {
            "source_seed": seed,
            "file": out_path,
            "method": "browser_console_dom_scrape",
            "accounts_captured": len(rows),
            "verified_count": n_verified,
            "unreliable_text_count": n_unreliable,
        }
    )
    save_index(index)

    print(f"[OK] @{seed}: {len(rows)} cuentas unicas -> '{out_path}'")
    print(f"     {n_verified} verificadas, {n_unreliable} con texto no confiable (marcadas, no descartadas)")
    print(f"     Indice actualizado -> '{INDEX_PATH}' ({len(index['batches'])} tandas registradas en total)")
    return True


def cmd_add(seed: str, input_path: str) -> None:
    if input_path == "-":
        rows = parse_rows(sys.stdin)
    else:
        with open(input_path, "r", encoding="utf-8", newline="") as f:
            rows = list(parse_rows(f))
    process_batch(seed, rows)


def cmd_merge() -> None:
    candidates: dict[str, dict] = {}

    for path in sorted(Path(DATA_RAW_DIR).glob(f"{FILE_PREFIX}*.csv")):
        seed = path.stem.replace(FILE_PREFIX, "")
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                username = (row.get("username") or "").strip()
                if not username:
                    continue
                if username not in candidates:
                    candidates[username] = {
                        "username": username,
                        "is_verified": row.get("is_verified", ""),
                        "seen_by_count": 0,
                        "seen_by_seeds": set(),
                    }
                candidates[username]["seen_by_count"] += 1
                candidates[username]["seen_by_seeds"].add(seed)
                if row.get("is_verified", "").lower() == "true":
                    candidates[username]["is_verified"] = "true"

    if not candidates:
        print("[AVISO] No hay archivos data_raw/manual_following_*.csv todavia -- nada para mergear.")
        return

    os.makedirs(DATA_PROCESSED_DIR, exist_ok=True)
    rows = sorted(candidates.values(), key=lambda r: (-r["seen_by_count"], r["username"]))

    with open(MERGED_OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["username", "is_verified", "seen_by_count", "seen_by_seeds", "review_status"])
        for r in rows:
            writer.writerow(
                [r["username"], r["is_verified"], r["seen_by_count"],
                 "|".join(sorted(r["seen_by_seeds"])), "pending"]
            )

    print(f"[OK] {len(rows)} cuentas unicas -> '{MERGED_OUT_PATH}'")
    print(f"     {sum(1 for r in rows if r['seen_by_count'] > 1)} vistas por mas de una semilla (senal mas fuerte)")


def cmd_status() -> None:
    index = load_index()
    if not index["batches"]:
        print("Sin tandas registradas todavia.")
        return
    total = 0
    for b in index["batches"]:
        total += b["accounts_captured"]
        print(
            f"  @{b['source_seed']:<28} {b['accounts_captured']:>5} cuentas "
            f"({b.get('verified_count', 0)} verificadas, "
            f"{b.get('unreliable_text_count', 0)} texto no confiable) -> {b['file']}"
        )
    print(f"\n  {len(index['batches'])} tandas, {total} filas capturadas en total (con posible solapamiento entre semillas)")


def cmd_interactive() -> None:
    print("=== Ingesta interactiva de scraping manual ===\n")
    seed = input("Usuario semilla (la cuenta de la que sacaste esta lista de 'following'): ").strip().lstrip("@")
    if not seed:
        print("[ERROR] Sin usuario semilla, cancelado.")
        return

    print(f"\nPegá el CSV crudo (con header 'username,texto') tal cual lo copiaste.")
    print(f"Cuando termines, escribí {STOP_WORD} en una línea sola y Enter:\n")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == STOP_WORD:
            break
        lines.append(line)

    if not lines:
        print("[ERROR] No se pegó nada, cancelado.")
        return

    try:
        rows = list(parse_rows("\n".join(lines)))
    except ValueError as e:
        print(f"[ERROR] {e}")
        return

    wrote = process_batch(seed, rows)
    if not wrote:
        return

    answer = input("\n¿Correr 'merge' ahora para consolidar todas las tandas? (s/n): ").strip().lower()
    if answer in ("s", "si", "sí", "y", "yes"):
        print()
        cmd_merge()


def main():
    if len(sys.argv) == 1:
        cmd_interactive()
        return

    cmd = sys.argv[1]
    if cmd == "add":
        if len(sys.argv) != 4:
            print("Uso: python manual_scrape_ingest.py add <seed_username> <archivo.csv|->")
            sys.exit(1)
        cmd_add(sys.argv[2], sys.argv[3])
    elif cmd == "merge":
        cmd_merge()
    elif cmd == "status":
        cmd_status()
    elif cmd == "interactive":
        cmd_interactive()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
