"""
5_export_dashboard_data.py — Hub Cultural DU

Exporta desde Neo4j todo lo que el sitio nuevo de descubrimiento de eventos
necesita (ver docs/dashboard_redesign_proposal.md) a un único JSON estático
que el front-end (site/) lee directo, sin backend ni dependencia de Neo4j en
producción — decisión tomada en la propuesta: 664 eventos caben en ~1-2MB,
un sitio estático (GitHub Pages / Cloudflare Pages) alcanza de sobra.

Qué hace:
  1. Trae eventos válidos (no rechazados, invitación pública futura confirmada,
     fecha real) que tengan AL MENOS un texto de ubicación extraído por el LLM
     (exactAddress / locationName / cityName). **Decisión de producto
     2026-08-15 (DD-045), reemplaza la de 2026-08-13:** aparecer en el sitio y
     tener pin en el mapa son dos cosas independientes. Un evento entra al
     sitio si el LLM extrajo texto de ubicación — que es lo que el front
     muestra realmente (`ev.exactAddress || ev.locationName || ev.cityName`,
     ver site/app.js), nunca algo derivado de Nominatim. Que el geocoder haya
     podido o no convertir ese texto en coordenadas confiables solo decide si
     el evento tendrá pin de mapa (feature futura): cuando la geocodificación
     es sospechosa, `lat`/`lon` salen en `null` y el evento igual se exporta.
     Antes se descartaba el evento entero, lo que sacaba del sitio ~100 de 170
     eventos por un problema del geocoder, no del evento.
  2. Trae cuentas curadas/con métricas de grafo (para el perfil de organizador
     y el componente A del ranking).
  3. Calcula, con el dataset completo en memoria, los sub-scores de ranking
     que necesitan contexto de percentil (Q, A, B — ver fórmula en la
     propuesta, sección 3). T (proximidad temporal) y C (contexto de sesión)
     NO se precalculan acá a propósito: dependen de "hoy" y de las
     preferencias del visitante en su navegador, así que se computan en el
     cliente (site/app.js) en el momento de cada visita — precalcularlos acá
     los dejaría corriendo con la fecha de la última exportación, no con la
     fecha real de cada visita.
  4. Calcula similitud coseno sobre los embeddings (mismo patrón que
     4_enrich_events_resolve.py) y guarda los 5 vecinos más cercanos por
     evento — "eventos similares" real, sin costo de LLM. El embedding en sí
     NUNCA se exporta al JSON (ver nota de la propuesta): son 384 floats por
     evento, no aportan nada al front y duplicarían el peso del archivo.

Idempotente / repetible: no escribe nada en Neo4j, solo lee. Se puede correr
tantas veces como haga falta (cada vez que corras el pipeline de extracción o
el resolver, volvés a correr esto para refrescar site/data.json).

Uso:
    python 5_export_dashboard_data.py
    python 5_export_dashboard_data.py --out site/data.json
    python 5_export_dashboard_data.py --dry-run   # solo imprime conteos, no escribe
"""

import json
import os
import re
from datetime import datetime, timedelta

import numpy as np
import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

app = typer.Typer()

DEFAULT_OUT = "site/data.json"

# Retención del export (DD-071, 2026-08-28): eventos pasados hace más de
# PAST_RETENTION_DAYS días dejan de exportarse a site/data.json — Neo4j los
# conserva para siempre (nunca se borra un :Event, ver CLAUDE.md), esto es
# solo el filtro de qué se manda al navegador de cada visitante. De 743
# eventos con fecha parseable, 530 (71%) ya estaban a más de 30 días de hoy
# el 2026-08-28 — ese es el peso muerto que este corte elimina del JSON.
PAST_RETENTION_DAYS = 30

# ── Ranking (ver docs/dashboard_redesign_proposal.md sección 3) ─────────────
# P: multiplicadores que NO dependen de tiempo/sesión, se precalculan acá.
# NOTA (2026-08-13): el bonus por culturalIdentity se eliminó del ranking —
# el proyecto ya no es exclusivamente sobre eventos colombianos, ver DD en
# docs/decisions_es.md. culturalIdentity sigue viajando en el JSON como
# filtro opcional, simplemente no influye el score.
POLITICO_PENALTY = 0.55
LOW_CONFIDENCE_PENALTY = 0.80
LOW_CONFIDENCE_THRESHOLD = 0.50
FREE_BONUS = 1.05

