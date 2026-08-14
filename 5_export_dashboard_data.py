"""
5_export_dashboard_data.py — Hub Cultural DU

Exporta desde Neo4j todo lo que el sitio nuevo de descubrimiento de eventos
necesita (ver docs/dashboard_redesign_proposal.md) a un único JSON estático
que el front-end (site/) lee directo, sin backend ni dependencia de Neo4j en
producción — decisión tomada en la propuesta: 664 eventos caben en ~1-2MB,
un sitio estático (GitHub Pages / Cloudflare Pages) alcanza de sobra.

Qué hace:
  1. Trae eventos válidos (no rechazados, invitación pública futura confirmada,
     fecha real) Y CON COORDENADAS REALES (decisión 2026-08-13: un evento sin
     lat/lon queda fuera de la experiencia de descubrimiento por completo —
     es filtro de presentación, no borra nada del grafo).
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
from datetime import datetime

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
WHERE NOT 'Rejected' IN labels(e)
  AND e.isPublicInvitation = true
  AND e.isUpcoming = true
  AND e.eventDate IS NOT NULL AND e.eventDate <> ''
OPTIONAL MATCH (e)-[:LOCATED_AT]->(l:Location)
WITH e, l
WHERE l IS NOT NULL AND l.lat IS NOT NULL AND l.lon IS NOT NULL
RETURN e.id AS id, e.title AS title, e.category AS category, e.type AS type,
       e.eventArtTags AS eventArtTags, e.artType AS artType,
       e.eventDate AS eventDate, e.locationName AS locationName,
       e.cityName AS cityName, e.exactAddress AS exactAddress,
       l.lat AS lat, l.lon AS lon,
       e.hotnessScore AS hotnessScore, e.eventScore AS eventScore,
       e.confidence AS confidence, e.postCount AS postCount,
       e.description AS description, e.priceRange AS priceRange,
       e.sourcePostUrl AS sourcePostUrl, e.sourceAuthor AS sourceAuthor,
       e.sourcePostDate AS sourcePostDate,
       e.culturalIdentity AS culturalIdentity, e.geoZone AS geoZone,
       e.parentInstitution AS parentInstitution, e.institutionType AS institutionType,
       e.embedding AS embedding
"""

ACCOUNTS_QUERY = """
MATCH (a:Account)
WHERE a.manualDataCuratedAt IS NOT NULL OR a.pageRankExact IS NOT NULL
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


@app.command()
def main(
    out: str = typer.Option(DEFAULT_OUT, "--out", help="Ruta del JSON de salida"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo imprime conteos, no escribe el archivo"),
):
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    with driver.session() as session:
        events = [dict(r) for r in session.run(EVENTS_QUERY)]
        accounts = [dict(r) for r in session.run(ACCOUNTS_QUERY)]
    driver.close()

    print(f"📦 {len(events)} eventos válidos y geocodificados")
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
