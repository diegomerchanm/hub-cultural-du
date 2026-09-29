"""
1_harvest_ig_profiles_hikerapi.py — Perfiles de Instagram vía HikerAPI
(reemplaza a 1_harvest_ig_profiles.py y extract_profiles.py, que usaban
Apify; ambos archivados en old/ — DD-076).

Endpoint: GET /v1/user/by/username (1 request por cuenta).

Escribe data_raw/profile_<username>.json con el MISMO shape que escribía el
actor apify/instagram-profile-scraper, para que 2_build_graph.py lo ingiera
sin cambios. Campos que 2_build_graph.py lee y cómo se mapean:

    Apify (lo que lee 2_build_graph)   HikerAPI /v1/user/by/username
    ─────────────────────────────────  ──────────────────────────────
    username, id                       username, pk
    fullName, biography                full_name, biography
    followersCount, followsCount       follower_count, following_count
    postsCount                         media_count
    private, verified                  is_private, is_verified
    isBusinessAccount                  is_business
    businessCategoryName               business_category_name | category_name | category
    profilePicUrl                      profile_pic_url_hd | profile_pic_url
    businessAddress.{city_name, ...}   city_name, latitude, longitude, address_street, zip
    highlightReelCount                 (no existe → 0)
    relatedProfiles                    (no existe en HikerAPI → se conservan los
                                        del archivo anterior si había, si no [])
    latestPosts / latestIgtvVideos     (no se piden: los posts salen de
                                        1_harvest_ig_posts_hikerapi.py)

Pérdida conocida: HikerAPI no expone "perfiles relacionados" (el endpoint
gql/user/related/profiles figura como deprecado en su doc). Eso afectaba el
descubrimiento de cuentas vía RELATED_TO; el motor de descubrimiento de la
Fase 2 del roadmap lo reemplaza con otras fuentes (OSM, búsqueda, hashtags).

Uso:
    python 1_harvest_ig_profiles_hikerapi.py calibrate --username lecarreaudutemple
    python 1_harvest_ig_profiles_hikerapi.py harvest --seeds config/seeds_idf.json --dry-run
    python 1_harvest_ig_profiles_hikerapi.py harvest --seeds config/seeds_idf.json --max-usd 0.50
    python 1_harvest_ig_profiles_hikerapi.py harvest --pending-from-neo4j        # ex extract_profiles.py
    python 1_harvest_ig_profiles_hikerapi.py harvest --seeds ... --force         # refrescar existentes
"""

from __future__ import annotations

import json
import os
import time

import requests
import typer

from hikerapi_client import (PRICE_PER_REQUEST, BudgetExceeded, HikerClient,
                             load_excluded, usernames_from_seeds)

DATA_RAW_DIR = "data_raw"
app = typer.Typer(add_completion=False)


# ── Normalización al shape de Apify ─────────────────────────────────────────

def normalize_user(user: dict, previous: dict | None = None) -> dict:
    previous = previous or {}
    address = {}
    if user.get("city_name"):
        address = {
            "city_name": user.get("city_name", ""),
            "latitude": user.get("latitude"),
            "longitude": user.get("longitude"),
            "street_address": user.get("address_street", "") or "",
            "zip_code": user.get("zip", "") or "",
        }
    return {
        "username": user.get("username", ""),
        "id": str(user.get("pk", "")),
        "fullName": user.get("full_name", "") or "",
        "biography": user.get("biography", "") or "",
        "externalUrl": user.get("external_url", "") or "",
        "followersCount": user.get("follower_count", 0) or 0,
        "followsCount": user.get("following_count", 0) or 0,
        "postsCount": user.get("media_count", 0) or 0,
        "highlightReelCount": 0,
        "private": bool(user.get("is_private", False)),
        "verified": bool(user.get("is_verified", False)),
        "isBusinessAccount": bool(user.get("is_business", False)),
        "businessCategoryName": (user.get("business_category_name")
                                 or user.get("category_name")
                                 or user.get("category") or ""),
        "profilePicUrl": user.get("profile_pic_url_hd") or user.get("profile_pic_url", "") or "",
        "publicEmail": user.get("public_email", "") or "",
        "businessAddress": address,
        # HikerAPI no tiene perfiles relacionados: se conservan los viejos (Apify).
        "relatedProfiles": previous.get("relatedProfiles", []),
        "latestPosts": [],
        "latestIgtvVideos": [],
        "_source": "hikerapi",
    }