_FREE_RE = re.compile(r"\bgratis\b|\bentrada libre\b|\bacc[eè]s libre\b|\bfree\b", re.IGNORECASE)


def is_free(price_range) -> bool:
    return bool(price_range) and bool(_FREE_RE.search(price_range))


def pctl_rank(values: list) -> dict:
    """Rango percentil [0,1] por posición en orden ascendente — empates
    comparten el mismo percentil (rank promedio), igual que el Cultural
    Relevance Score archivado (ver CLAUDE.md) del que se reusa la idea."""
    pairs = [(i, v) for i, v in enumerate(values) if v is not None]
    if not pairs:
        return {}
    pairs.sort(key=lambda p: p[1])
    n = len(pairs)
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][1] == pairs[i][1]:
            j += 1
        avg_rank = (i + j) / 2
        pctl = avg_rank / (n - 1) if n > 1 else 1.0
        for k in range(i, j + 1):
            out[pairs[k][0]] = pctl
        i = j + 1
    return out


EVENTS_QUERY = """
MATCH (e:Event)
OPTIONAL MATCH (src:Account {username: e.sourceAuthor})
WHERE NOT 'Rejected' IN labels(e)
  AND NOT 'PendingReview' IN labels(e)
  // :PendingReview = staging gate (ver review_events.py, 2026-08-21) — eventos
  // nuevos no salen al sitio hasta que Diego los revisa/aprueba a mano.
  AND e.isPublicInvitation = true
  AND e.isUpcoming = true
  AND e.eventDate IS NOT NULL AND e.eventDate <> ''
  // Retención (DD-071): eventos pasados hace más de PAST_RETENTION_DAYS no se
  // exportan — comparación de string sobre los primeros 10 caracteres de
  // eventDate (mismo patrón string-compare ya usado en 4_enrich_events_extract.py
  // para --max-post-age-days, DD-048): eventDate es 'YYYY-MM-DD' en la
  // práctica (confirmado: 743/751 eventos parsean con date.fromisoformat en
  // los primeros 10 chars), y ISO ordena lexicográficamente igual que
  // cronológicamente. $cutoffDate ya incluye todos los eventos futuros
  // (siempre son >= cutoff).
  AND substring(e.eventDate, 0, 10) >= $cutoffDate
  // Cuentas fuera de alcance (exclude_accounts.py, ver DD-045 y
  // config/excluded_accounts.json) — geográficamente fuera del proyecto,
  // tageadas con outOfScope=true en vez de borradas. src puede ser NULL si
  // por algún motivo la cuenta no está en el grafo; en ese caso no se
  // excluye por esto (no hay señal de que esté fuera de alcance).
  AND (src IS NULL OR NOT coalesce(src.outOfScope, false))
  // Requisito de ubicación (DD-045, tercera vuelta): basta con que el LLM haya
  // extraído ALGÚN texto de ubicación. Es lo único que el sitio muestra; si no
  // hay ni dirección ni nombre de lugar ni ciudad, el evento no es accionable
  // para nadie y ahí sí no tiene sentido publicarlo. NO se exige nada sobre el
  // :Location geocodificado — ver docstring del módulo.
  AND (trim(coalesce(e.exactAddress, '')) <> ''
       OR trim(coalesce(e.locationName, '')) <> ''
       OR trim(coalesce(e.cityName, '')) <> '')
// OPTIONAL: un evento sin :Location (o con :Location sin lat/lon) igual se
// exporta, con lat/lon = null → simplemente no tendrá pin en el mapa.
OPTIONAL MATCH (e)-[:LOCATED_AT]->(l:Location)
WITH e, l
// NOTA (DD-045, segunda vuelta): l.geocodeConfidence NO EXISTE en la base —
// Neo4j lo confirma con un warning ("property key does not exist"), no es
// que esté null en algunos nodos: nunca se escribió en ninguno. Se ve en el
// código de 4_enrich_locations.py, pero el script es idempotente por
// `lat IS NULL` como criterio de "falta geocodificar" — como todos los
// Location ya tenían lat/lon de antes de que se agregara ese campo, nunca se
// volvió a correr sobre ellos y el campo quedó sin poblar en la práctica
// (bug real, aparte, en 4_enrich_locations.py — no arreglado acá). Filtrar
// por esa propiedad devuelve 0 resultados. Se retira el filtro acá; la
// detección de coordenadas-fallback se hace en Python después de traer los
// datos (_filter_fallback_coordinates), sin depender de esta propiedad.
RETURN e.id AS id, e.title AS title, e.titleFr AS titleFr, e.category AS category, e.type AS type,
       e.eventArtTags AS eventArtTags, e.eventArtTagsFr AS eventArtTagsFr, e.imageUrl AS imageUrl, e.artType AS artType,
       e.eventDate AS eventDate, e.locationName AS locationName,
       e.cityName AS cityName, e.exactAddress AS exactAddress,
       l.lat AS lat, l.lon AS lon,
       e.hotnessScore AS hotnessScore, e.eventScore AS eventScore,
       e.confidence AS confidence, e.postCount AS postCount,
       e.description AS description, e.descriptionFr AS descriptionFr, e.priceRange AS priceRange,
       e.sourcePostUrl AS sourcePostUrl, e.sourceAuthor AS sourceAuthor,
       e.sourcePostDate AS sourcePostDate,
       e.culturalIdentity AS culturalIdentity, e.geoZone AS geoZone,
       e.parentInstitution AS parentInstitution, e.institutionType AS institutionType,
       e.photoPermission AS photoPermission,
       e.embedding AS embedding
"""

