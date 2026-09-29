"""
1_harvest_ig_posts_hikerapi.py — Posts recientes vía HikerAPI.
Scraper de posts POR DEFECTO desde DD-076 (reemplaza a 1_harvest_ig_posts.py,
Apify, archivado en old/). Exploratorio en DD-059; adoptado en DD-076.

Endpoint: GET /v1/user/medias/chunk?user_id=...&end_cursor=...

Escribe data_raw/posts_<username>.json con el MISMO shape que el actor
apify/instagram-post-scraper, así 2_build_graph.py lo ingiere sin cambios
(lo marca source="dedicated_scraper", igual que antes).

Comportamiento heredado del script de Apify (DD-029), ahora también aquí:
  - Ventana dinámica por cuenta: solo se piden posts desde el más reciente
    ya guardado (topado en --max-days). Si la brecha es < 1 día, se salta.
  - Fusión en vez de sobrescritura: dedup por id (el nuevo gana), orden por
    fecha, tope deslizante de 50 posts por cuenta.
  - Paginación que corta apenas aparece una página fuera de la ventana.

Mejoras respecto a la versión exploratoria (DD-059):
  - user_id (pk) en caché (data_raw/hikerapi_user_ids.json): ahorra 1
    request por cuenta y por corrida.
  - --dry-run (estima sin llamar) y --max-usd (tope duro de gasto).
  - Timestamps normalizados a ISO UTC 'YYYY-MM-DDTHH:MM:SS.000Z' (HikerAPI
    puede devolver epoch o ISO; el resto del pipeline compara los 10
    primeros caracteres como fecha).

Gaps conocidos vs. Apify (sin impacto en la extracción de eventos, que no
los lee): musicInfo y latestComments vacíos; hashtags/mentions
reconstruidos con regex sobre el caption.

Uso:
    python 1_harvest_ig_posts_hikerapi.py calibrate --username lecarreaudutemple
    python 1_harvest_ig_posts_hikerapi.py compare   --username lecarreaudutemple
    python 1_harvest_ig_posts_hikerapi.py harvest --seeds config/seeds_idf.json --dry-run
    python 1_harvest_ig_posts_hikerapi.py harvest --seeds config/seeds_idf.json --max-days 10 --max-usd 1
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
import typer

from hikerapi_client import (PRICE_PER_REQUEST, BudgetExceeded, HikerClient,
                             to_iso_utc, usernames_from_seeds)

DATA_RAW_DIR = "data_raw"
RESULTS_LIMIT = 50            # tope deslizante por cuenta (DD-028/DD-029)
EST_ITEMS_PER_PAGE = 12       # supuesto para estimar costo; confirmar con calibrate
app = typer.Typer(add_completion=False)


# ── Parseo de respuestas ─────────────────────────────────────────────────

def parse_chunk_response(data) -> tuple[list, str | None]:
    """La doc dice [items, end_cursor]; en DD-049 la respuesta real de otro
    endpoint no coincidía con la doc, así que se aceptan varias formas."""
    if isinstance(data, list) and len(data) == 2 and isinstance(data[0], list):
        return data[0], data[1]
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        items = (data.get("items") or data.get("medias")
                 or (data.get("response") or {}).get("items") or [])
        cursor = data.get("end_cursor") or data.get("next_max_id") or data.get("next_page_id")
        return items, cursor
    return [], None


def _parse_dt(value) -> datetime | None:
    iso = to_iso_utc(value)
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def fetch_medias(client: HikerClient, user_id: str, limit: int,
                 cutoff: datetime | None = None) -> list:
    """Pagina /v1/user/medias/chunk hasta `limit` items o hasta que una
    página toque el corte temporal (asume feed de más nuevo a más viejo;
    los posts fijados pueden ser viejos, por eso se descartan pero no
    cortan la paginación por sí solos si hay otros dentro de la ventana)."""
    items, cursor = [], None
    while len(items) < limit:
        params = {"user_id": user_id}
        if cursor:
            params["end_cursor"] = cursor
        batch, cursor = parse_chunk_response(client.get("/v1/user/medias/chunk", params))
        if not batch:
            break
        if cutoff is None:
            items.extend(batch)
        else:
            in_window = [m for m in batch if isinstance(m, dict)
                         and (_parse_dt(m.get("taken_at")) or cutoff) >= cutoff]
            items.extend(in_window)
            if len(in_window) < len(batch) and not in_window:
                break           # página entera fuera de ventana
            if len(in_window) < len(batch) * 0.5:
                break           # mayoría vieja: el resto también lo será
        if not cursor:
            break
        time.sleep(0.2)
    return items[:limit]


# ── Normalización → shape de Apify ───────────────────────────────────────

HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
MENTION_RE = re.compile(r"@([\w.]+)", re.UNICODE)
_MEDIA_TYPE_MAP = {1: "Image", 2: "Video", 8: "Sidecar"}


def _best_display_url(media: dict) -> str:
    versions = media.get("image_versions") or []
    if isinstance(versions, list) and versions and isinstance(versions[0], dict) and versions[0].get("url"):
        return versions[0]["url"]
    if isinstance(versions, dict):  # forma image_versions2.candidates
        cands = versions.get("candidates") or []
        if cands and cands[0].get("url"):
            return cands[0]["url"]
    return media.get("thumbnail_url", "") or ""


def _users(raw) -> list[dict]:
    """usertags / coauthors a la forma que lee 2_build_graph
    ({username, id, full_name, is_verified, profile_pic_url})."""
    if isinstance(raw, dict):
        raw = raw.get("in", []) or []
    out = []
    for it in raw or []:
        u = it.get("user", it) if isinstance(it, dict) else {}
        if u.get("username"):
            out.append({"username": u.get("username"), "id": str(u.get("pk", "")),
                        "full_name": u.get("full_name", ""),
                        "is_verified": u.get("is_verified", False),
                        "profile_pic_url": u.get("profile_pic_url", "")})
    return out


def normalize_media(media: dict) -> dict:
    caption = media.get("caption_text") or ""
    if not caption and isinstance(media.get("caption"), dict):
        caption = media["caption"].get("text", "") or ""
    user = media.get("user") or {}
    location = media.get("location") or {}
    code = media.get("code", "") or ""
    return {
        "id": str(media.get("pk", "") or media.get("id", "")),
        "type": _MEDIA_TYPE_MAP.get(media.get("media_type"), str(media.get("media_type") or "")),
        "shortCode": code,
        "url": f"https://www.instagram.com/p/{code}/" if code else "",
        "caption": caption,
        "timestamp": to_iso_utc(media.get("taken_at")),
        "likesCount": media.get("like_count", 0) or 0,
        "commentsCount": media.get("comment_count", 0) or 0,
        "videoViewCount": media.get("view_count", 0) or 0,
        "videoPlayCount": media.get("play_count", 0) or 0,
        "videoDuration": media.get("video_duration", 0.0) or 0.0,
        "displayUrl": _best_display_url(media),
        "productType": media.get("product_type", "") or "",
        "isCommentsDisabled": bool(media.get("comments_disabled", False)),
        "hashtags": HASHTAG_RE.findall(caption),
        "mentions": MENTION_RE.findall(caption),
        "taggedUsers": _users(media.get("usertags")),
        "coauthorProducers": _users(media.get("coauthor_producers")),
        "locationName": location.get("name", "") if isinstance(location, dict) else "",
        "locationId": str(location.get("pk", "")) if isinstance(location, dict) and location.get("pk") else "",
        "musicInfo": {},
        "latestComments": [],
        "ownerUsername": user.get("username", ""),
        "ownerFullName": user.get("full_name", ""),
        "_source": "hikerapi",
    }


# ── Ventana dinámica + fusión (portado de 1_harvest_ig_posts.py, DD-029) ──

def load_existing(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def days_to_fetch(existing: list, max_days: int, now: datetime | None = None) -> int | None:
    """None = brecha < 1 día (se salta). Si no hay posts, max_days."""
    now = now or datetime.now(timezone.utc)
    stamps = [_parse_dt(p.get("timestamp")) for p in existing if isinstance(p, dict)]
    stamps = [s for s in stamps if s]
    if not stamps:
        return max_days
    gap = (now - max(stamps)).total_seconds() / 86400
    if gap < 1:
        return None
    return min(math.ceil(gap), max_days)


def merge_and_cap(existing: list, new_items: list, cap: int = RESULTS_LIMIT) -> list:
    by_id = {}
    for post in [*existing, *new_items]:
        if isinstance(post, dict) and post.get("id") not in (None, ""):
            by_id[str(post["id"])] = post
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(by_id.values(), key=lambda p: _parse_dt(p.get("timestamp")) or epoch,
                  reverse=True)[:cap]


# ── Comandos ─────────────────────────────────────────────────────────────

@app.command()
def calibrate(username: str = typer.Option(..., "--username")):
    """1-2 requests: resolver usuario (si no está en caché) + 1 página cruda."""
    client = HikerClient(max_requests=3)
    user_id = client.user_id(username)
    data = client.get("/v1/user/medias/chunk", {"user_id": user_id})
    items, cursor = parse_chunk_response(data)
    print(f"user_id={user_id} · {len(items)} items en la 1ª página · cursor={'sí' if cursor else 'no'}")
    print("── JSON crudo (2500 caracteres) ──")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:2500])
    if items:
        print("\n── 1er item normalizado ──")
        print(json.dumps(normalize_media(items[0]), indent=2, ensure_ascii=False)[:1500])
    print(f"\n💰 {client.requests_used} request(s), ~${client.cost_usd:.4f} USD")
    client.save_user_id_cache()
    client.log_run("posts_calibrate", accounts=1, items_first_page=len(items))


@app.command()
def compare(username: str = typer.Option(..., "--username"),
            max_usd: float = typer.Option(0.05, "--max-usd")):
    """Compara contra un posts_<username>.json de Apify ya existente, sin pisarlo."""
    apify_path = f"{DATA_RAW_DIR}/posts_{username}.json"
    apify_posts = [p for p in load_existing(apify_path) if p.get("_source") != "hikerapi"]
    if not apify_posts:
        print(f"❌ No hay posts de Apify en '{apify_path}' para comparar.")
        raise typer.Exit(1)
    client = HikerClient(max_requests=HikerClient.max_requests_from_usd(max_usd))
    raw = fetch_medias(client, client.user_id(username), len(apify_posts))
    hiker = [normalize_media(m) for m in raw]
    out = f"{DATA_RAW_DIR}/posts_hikerapi_{username}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(hiker, f, ensure_ascii=False, indent=2)

    a_ids = {str(p.get("id")) for p in apify_posts}
    overlap = a_ids & {p["id"] for p in hiker}
    print(f"\nApify: {len(apify_posts)} posts · HikerAPI: {len(hiker)} · ids en común: {len(overlap)}")
    for field in ["caption", "timestamp", "displayUrl", "hashtags", "likesCount",
                  "locationName", "taggedUsers"]:
        n = sum(1 for p in hiker if p.get(field) not in (None, "", [], 0))
        m = sum(1 for p in apify_posts if p.get(field) not in (None, "", [], 0))
        print(f"  {field:<14} HikerAPI {n:>3}/{len(hiker):<3}  Apify {m:>3}/{len(apify_posts)}")
    same_ts = sum(1 for p in hiker for q in apify_posts
                  if p["id"] == str(q.get("id")) and p["timestamp"][:10] == str(q.get("timestamp", ""))[:10])
    print(f"  fecha idéntica en ids comunes: {same_ts}/{len(overlap)}")
    print(f"\n💰 {client.requests_used} requests, ~${client.cost_usd:.4f} USD · guardado en '{out}'")
    client.save_user_id_cache()
    client.log_run("posts_compare", accounts=1, posts=len(hiker), overlap=len(overlap))


@app.command()
def harvest(
    seeds: str = typer.Option(..., "--seeds", help="config/seeds_*.json"),
    max_days: int = typer.Option(10, "--max-days", help="Ventana máxima en días."),
    force: bool = typer.Option(False, "--force", help="Ignorar la ventana dinámica y pedir --max-days completos."),
    max_usd: float = typer.Option(1.0, "--max-usd", help="Tope de gasto de la corrida."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo estima; no llama a la API."),
    yes: bool = typer.Option(False, "--yes", help="Sin confirmación por teclado."),
):
    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    targets = usernames_from_seeds(seeds)

    plan = []  # (username, días)
    for u in targets:
        existing = load_existing(f"{DATA_RAW_DIR}/posts_{u}.json")
        days = max_days if force else days_to_fetch(existing, max_days)
        if days is not None:
            plan.append((u, days))
    skipped = len(targets) - len(plan)
    if skipped:
        print(f"⏭️  {skipped} cuentas con posts de hace < 1 día — se saltan")
    if not plan:
        print("✅ Nada para procesar.")
        return

    cache = HikerClient(dry_run=True)._user_ids
    missing_ids = sum(1 for u, _ in plan if u.lower() not in cache)
    pages = sum(max(1, math.ceil(min(RESULTS_LIMIT, d * 2) / EST_ITEMS_PER_PAGE)) for _, d in plan)
    est_reqs = pages + missing_ids
    print(f"\n🎯 {len(plan)} cuentas · ventana ≤ {max_days} días · "
          f"~{est_reqs} requests ({missing_ids} para resolver user_id) · "
          f"~${est_reqs * PRICE_PER_REQUEST:.4f} USD (estimación, sin calibrar) · tope ${max_usd:.2f}")
    if dry_run:
        print("🧪 --dry-run: no se llamó a la API.")
        return
    if not yes and input("¿Confirmar? (y/n): ").strip().lower() not in ("y", "s"):
        print("❌ Cancelado.")
        return

    client = HikerClient(max_requests=HikerClient.max_requests_from_usd(max_usd))
    now = datetime.now(timezone.utc)
    total_new, done, failed = 0, 0, []
    try:
        for username, days in plan:
            path = f"{DATA_RAW_DIR}/posts_{username}.json"
            try:
                cutoff = now - timedelta(days=days)
                raw = fetch_medias(client, client.user_id(username), RESULTS_LIMIT, cutoff=cutoff)
                new = [p for p in (normalize_media(m) for m in raw)
                       if (_parse_dt(p["timestamp"]) or cutoff) >= cutoff and p["id"]]
                existing = load_existing(path)
                merged = merge_and_cap(existing, new)
                if merged:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(merged, f, ensure_ascii=False, indent=2)
                known = {str(p.get("id")) for p in existing}
                n_new = sum(1 for p in new if p["id"] not in known)
                total_new += n_new
                done += 1
                print(f"  ✅ @{username}: {len(new)} en ventana de {days}d, {n_new} nuevos, {len(merged)} en archivo")
            except BudgetExceeded:
                raise
            except (requests.exceptions.HTTPError, ValueError) as e:
                failed.append(username)
                print(f"  ❌ @{username}: {e}")
            time.sleep(0.2)
    except BudgetExceeded as e:
        print(f"\n🛑 {e}")
    finally:
        client.save_user_id_cache()
        client.log_run("posts", accounts_ok=done, accounts_failed=len(failed), new_posts=total_new)
        print(f"\n💰 {client.requests_used} requests, ~${client.cost_usd:.4f} USD · "
              f"{done} cuentas OK · {total_new} posts nuevos · {len(failed)} errores")


if __name__ == "__main__":
    app()
