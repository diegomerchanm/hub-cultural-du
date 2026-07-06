"""
Fase 4-C — Deduplicación semántica de nodos (:Event) existentes en Neo4j.

Algoritmo:
  1. Carga todos los eventos con embeddings.
  2. Agrupa por locationName.
  3. Dentro de cada grupo, compara pares con fecha ±date_window días.
  4. Si similitud coseno > threshold: fusiona (mantiene el de mayor hotnessScore,
     redirige todas las relaciones, elimina el duplicado).

Sin APOC: redireccionamiento manual de cada tipo de relación.
Idempotente: fusionar duplicados no genera nuevos duplicados.
"""

import os
import random
from collections import defaultdict
from datetime import datetime
from typing import Optional

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase
from scipy.spatial.distance import cosine as cosine_dist
from tqdm import tqdm
from unidecode import unidecode

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# Tipos de relaciones hacia/desde un Event (sin APOC — lista explícita)
INCOMING_REL_TYPES = ["MENTIONS_EVENT", "ORGANIZED", "PARTICIPATED_IN", "SUPPORTED"]
OUTGOING_REL_TYPES = ["LOCATED_AT", "HAS_HASHTAG"]


# ── 2. Helpers ────────────────────────────────────────────────────────────────
def normalize_loc(loc: Optional[str]) -> str:
    """Normaliza locationName: lowercase + strip + quitar acentos.

    Garantiza que "París", "Paris" y "paris" caigan en el mismo grupo.
    """
    return unidecode((loc or "").lower().strip())


def dates_close(d1: Optional[str], d2: Optional[str], window: int) -> bool:
    if not d1 or not d2:
        return True  # sin fecha → no excluir por fecha
    try:
        dt1 = datetime.fromisoformat(d1)
        dt2 = datetime.fromisoformat(d2)
        return abs((dt1 - dt2).days) <= window
    except Exception:
        return True


def cosine_sim(a: list, b: list) -> float:
    try:
        return float(1.0 - cosine_dist(a, b))
    except Exception:
        return 0.0


# ── 3. Cargar eventos ─────────────────────────────────────────────────────────
def load_all_events(session) -> list:
    return session.run("""
        MATCH (e:Event)
        RETURN e.id           AS id,
               e.title        AS title,
               e.eventDate    AS eventDate,
               e.locationName AS locationName,
               e.hotnessScore AS hotnessScore,
               e.postCount    AS postCount,
               e.embedding    AS embedding
        ORDER BY e.hotnessScore DESC
    """).data()


# ── 4. Fusionar dos eventos (sin APOC) ────────────────────────────────────────
def merge_events(session, canonical_id: str, dup_id: str, dry_run: bool) -> int:
    """
    Redirige todas las relaciones de dup → canonical, luego elimina dup.
    Retorna el número de relaciones redirigidas.
    """
    redirected = 0

    if dry_run:
        # Solo contar cuántas relaciones habría que redirigir
        r = session.run("""
            MATCH (e:Event {id: $id})-[r]-()
            RETURN count(r) AS n
        """, id=dup_id).single()
        return r["n"] if r else 0

    # Relaciones ENTRANTES al duplicado
    for rel_type in INCOMING_REL_TYPES:
        result = session.run(f"""
            MATCH (src)-[r:{rel_type}]->(dup:Event {{id: $dup_id}})
            MATCH (canon:Event {{id: $canon_id}})
            MERGE (src)-[:{rel_type}]->(canon)
            DELETE r
            RETURN count(r) AS n
        """, dup_id=dup_id, canon_id=canonical_id).single()
        if result:
            redirected += result["n"]

    # Relaciones SALIENTES del duplicado
    for rel_type in OUTGOING_REL_TYPES:
        result = session.run(f"""
            MATCH (dup:Event {{id: $dup_id}})-[r:{rel_type}]->(dst)
            MATCH (canon:Event {{id: $canon_id}})
            MERGE (canon)-[:{rel_type}]->(dst)
            DELETE r
            RETURN count(r) AS n
        """, dup_id=dup_id, canon_id=canonical_id).single()
        if result:
            redirected += result["n"]

    # Actualizar postCount en canonical
    session.run("""
        MATCH (canon:Event {id: $canon_id})
        MATCH (dup:Event   {id: $dup_id})
        SET canon.postCount = coalesce(canon.postCount, 0) + coalesce(dup.postCount, 0)
    """, canon_id=canonical_id, dup_id=dup_id)

    # Eliminar duplicado (debería estar sin relaciones)
    session.run("MATCH (e:Event {id: $id}) DETACH DELETE e", id=dup_id)

    return redirected