def _read_previous(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data[0] if isinstance(data, list) and data else data
    except (json.JSONDecodeError, OSError):
        return None


def _pending_from_neo4j() -> list[str]:
    """Cuentas en el grafo sin followersCount (nunca scrapeadas como perfil).
    Misma consulta que el viejo extract_profiles.py, con el paréntesis
    corregido (antes el AND se aplicaba solo a la 2ª condición)."""
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(os.getenv("NEO4J_URI"),
                                  auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")))
    with driver.session() as session:
        rows = session.run("""
            MATCH (a:Account)
            WHERE (a.followersCount IS NULL OR a.followersCount = 0)
              AND a.username IS NOT NULL
            RETURN a.username AS username ORDER BY username
        """)
        names = [r["username"] for r in rows]
    driver.close()
    excluded = load_excluded()
    names = [u for u in names if u.lower() not in excluded]
    print(f"📋 {len(names)} cuentas pendientes en Neo4j (tras exclusiones)")
    return names


# ── Comandos ───────────────────────────────────────────────────────────────

@app.command()
def calibrate(username: str = typer.Option(..., "--username")):
    """1 request. Imprime el JSON crudo para confirmar la forma real."""
    client = HikerClient(max_requests=2)
    data = client.get("/v1/user/by/username", {"username": username})
    print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
    user = data.get("user", data) if isinstance(data, dict) else {}
    print("\n── Normalizado (lo que leería 2_build_graph.py) ──")
    print(json.dumps(normalize_user(user), indent=2, ensure_ascii=False)[:1500])
    print(f"\n💰 {client.requests_used} request(s), ~${client.cost_usd:.4f} USD")
    client.log_run("profiles_calibrate", accounts=1)


@app.command()
def harvest(
    seeds: str = typer.Option(None, "--seeds", help="config/seeds_*.json"),
    pending_from_neo4j: bool = typer.Option(False, "--pending-from-neo4j",
        help="Cuentas del grafo sin perfil scrapeado (reemplaza extract_profiles.py)."),
    force: bool = typer.Option(False, "--force", help="Re-descargar aunque exista el archivo."),
    max_usd: float = typer.Option(1.0, "--max-usd", help="Tope de gasto de la corrida."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo estima; no llama a la API."),
    yes: bool = typer.Option(False, "--yes", help="Sin confirmación por teclado."),
):
    if bool(seeds) == pending_from_neo4j:
        print("❌ Indicá exactamente una fuente: --seeds <archivo> o --pending-from-neo4j")
        raise typer.Exit(1)

    targets = _pending_from_neo4j() if pending_from_neo4j else usernames_from_seeds(seeds)
    os.makedirs(DATA_RAW_DIR, exist_ok=True)
    pending = targets if force else [
        u for u in targets if not os.path.exists(f"{DATA_RAW_DIR}/profile_{u}.json")]
    if len(pending) < len(targets):
        print(f"⏭️  {len(targets) - len(pending)} ya tienen profile_<username>.json (usá --force para refrescar)")
    if not pending:
        print("✅ Nada para procesar.")
        return

    est = len(pending) * PRICE_PER_REQUEST
    print(f"\n🎯 {len(pending)} perfiles · 1 request c/u · estimado ~${est:.4f} USD "
          f"(precio supuesto ${PRICE_PER_REQUEST}/req) · tope ${max_usd:.2f}")
    if dry_run:
        print("🧪 --dry-run: no se llamó a la API.")
        for u in pending[:20]:
            print(f"   · @{u}")
        if len(pending) > 20:
            print(f"   … y {len(pending) - 20} más")
        return
    if not yes and input("¿Confirmar? (y/n): ").strip().lower() not in ("y", "s"):
        print("❌ Cancelado.")
        return

    client = HikerClient(max_requests=HikerClient.max_requests_from_usd(max_usd))
    ok, failed, private = 0, [], 0
    try:
        for username in pending:
            path = f"{DATA_RAW_DIR}/profile_{username}.json"
            try:
                user = client.user_by_username(username)
                if not user.get("username"):
                    raise ValueError("respuesta sin username")
                profile = normalize_user(user, previous=_read_previous(path))
                with open(path, "w", encoding="utf-8") as f:
                    json.dump([profile], f, ensure_ascii=False, indent=2)  # lista, como Apify
                ok += 1
                private += profile["private"]
                print(f"  ✅ @{username} ({profile['followersCount']:,} seguidores"
                      f"{', privada' if profile['private'] else ''})")
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
        client.log_run("profiles", accounts_ok=ok, accounts_failed=len(failed))
        print(f"\n💰 {client.requests_used} requests, ~${client.cost_usd:.4f} USD · "
              f"{ok} OK ({private} privadas) · {len(failed)} errores")
        if failed:
            print("   Fallidas: " + ", ".join(failed[:30]))


if __name__ == "__main__":
    app()
