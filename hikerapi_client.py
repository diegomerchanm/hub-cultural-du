"""
hikerapi_client.py — Cliente compartido de HikerAPI (DD-076).

Centraliza lo que antes estaba duplicado en cada script *_hikerapi.py:
  - autenticación (HIKERAPI_ACCESS_KEY, leída de variables de entorno / .env,
    nunca en duro);
  - llamadas GET con reintentos (429 y 5xx) y conteo de requests reales
    (header x-hiker-info, cuando HikerAPI lo manda);
  - guardarraíl de gasto: cada corrida fija un tope de requests (o de USD) y
    el cliente se detiene ANTES de pasarlo, con BudgetExceeded;
  - caché username -> user_id (pk) en data_raw/hikerapi_user_ids.json, para no
    pagar /v1/user/by/username en cada corrida de posts;
  - log de costo por corrida en .hikerapi_cost_log.json (gitignored).

El precio por request es un SUPUESTO (0.0006 USD, observado en DD-049 para
/v1/user/following/chunk). Se puede ajustar sin tocar código con la variable
de entorno HIKERAPI_PRICE_PER_REQUEST una vez confirmado el precio real en el
dashboard de HikerAPI.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.hikerapi.com"
PRICE_PER_REQUEST = float(os.getenv("HIKERAPI_PRICE_PER_REQUEST", "0.0006"))
COST_LOG_PATH = ".hikerapi_cost_log.json"
USER_ID_CACHE_PATH = os.path.join("data_raw", "hikerapi_user_ids.json")
MAX_RETRIES = 3


class BudgetExceeded(RuntimeError):
    """Se levanta antes de hacer una llamada que superaría el tope fijado."""


class HikerClient:
    def __init__(self, max_requests: int | None = None, dry_run: bool = False):
        self.access_key = os.getenv("HIKERAPI_ACCESS_KEY")
        if not self.access_key and not dry_run:
            raise ValueError(
                "Falta HIKERAPI_ACCESS_KEY (variable de entorno o .env). "
                "Generala en el dashboard de hikerapi.com."
            )
        self.max_requests = max_requests
        self.dry_run = dry_run
        self.requests_used = 0
        self._user_ids = self._load_user_id_cache()

    # ── Presupuesto ──────────────────────────────────────────────────────
    @staticmethod
    def max_requests_from_usd(max_usd: float | None) -> int | None:
        if max_usd is None:
            return None
        return max(1, int(max_usd / PRICE_PER_REQUEST))

    @property
    def cost_usd(self) -> float:
        return round(self.requests_used * PRICE_PER_REQUEST, 6)

    def _check_budget(self):
        if self.max_requests is not None and self.requests_used >= self.max_requests:
            raise BudgetExceeded(
                f"Tope alcanzado: {self.requests_used} requests "
                f"(~${self.cost_usd:.4f} USD). Subí --max-usd si querés seguir."
            )

    # ── HTTP ─────────────────────────────────────────────────────────────
    def get(self, path: str, params: dict):
        """GET con reintentos. Devuelve el JSON. Cuenta requests reales."""
        if self.dry_run:
            raise RuntimeError("HikerClient en modo dry-run: no debería llamar a la API.")
        self._check_budget()
        headers = {"x-access-key": self.access_key, "accept": "application/json"}
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.get(f"{BASE_URL}{path}", params=params,
                                    headers=headers, timeout=30)
            except requests.exceptions.RequestException as e:
                last_exc = e
                time.sleep(2 * attempt)
                continue

            # Cada respuesta HTTP cuenta como gasto potencial, incluso errores.
            self.requests_used += self._reqs_from_header(resp)

            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = requests.exceptions.HTTPError(
                    f"{resp.status_code} en {path}", response=resp)
                time.sleep(3 * attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _reqs_from_header(resp) -> int:
        info = resp.headers.get("x-hiker-info")
        if info:
            try:
                return int(json.loads(info).get("reqs", 1))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return 1

    # ── Usuarios ─────────────────────────────────────────────────────────
    def user_by_username(self, username: str) -> dict:
        data = self.get("/v1/user/by/username", {"username": username})
        # Algunas respuestas vienen envueltas ({"user": {...}}); se aceptan ambas.
        user = data.get("user", data) if isinstance(data, dict) else {}
        if user.get("pk"):
            self.remember_user_id(username, str(user["pk"]))
        return user

    def user_id(self, username: str) -> str:
        """pk desde la caché; solo llama a la API si no está."""
        cached = self._user_ids.get(username.lower())
        if cached:
            return cached
        user = self.user_by_username(username)
        if not user.get("pk"):
            raise ValueError(f"HikerAPI no devolvió pk para @{username}")
        return str(user["pk"])

    def remember_user_id(self, username: str, pk: str):
        self._user_ids[username.lower()] = pk

    def _load_user_id_cache(self) -> dict:
        if os.path.exists(USER_ID_CACHE_PATH):
            try:
                with open(USER_ID_CACHE_PATH, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save_user_id_cache(self):
        os.makedirs(os.path.dirname(USER_ID_CACHE_PATH), exist_ok=True)
        with open(USER_ID_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(self._user_ids, f, indent=1, sort_keys=True)

    # ── Log de costo ─────────────────────────────────────────────────────
    def log_run(self, kind: str, **extra):
        log = {"runs": []}
        if os.path.exists(COST_LOG_PATH):
            try:
                with open(COST_LOG_PATH, encoding="utf-8") as f:
                    log = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        log.setdefault("runs", []).append({
            "type": kind,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requests": self.requests_used,
            "cost": self.cost_usd,
            "price_per_request_assumed": PRICE_PER_REQUEST,
            **extra,
        })
        log["runs"] = log["runs"][-50:]
        with open(COST_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2)


# ── Utilidades compartidas ───────────────────────────────────────────────

def usernames_from_seeds(path: str, excluded: set[str] | None = None) -> list[str]:
    """Formato {"seeds": [{"handle": "..."}]} de config/seeds_*.json.
    Deduplica y descarta las cuentas de config/excluded_accounts.json."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    excluded = excluded if excluded is not None else load_excluded()
    seen, out = set(), []
    for s in data.get("seeds", []):
        u = (s.get("handle") or "").strip().lstrip("@")
        if u and u.lower() not in seen and u.lower() not in excluded:
            seen.add(u.lower())
            out.append(u)
    print(f"📋 {len(out)} usernames desde '{path}' (tras exclusiones y duplicados)")
    return out


def load_excluded(path: str = "config/excluded_accounts.json") -> set[str]:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    items = (data.get("accounts") or data.get("excluded") or []) if isinstance(data, dict) else data
    out = set()
    for it in items if isinstance(items, list) else []:
        name = it.get("username") if isinstance(it, dict) else it
        if isinstance(name, str) and name.strip():
            out.add(name.strip().lstrip("@").lower())
    return out


def to_iso_utc(value) -> str:
    """Normaliza un timestamp de HikerAPI (ISO string o epoch en segundos)
    a 'YYYY-MM-DDTHH:MM:SS.000Z', el mismo formato que usaba Apify. Importa
    porque 4_enrich_events_extract.py y 5_export comparan los primeros 10
    caracteres como fecha."""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            dt = datetime.fromtimestamp(int(value), tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except (ValueError, OSError, OverflowError):
        return ""