# ── 5. Encontrar y resolver duplicados ────────────────────────────────────────
def resolve_duplicates(
    threshold: float = 0.75,
    date_window: int = 3,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Retorna (n_pares_duplicados, n_relaciones_redirigidas).
    """
    print("\n🔍 Cargando eventos desde Neo4j...")
    with driver.session() as session:
        events = load_all_events(session)

    if not events:
        print("  ⚠️  No hay eventos en la base de datos.")
        return 0, 0

    # Filtrar los que tienen embedding
    with_emb    = [e for e in events if e.get("embedding")]
    without_emb = len(events) - len(with_emb)
    print(f"  📊 {len(events)} eventos  ({len(with_emb)} con embedding, {without_emb} sin embedding)")

    if len(with_emb) < 2:
        print("  ✅ Menos de 2 eventos con embedding — nada que resolver.")
        return 0, 0

    # Agrupar por locationName normalizado (None → grupo especial "")
    # normalize_loc: lowercase + strip + unidecode → "París" = "Paris" = "paris"
    by_location: dict = defaultdict(list)
    for e in with_emb:
        by_location[normalize_loc(e.get("locationName"))].append(e)

    print(f"  🗺️  {len(by_location)} grupos de localización")

    # IDs ya fusionados en esta sesión (para evitar doble fusión)
    merged_ids:   set  = set()
    n_pairs      = 0
    n_redirected = 0
    near_misses: list  = []   # pares que pasan loc+fecha pero sim < threshold

    for loc_key, group in tqdm(by_location.items(), desc="  Localizaciones"):
        if len(group) < 2:
            continue

        # Comparar todos los pares dentro del grupo
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                ea = group[i]
                eb = group[j]

                if ea["id"] in merged_ids or eb["id"] in merged_ids:
                    continue

                # Filtro por fecha
                if not dates_close(ea.get("eventDate"), eb.get("eventDate"), date_window):
                    continue

                sim = cosine_sim(ea["embedding"], eb["embedding"])

                if sim < threshold:
                    # Guardar para muestra de calibración en dry-run
                    if dry_run:
                        near_misses.append({
                            "sim":    sim,
                            "loc":    loc_key,
                            "date_a": ea.get("eventDate"),
                            "date_b": eb.get("eventDate"),
                            "title_a": ea.get("title", ea["id"]),
                            "title_b": eb.get("title", eb["id"]),
                        })
                    continue

                # Canónico = el de mayor hotnessScore
                hotness_a = ea.get("hotnessScore") or 0.0
                hotness_b = eb.get("hotnessScore") or 0.0
                canonical, duplicate = (ea, eb) if hotness_a >= hotness_b else (eb, ea)

                if dry_run:
                    print(
                        f"  [dry-run] MERGE {duplicate['id']} → {canonical['id']}"
                        f"  sim={sim:.3f}  loc='{loc_key}'"
                        f"  dates={ea.get('eventDate')} / {eb.get('eventDate')}"
                    )

                with driver.session() as session:
                    redirected = merge_events(session, canonical["id"], duplicate["id"], dry_run)

                n_pairs      += 1
                n_redirected += redirected
                merged_ids.add(duplicate["id"])

    # ── Muestra de calibración (dry-run) ──────────────────────────────────────
    if dry_run and near_misses:
        sample = random.sample(near_misses, min(5, len(near_misses)))
        sample.sort(key=lambda x: -x["sim"])
        print(f"\n  📐 MUESTRA DE CALIBRACIÓN — 5 pares bajo threshold={threshold}")
        print(f"  (pasaron loc+fecha, sim < {threshold} — útiles para ajustar umbral)")
        print(f"  {'─'*60}")
        for r in sample:
            print(f"  sim={r['sim']:.3f}  loc='{r['loc']}'  "
                  f"dates={r['date_a']} / {r['date_b']}")
            print(f"    A: {r['title_a']}")
            print(f"    B: {r['title_b']}")

    return n_pairs, n_redirected


# ── 6. Reporte de eventos ──────────────────────────────────────────────────────
def print_event_summary():
    print("\n📊 Estado actual de (:Event)")
    print("=" * 55)
    with driver.session() as session:
        stats = session.run("""
            MATCH (e:Event)
            RETURN count(e)                    AS total,
                   count(e.embedding)          AS withEmbedding,
                   avg(e.hotnessScore)         AS avgHotness,
                   max(e.postCount)            AS maxPostCount
        """).single()
        by_type = session.run("""
            MATCH (e:Event)
            RETURN e.type AS type, count(*) AS n
            ORDER BY n DESC LIMIT 10
        """).data()
        by_loc = session.run("""
            MATCH (e:Event) WHERE e.locationName IS NOT NULL AND e.locationName <> ''
            RETURN e.locationName AS loc, count(*) AS n
            ORDER BY n DESC LIMIT 5
        """).data()

    print(f"\n  Total eventos     : {stats['total']}")
    print(f"  Con embedding     : {stats['withEmbedding']}")
    print(f"  Hotness promedio  : {(stats['avgHotness'] or 0):.3f}")
    print(f"  Max postCount     : {stats['maxPostCount'] or 0}")

    if by_type:
        print("\n  Por tipo:")
        for row in by_type:
            print(f"    {(row['type'] or '?'):<45} {row['n']:>4}")

    if by_loc:
        print("\n  Top localizaciones:")
        for row in by_loc:
            print(f"    {row['loc']:<40} {row['n']:>4}")


# ── 7. CLI ────────────────────────────────────────────────────────────────────
app = typer.Typer(add_completion=False)


@app.command()
def main(
    threshold: float = typer.Option(
        0.75, "--threshold", help="Similitud coseno mínima para considerar duplicado."
    ),
    date_window: int = typer.Option(
        3, "--date-window", help="Ventana de días para considerar misma fecha."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Mostrar duplicados sin fusionar."
    ),
    summary: bool = typer.Option(
        True, "--summary/--no-summary", help="Mostrar resumen de eventos al final."
    ),
):
    """
    Fase 4-C: fusiona eventos duplicados en Neo4j.

    Criterio de duplicado:
      • misma locationName (o sin location en alguno)
      • fecha dentro de ±date_window días
      • similitud coseno del embedding > threshold (default 0.75)

    Canónico = evento con mayor hotnessScore.
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    print(f"⚙️  Parámetros: threshold={threshold}  date_window=±{date_window}d  dry_run={dry_run}")

    n_pairs, n_redirected = resolve_duplicates(
        threshold=threshold,
        date_window=date_window,
        dry_run=dry_run,
    )

    tag = "[dry-run] " if dry_run else ""
    print(f"\n  {tag}Pares duplicados encontrados : {n_pairs}")
    print(f"  {tag}Relaciones redirigidas       : {n_redirected}")

    if summary:
        print_event_summary()

    driver.close()
    print("\n✅ Resolución de eventos completa.")


if __name__ == "__main__":
    app()
