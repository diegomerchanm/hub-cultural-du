"""
1_harvest_ig_posts_hikerapi.py — Harvest de posts recientes vía HikerAPI
(alternativa pay-per-use a Apify para la Fase 1, sin mensualidad fija),
mismo espíritu que 1_harvest_ig_network_hikerapi.py (DD-049) pero para
posts en vez de listas "following".

Endpoint usado: GET /v1/user/medias/chunk?user_id=...&end_cursor=...
(doc oficial: https://hiker-doc.readthedocs.io/en/latest/api-reference/v1/user/).
Devuelve, según la doc, [lista_de_media, end_cursor_o_null] — MISMO patrón
"lista de dos" que /v1/user/following/chunk, que en DD-049 resultó no
coincidir con la doc pública. Este endpoint de posts NO se calibró contra
una llamada real todavía (el sandbox donde se escribió este script no
tiene salida de red a api.hikerapi.com — ProxyError 403 al intentarlo, el
mismo tipo de bloqueo que ya afecta a Neo4j desde acá). Por eso el parseo
de la respuesta es defensivo (acepta dict U list-of-two, igual que
fetch_following) — pero DIEGO DEBE CORRER `calibrate` PRIMERO, contra UNA
sola cuenta, antes de confiar en `harvest`/`compare` a mayor escala.

DIFERENCIAS DE CAMPOS CONOCIDAS vs. el actor de Apify (apify/instagram-post-scraper),
según la doc de HikerAPI (no verificado en vivo):
  - displayUrl:  no existe un campo con ese nombre exacto. Se aproxima con
    image_versions[0].url (fotos/carruseles) o thumbnail_url (videos/reels)
    — mismo propósito (imagen de portada), pero no garantizado ser
    exactamente la misma resolución/crop que devuelve Apify.
  - hashtags / mentions: el endpoint no los devuelve pre-parseados como
    listas de entidades (Apify sí). Se reconstruyen con regex sobre
    caption_text — aproximación razonable pero no idéntica al parser de
    Instagram (p.ej. límites de unicode en nombres de usuario).
  - musicInfo / latestComments: no vienen en este endpoint (harían falta
    llamadas extra pagas por post — /v1/media/comments/chunk, y no hay
    endpoint de música identificado). Se dejan vacíos a propósito: grep
    contra 4_enrich_events_extract.py confirma que la extracción de
    eventos NO lee p.musicInfo ni p.latestComments — impacto real: bajo.
    Si en el futuro algo del pipeline empieza a necesitarlos, este script
    necesita revisarse.
  - productType: mapeado 1:1 desde product_type, pero el vocabulario de
    valores no se comparó contra el de Apify (p.ej. "clips" vs "Reel").

Tres subcomandos:

  calibrate  Una sola llamada real (resolver usuario + 1 página de medias)
             contra UNA cuenta, para confirmar la forma real de la
             respuesta antes de gastar en algo más grande. Imprime el JSON
             crudo de HikerAPI sin normalizar.

  compare    Para una cuenta que YA tiene data_raw/posts_<username>.json
             de Apify: descarga la misma cantidad de posts vía HikerAPI,
             los guarda en data_raw/posts_hikerapi_<username>.json (NUNCA
             pisa el archivo de Apify), y imprime un reporte de
             comparación (solapamiento de ids, cobertura de campos, costo
             real vs. lo que costó la corrida de Apify según
             .apify_cost_log.json).

  harvest    Modo "producción": descarga posts para una lista de seeds
             (mismo formato --seeds que 1_harvest_ig_posts.py) y escribe
             data_raw/posts_<username>.json normalizado al MISMO shape que
             ya escribe el scraper de Apify — para que 2_build_graph.py lo
             ingiera sin ningún cambio. Salta cuentas que ya tengan
             archivo (incremental), igual que el resto del pipeline.

Requiere:
    pip install requests python-dotenv typer
    HIKERAPI_ACCESS_KEY en .env (ya debería estar si corriste
    1_harvest_ig_network_hikerapi.py antes, DD-049).

Uso:
    python 1_harvest_ig_posts_hikerapi.py calibrate --username francy_barahona_calisabor
    python 1_harvest_ig_posts_hikerapi.py compare --username francy_barahona_calisabor
    python 1_harvest_ig_posts_hikerapi.py harvest --seeds config/seeds_idf.json --max-days 10
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

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
PRICE_PER_REQUEST = 0.0006  # asumido igual al de /v1/user/following/chunk (DD-049) —
                            # /v1/user/medias/chunk no está en la tabla de "multi-request
                            # endpoints" de la doc, así que 1 req/página es razonable,
                            # pero no se confirmó con una llamada real todavía.

COST_LOG_PATH = ".hikerapi_cost_log.json"
DATA_RAW_DIR = "data_raw"
RESULTS_LIMIT = 50  # mismo tope que 1_harvest_ig_posts.py (Apify)

app = typer.Typer(add_completion=False)

# ── 2. Cliente HikerAPI ─────────────────────────────────────────────────────

def call(path: str, params: dict) -> tuple[dict | list, int]:
    """Devuelve (json, requests_usados_segun_header)."""
    resp = requests.get(f"{BASE_URL}{path}", params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    reqs = 1
    info = resp.headers.get("x-hiker-info")
    if info:
        try:
            reqs = json.loads(info).get("reqs", 1)
        except (json.JSONDecodeError, TypeError):
            pass
    return resp.json(), reqs


def resolve_user_id(username: str) -> tuple[str, int]:
    data, reqs = call("/v1/user/by/username", {"username": username})
    return data["pk"], reqs


def _parse_chunk_response(data) -> tuple[list, str | None]:
    """Mismo parseo defensivo que fetch_following en el script de red
    (DD-049) — la doc dice [items, end_cursor], pero ya vimos una vez que
    la doc pública no coincide con la respuesta real. Acepta también la
    forma {"items"/"medias": [...], "end_cursor"/"next_max_id": ...}."""
    if isinstance(data, list) and len(data) == 2 and isinstance(data[0], list):
        return data[0], data[1]
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        items = data.get("items") or data.get("medias") or data.get("response", {}).get("items") or []
        cursor = data.get("end_cursor") or data.get("next_max_id")
        return items, cursor
    return [], None


def _media_taken_at(media: dict) -> datetime | None:
    ts = media.get("taken_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_medias_raw(user_id: str, limit: int, cutoff: datetime | None = None) -> tuple[list, int]:
    """Devuelve (lista_de_media_cruda_de_hikerapi, requests_usados).

    Si se pasa `cutoff`, corta la paginación apenas UNA página completa
    queda más vieja que el corte — no sigue pidiendo páginas solo para
    descartarlas después (así --max-days en 'harvest' realmente baja el
    costo, no solo el resultado final). Asume que el feed viene ordenado
    de más nuevo a más viejo (lo normal en Instagram y en el ejemplo de
    la doc de HikerAPI) — no confirmado con una llamada real todavía;
    'calibrate' es el lugar para verificarlo antes de confiar en esto a
    escala."""
    all_items, cursor, requests_used = [], None, 0
    while len(all_items) < limit:
        params = {"user_id": user_id}
        if cursor:
            params["end_cursor"] = cursor
        data, reqs = call("/v1/user/medias/chunk", params)
        requests_used += reqs

        batch, cursor = _parse_chunk_response(data)
        if not batch:
            break

        if cutoff is not None:
            in_window = []
            hit_cutoff = False
            for item in batch:
                taken_at = _media_taken_at(item) if isinstance(item, dict) else None
                if taken_at is not None and taken_at < cutoff:
                    hit_cutoff = True
                    continue
                in_window.append(item)
            all_items.extend(in_window)
            if hit_cutoff:
                break
        else:
            all_items.extend(batch)

        if not cursor:
            break
        time.sleep(0.2)
    return all_items[:limit], requests_used


# ── 3. Normalización → mismo shape que produce Apify (ver docstring) ───────

HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
MENTION_RE = re.compile(r"@([\w.]+)", re.UNICODE)


def _best_display_url(media: dict) -> str:
    versions = media.get("image_versions") or []
    if versions and isinstance(versions, list) and versions[0].get("url"):
        return versions[0]["url"]
    return media.get("thumbnail_url", "") or ""


# HikerAPI media_type es un entero (1=foto, 2=video, 8=carrusel/álbum);
# Apify usa strings ("Image"/"Video"/"Sidecar"). Nada del pipeline activo
# lee p.type para filtrar (grep confirmado contra 2_build_graph.py y
# 4_enrich_events_extract.py), así que esto es solo por legibilidad/
# consistencia del dato guardado, no una dependencia funcional.
_MEDIA_TYPE_MAP = {1: "Image", 2: "Video", 8: "Sidecar"}


def _map_type(media_type) -> str:
    return _MEDIA_TYPE_MAP.get(media_type, str(media_type) if media_type is not None else "")


def normalize_media(media: dict) -> dict:
    caption = media.get("caption_text") or ""
    user = media.get("user") or {}
    coauthors = media.get("coauthor_producers") or []
    usertags_raw = media.get("usertags")
    tagged = []
    if isinstance(usertags_raw, dict):
        tagged = usertags_raw.get("in", []) or []
    elif isinstance(usertags_raw, list):
        tagged = usertags_raw
    location = media.get("location") or {}

    return {
        "id": media.get("pk", ""),
        "type": _map_type(media.get("media_type")),
        "shortCode": media.get("code", ""),
        "url": f"https://www.instagram.com/p/{media.get('code', '')}/" if media.get("code") else "",
        "caption": caption,
        "timestamp": media.get("taken_at", ""),
        "likesCount": media.get("like_count", 0) or 0,
        "commentsCount": media.get("comment_count", 0) or 0,
        "videoViewCount": media.get("view_count", 0) or 0,
        "videoPlayCount": media.get("play_count", 0) or 0,
        "videoDuration": media.get("video_duration", 0.0) or 0.0,
        "displayUrl": _best_display_url(media),
        "productType": media.get("product_type", ""),
        "isCommentsDisabled": media.get("comments_disabled", False),
        "hashtags": HASHTAG_RE.findall(caption),
        "mentions": MENTION_RE.findall(caption),
        "taggedUsers": tagged,
        "coauthorProducers": coauthors,
        "locationName": location.get("name", "") if isinstance(location, dict) else "",
        "locationId": location.get("pk", "") if isinstance(location, dict) else "",
        "musicInfo": {},       # gap conocido — ver docstring
        "latestComments": [],  # gap conocido — ver docstring
        "ownerFullName": user.get("full_name", ""),
        "_source": "hikerapi",
    }


# ── 4. FinOps ────────────────────────────────────────────────────────────

def log_run_cost(n_requests: int, n_posts: int, kind: str):
    log = {"runs": []}
    if os.path.exists(COST_LOG_PATH):
        with open(COST_LOG_PATH, "r") as f:
            log = json.load(f)
    log["runs"].append({
        "type": kind,  # "posts" — distingue de las corridas de "following" (DD-049)
        "requests": n_requests,
        "posts": n_posts,
        "cost": round(n_requests * PRICE_PER_REQUEST, 6),
    })
    log["runs"] = log["runs"][-20:]
    with open(COST_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def _apify_cost_for(username: str) -> float | None:
    """Busca en .apify_cost_log.json si hay un costo registrado para esta
    cuenta específica. El log de Apify no guarda username por entrada
    (solo agregados por corrida) — esto es best-effort, puede no
    encontrar nada."""
    if not os.path.exists(".apify_cost_log.json"):
        return None
    try:
        with open(".apify_cost_log.json", encoding="utf-8") as f:
            log = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    costs = [r.get("cost_per_profile") for r in log.get("runs", []) if r.get("cost_per_profile")]
    return sum(costs) / len(costs) if costs else None


# ── 5. calibrate ─────────────────────────────────────────────────────────

@app.command()
def calibrate(username: str = typer.Option(..., "--username")):
    """Una sola cuenta, 1 página, JSON crudo sin normalizar — para
    confirmar la forma real de la respuesta antes de gastar en algo más
    grande. Costo esperado: 1-2 requests (~$0.001-0.0012 USD)."""
    print(f"🔎 Calibrando contra @{username}...")
    user_id, reqs1 = resolve_user_id(username)
    print(f"  ✅ user_id={user_id} ({reqs1} req)")

    data, reqs2 = call("/v1/user/medias/chunk", {"user_id": user_id})
    print(f"  ✅ 1 página de medias ({reqs2} req)")
    print(f"\n💰 Total: {reqs1 + reqs2} requests, ~${(reqs1 + reqs2) * PRICE_PER_REQUEST:.4f} USD\n")
    print("── JSON crudo (primeros 2000 caracteres) ──")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
    print("\n⚠️  Revisá a mano: ¿la forma coincide con [items, cursor] como dice la doc,")
    print("   o es un dict {\"items\":...}? Si es distinto, avisá antes de correr 'harvest'.")


# ── 6. compare ───────────────────────────────────────────────────────────

@app.command()
def compare(username: str = typer.Option(..., "--username")):
    apify_path = f"{DATA_RAW_DIR}/posts_{username}.json"
    if not os.path.exists(apify_path):
        print(f"❌ No existe '{apify_path}' — elegí una cuenta que ya tenga posts de Apify para comparar manzanas con manzanas.")
        raise typer.Exit(1)

    with open(apify_path, encoding="utf-8") as f:
        apify_posts = json.load(f)
    apify_ids = {str(p.get("id")) for p in apify_posts if isinstance(p, dict)}
    target_n = len(apify_posts)

    print(f"📋 Apify: {target_n} posts en '{apify_path}'")
    print(f"🚀 Pidiendo {target_n} posts de @{username} vía HikerAPI...")

    user_id, reqs1 = resolve_user_id(username)
    raw_items, reqs2 = fetch_medias_raw(user_id, target_n)
    total_reqs = reqs1 + reqs2
    cost = total_reqs * PRICE_PER_REQUEST

    hiker_posts = [normalize_media(m) for m in raw_items]
    hiker_path = f"{DATA_RAW_DIR}/posts_hikerapi_{username}.json"
    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    with open(hiker_path, "w", encoding="utf-8") as f:
        json.dump(hiker_posts, f, ensure_ascii=False, indent=2)

    hiker_ids = {str(p["id"]) for p in hiker_posts}
    overlap = apify_ids & hiker_ids

    fields_to_check = ["caption", "timestamp", "displayUrl", "hashtags", "likesCount", "commentsCount"]
    coverage = {}
    for field in fields_to_check:
        n_present = sum(1 for p in hiker_posts if p.get(field) not in (None, "", [], 0))
        coverage[field] = f"{n_present}/{len(hiker_posts)}"

    apify_cost_avg = _apify_cost_for(username)

    def _date_range(posts, key):
        dates = sorted(p.get(key, "")[:10] for p in posts if p.get(key))
        return (dates[0], dates[-1]) if dates else ("?", "?")

    apify_range = _date_range(apify_posts, "timestamp")
    hiker_range = _date_range(hiker_posts, "timestamp")

    print(f"\n{'─'*56}")
    print(f"  REPORTE DE COMPARACIÓN — @{username}")
    print(f"{'─'*56}")
    print(f"  Posts Apify (referencia)     : {target_n}  ({apify_range[0]} a {apify_range[1]})")
    print(f"  Posts HikerAPI (recuperados) : {len(hiker_posts)}  ({hiker_range[0]} a {hiker_range[1]})")
    print(f"  IDs en común                 : {len(overlap)} / {target_n}")
    print(f"  {'─'*54}")
    print(f"  Cobertura de campos (HikerAPI, no-vacíos/total):")
    for field, ratio in coverage.items():
        print(f"    · {field:<15}: {ratio}")
    print(f"  {'─'*54}")
    print(f"  Costo HikerAPI esta corrida  : ${cost:.4f} USD ({total_reqs} requests)")
    if apify_cost_avg is not None:
        print(f"  Costo Apify promedio/cuenta  : ${apify_cost_avg:.4f} USD (histórico, .apify_cost_log.json)")
    else:
        print(f"  Costo Apify promedio/cuenta  : sin dato en .apify_cost_log.json")
    print(f"  {'─'*54}")
    print(f"  Guardado (sin pisar el de Apify): '{hiker_path}'")
    print(f"{'─'*56}\n")

    if len(overlap) < target_n * 0.7:
        print("⚠️  Menos del 70% de los ids coinciden — puede ser normal (Apify y HikerAPI")
        print("   scrapearon en momentos distintos, la cuenta puede haber publicado/borrado")
        print("   posts entre medias) pero vale la pena revisar a mano antes de confiar en esto.")

    log_run_cost(total_reqs, len(hiker_posts), kind="compare")


# ── 7. harvest (modo producción) ────────────────────────────────────────

def usernames_from_seeds(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    seeds = data.get("seeds", [])
    usernames = [(s.get("handle") or "").strip() for s in seeds if (s.get("handle") or "").strip()]
    print(f"📋 {len(usernames)} usernames desde '{path}'")
    return usernames


def _within_window(iso_ts: str, cutoff: datetime) -> bool:
    if not iso_ts:
        return False
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


@app.command()
def harvest(
    seeds: str = typer.Option(..., "--seeds", help="Ruta a un archivo seeds (config/seeds_*.json)."),
    max_days: int = typer.Option(10, "--max-days", help="Solo posts de los últimos N días (filtro client-side, HikerAPI no expone onlyPostsNewerThan)."),
    force: bool = typer.Option(False, "--force", help="Re-descargar aunque ya exista posts_<username>.json (por defecto se saltea)."),
    yes: bool = typer.Option(False, "--yes", help="Saltar la confirmación de costo."),
):
    """Escribe data_raw/posts_<username>.json normalizado — mismo archivo
    que 1_harvest_ig_posts.py, mismo shape, listo para 2_build_graph.py sin
    cambios. NO corras esto contra un lote grande sin haber corrido antes
    'calibrate' y 'compare' contra 1-2 cuentas — la forma de la respuesta
    de /v1/user/medias/chunk no está confirmada en vivo todavía."""
    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    targets = usernames_from_seeds(seeds)
    if not force:
        pending = [u for u in targets if not os.path.exists(f"{DATA_RAW_DIR}/posts_{u}.json")]
        skipped = len(targets) - len(pending)
        if skipped:
            print(f"⏭️  {skipped} ya tienen posts_<username>.json — se saltan (usá --force para re-descargar)")
    else:
        pending = targets

    if not pending:
        print("✅ Nada para procesar.")
        return

    est_pages_per_account = max(1, RESULTS_LIMIT // 12)  # asume ~12 items/página, sin confirmar
    est_requests = len(pending) * (est_pages_per_account + 1)
    est_cost = est_requests * PRICE_PER_REQUEST

    print(f"\n┌─────────────────────────────────────────────────┐")
    print(f"│  🎯 Cuentas a procesar    : {len(pending):>4}                    │")
    print(f"│  📦 Tope por cuenta       : {RESULTS_LIMIT:>4} posts               │")
    print(f"│  📅 Ventana               : últimos {max_days:>3} días          │")
    print(f"│  💰 Estimado (techo, SIN calibrar): ${est_cost:>7.2f} USD      │")
    print(f"│  ⚠️  Costo real probablemente MENOR — la paginación   │")
    print(f"│     corta apenas ve una página fuera de la ventana │")
    print(f"│     de {max_days} días, no baja los {RESULTS_LIMIT} posts completos siempre │")
    print(f"│  ⚠️  Corré 'calibrate' antes si no lo hiciste         │")
    print(f"└─────────────────────────────────────────────────┘\n")

    if not yes:
        confirm = input(f"¿Confirmar descarga de {len(pending)} cuentas? (y/n): ").strip().lower()
        if confirm not in ("y", "s"):
            print("❌ Operación cancelada.")
            return

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    total_requests, total_posts = 0, 0

    for username in pending:
        print(f"🚀 @{username}...")
        try:
            user_id, reqs1 = resolve_user_id(username)
            # cutoff acá corta la paginación apenas se detecta una página vieja
            # (ver fetch_medias_raw) — el filtro de abajo es una red de
            # seguridad extra, no el mecanismo principal de ahorro.
            raw_items, reqs2 = fetch_medias_raw(user_id, RESULTS_LIMIT, cutoff=cutoff)
            total_requests += reqs1 + reqs2

            normalized = [normalize_media(m) for m in raw_items]
            in_window = [p for p in normalized if _within_window(p["timestamp"], cutoff)]
            if len(in_window) < len(normalized):
                print(f"  ℹ️  {len(normalized) - len(in_window)} posts fuera de la ventana de {max_days}d — descartados")

            if not in_window:
                print(f"  ⚠️  Sin posts en ventana para @{username}")
                continue

            filepath = f"{DATA_RAW_DIR}/posts_{username}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(in_window, f, ensure_ascii=False, indent=4)
            total_posts += len(in_window)
            print(f"  ✅ {len(in_window)} posts → '{filepath}'")

        except requests.exceptions.HTTPError as e:
            print(f"  ❌ Error HTTP con @{username}: {e}")
            continue
        except Exception as e:
            print(f"  ❌ Error con @{username}: {e}")
            continue

        time.sleep(0.2)

    total_cost = total_requests * PRICE_PER_REQUEST
    print(f"\n💰 FINOPS — {total_requests} requests, ${total_cost:.4f} USD, {total_posts} posts")
    log_run_cost(total_requests, total_posts, kind="posts")
    print(f"✅ Harvest completo.")


if __name__ == "__main__":
    app()