ACCOUNTS_QUERY = """
MATCH (a:Account)
WHERE (a.manualDataCuratedAt IS NOT NULL OR a.pageRankExact IS NOT NULL)
  AND NOT coalesce(a.outOfScope, false)
RETURN a.username AS username,
       coalesce(a.manualFollowersCount, a.followersCount) AS followers,
       a.verified AS verified,
       a.pageRankExact AS pageRankExact,
       a.betweennessExact AS betweennessExact,
       a.kCore AS kCore,
       a.participationCoef AS participationCoef,
       a.tier AS tier,
       a.geoZone AS geoZone,
       a.hasFreeEvents AS hasFreeEvents,
       a.eventFrequency AS eventFrequency,
       a.artType AS artType,
       a.culturalIdentity AS culturalIdentity
"""


def compute_similar_events(events: list, top_k: int = 5) -> None:
    """Vecinos más cercanos por coseno sobre embedding — mismo patrón
    vectorizado que 4_enrich_events_resolve.py. Muta cada dict de `events`
    agregando 'similarEventIds'. El embedding se descarta después de usarse."""
    idx_with_emb = [i for i, e in enumerate(events) if e.get("embedding")]
    if len(idx_with_emb) < 2:
        for e in events:
            e["similarEventIds"] = []
        return
    mat = np.array([events[i]["embedding"] for i in idx_with_emb], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed = mat / norms
    sim = normed @ normed.T
    for local_i, global_i in enumerate(idx_with_emb):
        order = np.argsort(-sim[local_i])
        neighbors = [idx_with_emb[j] for j in order if idx_with_emb[j] != global_i][:top_k]
        events[global_i]["similarEventIds"] = [events[j]["id"] for j in neighbors]
    no_emb = set(range(len(events))) - set(idx_with_emb)
    for i in no_emb:
        events[i]["similarEventIds"] = []


def compute_ranking_subscores(events: list, accounts_by_username: dict) -> None:
    """Agrega qScore/aScore/bScore/penaltyMultiplier a cada evento — todo lo
    que necesita contexto de percentil sobre el dataset completo, calculado
    UNA vez acá en vez de en cada visita del cliente. T y C quedan afuera a
    propósito (ver docstring del módulo)."""
    hotness_pctl = pctl_rank([e.get("hotnessScore") for e in events])

    pr_vals, bw_vals, fol_vals, pc_vals = [], [], [], []
    for e in events:
        acc = accounts_by_username.get(e.get("sourceAuthor"), {})
        pr_vals.append(acc.get("pageRankExact"))
        bw_vals.append(acc.get("betweennessExact"))
        fol_vals.append(acc.get("followers"))
        pc_vals.append(acc.get("participationCoef"))
    pr_pctl = pctl_rank(pr_vals)
    bw_pctl = pctl_rank(bw_vals)
    fol_pctl = pctl_rank(fol_vals)
    pc_pctl = pctl_rank(pc_vals)

    for i, e in enumerate(events):
        event_score = e.get("eventScore") or 0.0
        confidence = e.get("confidence") or 0.0
        q = 0.70 * event_score + 0.30 * confidence

        acc = accounts_by_username.get(e.get("sourceAuthor"), {})
        a = (
            0.40 * pr_pctl.get(i, 0.0)
            + 0.25 * bw_pctl.get(i, 0.0)
            + 0.20 * fol_pctl.get(i, 0.0)
            + 0.10 * pc_pctl.get(i, 0.0)
            + 0.05 * (1.0 if acc.get("verified") else 0.0)
        )

        post_count = e.get("postCount") or 1
        b = 0.75 * hotness_pctl.get(i, 0.0) + 0.25 * min(post_count / 3.0, 1.0)

        penalty = 1.0
        if e.get("category") == "politico":
            penalty *= POLITICO_PENALTY
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            penalty *= LOW_CONFIDENCE_PENALTY
        if is_free(e.get("priceRange")):
            penalty *= FREE_BONUS

        e["qScore"] = round(q, 4)
        e["aScore"] = round(a, 4)
        e["bScore"] = round(b, 4)
        e["penaltyMultiplier"] = round(penalty, 4)
        e["isFree"] = is_free(e.get("priceRange"))


def _dedupe_conflicting_locations(events: list[dict]) -> list[dict]:
    """EVENTS_QUERY puede devolver más de una fila por evento cuando un
    :Event tiene más de una relación :LOCATED_AT (varios :Location distintos
    para el mismo lugar, cada uno geocodificado por separado). Descubierto el
    2026-08-15 al validar el fix de coordenadas-fallback: 5 eventos traían
    2-4 filas con el MISMO locationName pero coordenadas completamente
    distintas y sin relación entre sí (ej. "Café Otraparte" resolviendo a la
    vez en Colombia y en España) — geocodificación en conflicto, no un
    duplicado inofensivo. Si las filas de un mismo id tienen coordenadas que
    no coinciden, no hay forma de saber cuál (si alguna) es correcta: se
    colapsa a una sola fila **con lat/lon = None** (el evento sigue en el
    sitio, simplemente sin pin de mapa — DD-045, decisión de producto del
    2026-08-15: existir en el sitio no depende de la geocodificación). Si
    coinciden (duplicado cartesiano real, mismas coordenadas repetidas), se
    colapsa a una sola fila conservando la coordenada.
    """
    by_id: dict[str, list[dict]] = {}
    for e in events:
        by_id.setdefault(e["id"], []).append(e)

    out: list[dict] = []
    conflicts = 0
    for eid, rows in by_id.items():
        coords = {(r.get("lat"), r.get("lon")) for r in rows}
        row = rows[0]
        if len(coords) > 1:
            conflicts += 1
            # geocodificación en conflicto — no se adivina cuál es correcta:
            # el evento se conserva, pero sin coordenada.
            row["lat"] = None
            row["lon"] = None
        out.append(row)

    if conflicts:
        print(f"  ⚠️  {conflicts} eventos con coordenadas en conflicto entre sí "
              f"(varias geocodificaciones distintas para el mismo lugar) -> "
              f"sin coordenada confiable (se mantienen, sin pin de mapa)")
    return out


def _filter_fallback_coordinates(events: list[dict], min_distinct_names: int = 3) -> list[dict]:
    """Detecta y anula las coordenadas que son, con alta probabilidad,
    el resultado de geocodificar el --city-hint por defecto en vez del lugar
    real (ver DD-045). No depende de l.geocodeConfidence (propiedad que en la
    práctica nunca se pobló — ver nota en EVENTS_QUERY): la señal es empírica,
    verificada contra datos reales el 2026-08-15 — una coordenada real de un
    lugar específico tiene locationName repetido o casi idéntico entre los
    eventos que la comparten (la misma cuenta postea varias veces desde el
    mismo sitio real); una coordenada de fallback tiene locationName
    completamente distinto entre eventos no relacionados (ej. un handle de
    Instagram, un teatro en Medellín, y una dirección real de París,
    los tres con la misma lat/lon). Umbral: 3+ locationName distintos en la
    misma coordenada (redondeada a 4 decimales, ~11m de precisión) se
    considera fallback.

    Los eventos del grupo NO se excluyen (DD-045, decisión de producto del
    2026-08-15): se les setea `lat`/`lon` a None y siguen en la exportación.
    El sitio muestra el texto de ubicación del LLM, no la coordenada; lo único
    que se pierde es el pin en el mapa (feature futura).
    """
    groups: dict[tuple, list[dict]] = {}
    for e in events:
        lat, lon = e.get("lat"), e.get("lon")
        if lat is None or lon is None:
            continue
        key = (round(lat, 4), round(lon, 4))
        groups.setdefault(key, []).append(e)

    bad_ids: set[str] = set()
    for key, group in groups.items():
        names = {(e.get("locationName") or "").strip().lower() for e in group}
        names.discard("")
        if len(names) >= min_distinct_names:
            bad_ids.update(e["id"] for e in group)
            print(f"  ⚠️  Coordenada sospechosa {key}: {len(group)} eventos, "
                  f"{len(names)} locationName distintos -> sin pin de mapa")

    if bad_ids:
        for e in events:
            if e["id"] in bad_ids:
                e["lat"] = None
                e["lon"] = None
        print(f"📍 {len(bad_ids)} eventos sin coordenada confiable por fallback de "
              f"geocodificación (se mantienen, sin pin de mapa)")
    return events


@app.command()
def main(
    out: str = typer.Option(DEFAULT_OUT, "--out", help="Ruta del JSON de salida"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo imprime conteos, no escribe el archivo"),
    past_days: int = typer.Option(
        PAST_RETENTION_DAYS, "--past-days",
        help="Retención: no exportar eventos pasados hace más de N días (DD-071). "
             "Los eventos futuros siempre se exportan sin importar este valor.",
    ),
):
    cutoff_date = (datetime.utcnow().date() - timedelta(days=past_days)).isoformat()
    print(f"🗓️  Retención: se excluyen eventos pasados antes de {cutoff_date} (--past-days {past_days})\n")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    with driver.session() as session:
        events = [dict(r) for r in session.run(EVENTS_QUERY, cutoffDate=cutoff_date)]
        accounts = [dict(r) for r in session.run(ACCOUNTS_QUERY)]
    driver.close()

    print(f"📦 {len(events)} filas antes de deduplicar/filtrar")
    events = _dedupe_conflicting_locations(events)
    events = _filter_fallback_coordinates(events)
    with_coords = sum(1 for e in events if e.get("lat") is not None and e.get("lon") is not None)
    print(f"📦 {len(events)} eventos válidos con texto de ubicación "
          f"({with_coords} con coordenada confiable → pin de mapa; "
          f"{len(events) - with_coords} sin pin)")
    print(f"📦 {len(accounts)} cuentas curadas/con métricas de grafo")

    accounts_by_username = {a["username"]: a for a in accounts}

    compute_similar_events(events)
    compute_ranking_subscores(events, accounts_by_username)

    # El embedding ya cumplió su función (similar_events) — no viaja al JSON.
    for e in events:
        e.pop("embedding", None)

    payload = {
        "exportedAt": datetime.utcnow().isoformat() + "Z",
        "counts": {"events": len(events), "accounts": len(accounts)},
        "events": events,
        "accounts": accounts,
    }

    size_kb = len(json.dumps(payload, ensure_ascii=False)) / 1024
    print(f"📏 Tamaño estimado del JSON: {size_kb:.0f} KB")

    if dry_run:
        print("\n[dry-run] No se escribió ningún archivo.")
        return

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n✅ Exportado a {out}")


if __name__ == "__main__":
    app()
