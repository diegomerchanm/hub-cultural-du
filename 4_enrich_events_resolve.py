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
import re
from datetime import datetime
from typing import Optional

import numpy as np
import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase
from tqdm import tqdm

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
def dates_close(d1: Optional[str], d2: Optional[str], window: int) -> bool:
    if not d1 or not d2:
        return True  # sin fecha → no excluir por fecha
    try:
        dt1 = datetime.fromisoformat(d1)
        dt2 = datetime.fromisoformat(d2)
        return abs((dt1 - dt2).days) <= window
    except Exception:
        return True


def dates_really_close(d1: Optional[str], d2: Optional[str], window: int) -> bool:
    """True solo si AMBAS fechas existen, parsean, y están dentro de la
    ventana — a diferencia de dates_close(), no asume corroboración
    cuando falta una fecha."""
    if not d1 or not d2:
        return False
    try:
        dt1 = datetime.fromisoformat(d1)
        dt2 = datetime.fromisoformat(d2)
        return abs((dt1 - dt2).days) <= window
    except Exception:
        return False


# DD-041: guardrail liviano de conflicto geográfico. Al sacar locationName
# como filtro (DD-040), aparecieron fusiones reales entre eventos en países
# distintos ('París'/'Colombia', 'Ecuador'/'Paris', 'París'/'Madrid') —
# posts genéricos de la diáspora (fiestas, encuentros) comparten vocabulario
# aunque describan eventos distintos en lugares distintos. No es un gazetteer
# exhaustivo ni un geocoder real — es una lista chica de países/ciudades que
# aparecieron en los datos reales, pensada para atajar los casos más obvios
# sin reintroducir el problema original de exigir match exacto de string:
# solo bloquea cuando AMBAS ubicaciones mencionan un país/ciudad reconocido
# Y esos países no coinciden en absoluto — si falta evidencia de cualquiera
# de los dos lados, no bloquea (default permisivo, igual que antes).
COUNTRY_ALIASES = {
    "colombia": "CO", "francia": "FR", "france": "FR", "ecuador": "EC",
    "mexico": "MX", "méxico": "MX", "honduras": "HN", "portugal": "PT",
    "espana": "ES", "españa": "ES", "spain": "ES", "brasil": "BR", "brazil": "BR",
    "argentina": "AR", "chile": "CL", "peru": "PE", "perú": "PE",
    "venezuela": "VE", "panama": "PA", "panamá": "PA", "costa rica": "CR",
    "cuba": "CU", "guatemala": "GT", "bolivia": "BO", "uruguay": "UY",
    "paraguay": "PY", "nicaragua": "NI", "el salvador": "SV",
    "estados unidos": "US", "austria": "AT", "italia": "IT", "italy": "IT",
    "alemania": "DE", "germany": "DE", "belgica": "BE", "bélgica": "BE",
    "belgium": "BE", "suiza": "CH", "switzerland": "CH",
}

# Ciudades/barrios frecuentes en el corpus, mapeados a país — permite que
# "Francia"+"Paris" o "Colombia"+"Medellín" NO cuenten como conflicto
# (jerarquía país↔ciudad), mientras que "Ecuador"+"Paris" sí.
CITY_TO_COUNTRY = {
    "paris": "FR", "parís": "FR", "bastille": "FR", "belleville": "FR",
    "montmartre": "FR", "menilmontant": "FR", "ménilmontant": "FR",
    "boulogne-billancourt": "FR", "boulogne billancourt": "FR", "montpellier": "FR",
    "bogota": "CO", "bogotá": "CO", "medellin": "CO", "medellín": "CO",
    "cali": "CO", "bucaramanga": "CO", "barranquilla": "CO", "cartagena": "CO",
    "pereira": "CO", "manizales": "CO", "envigado": "CO",
    "madrid": "ES", "guadalajara": "MX", "brooklyn": "US",
    "nueva york": "US", "new york": "US", "vienna": "AT", "viena": "AT",
}


def _extract_country_codes(loc: Optional[str]) -> set:
    """País(es) reconocido(s) en un string de ubicación libre, vía match de
    palabra completa. Normaliza separadores no alfabéticos (@, _, -, etc.)
    a espacios antes de matchear — sin esto, "bastille" dentro de
    "@osullivans_bastille" no tiene borde de palabra real (el guion bajo
    cuenta como \\w en regex), y un handle de Instagram como locationName
    (frecuente en este corpus) pasaría desapercibido."""
    if not loc:
        return set()
    text = re.sub(r"[^a-záéíóúüñ]+", " ", loc.lower())
    codes = set()
    for name, code in {**COUNTRY_ALIASES, **CITY_TO_COUNTRY}.items():
        if re.search(rf"\b{re.escape(name)}\b", text):
            codes.add(code)
    return codes


def geo_conflict(loc_a: Optional[str], loc_b: Optional[str]) -> bool:
    """True solo si ambas ubicaciones mencionan un país reconocido Y esos
    países no se solapan en absoluto — evidencia insuficiente en cualquiera
    de los dos lados nunca bloquea (permisivo por diseño)."""
    codes_a = _extract_country_codes(loc_a)
    codes_b = _extract_country_codes(loc_b)
    if not codes_a or not codes_b:
        return False
    return codes_a.isdisjoint(codes_b)


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
    threshold_no_date: float = 0.85,
    date_window: int = 3,
    dry_run: bool = False,
) -> tuple[int, int, int, int, int]:
    """
    Retorna (n_pares_duplicados, n_relaciones_redirigidas, n_sin_evidencia,
    n_fechas_lejanas, n_conflicto_geografico).
    """
    print("\n🔍 Cargando eventos desde Neo4j...")
    with driver.session() as session:
        events = load_all_events(session)

    if not events:
        print("  ⚠️  No hay eventos en la base de datos.")
        return 0, 0, 0, 0, 0

    # Filtrar los que tienen embedding
    with_emb    = [e for e in events if e.get("embedding")]
    without_emb = len(events) - len(with_emb)
    print(f"  📊 {len(events)} eventos  ({len(with_emb)} con embedding, {without_emb} sin embedding)")

    if len(with_emb) < 2:
        print("  ✅ Menos de 2 eventos con embedding — nada que resolver.")
        return 0, 0, 0, 0, 0

    # DD-040: antes se agrupaba por locationName normalizado y solo se
    # comparaban pares DENTRO del mismo grupo — eso perdía duplicados reales
    # cuando el mismo venue se describía distinto entre posts (ej. "La
    # Palmeraie, 20 Rue..." vs "20 Rue...", que caen en grupos de string
    # distintos) o cuando a uno de los dos posts le faltaba locationName del
    # todo (nunca llegaban a compararse, ni siquiera entraban al mismo grupo
    # — confirmado con dos pares reales de la corrida de producción del
    # 2026-08-12: "Los Tucanes de Tijuana" x2 y "L'Astrologue..." x2).
    #
    # Ahora se compara TODO par de eventos por similitud de embedding
    # primero (matriz de coseno vectorizada con numpy — con ~700 eventos
    # son ~250k pares, trivial). La fecha sigue siendo evidencia obligatoria
    # cuando ambas existen (no se relaja, igual que antes). Si falta alguna
    # fecha, se exige un umbral de similitud más alto (threshold_no_date)
    # como evidencia compensatoria. Ubicación ya no es un filtro — queda
    # solo como dato informativo en los logs de fusión.
    n = len(with_emb)
    print(f"  🧮 Calculando similitud coseno de los {n * (n - 1) // 2} pares posibles...")
    emb_matrix = np.array([e["embedding"] for e in with_emb], dtype=float)
    norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # evita división por cero si algún embedding quedó nulo
    normed = emb_matrix / norms
    sim_matrix = normed @ normed.T

    # Orden por similitud descendente: los merges de mayor confianza ocurren
    # primero, así merged_ids evita que una fusión en cadena una tres
    # eventos que en realidad son solo parecidos de a pares.
    order = sorted(
        ((sim_matrix[i, j], i, j) for i in range(n) for j in range(i + 1, n)),
        key=lambda x: -x[0],
    )

    merged_ids:   set  = set()
    n_pairs       = 0
    n_redirected  = 0
    n_no_evidence = 0   # sim insuficiente (dado el umbral que aplique según haya fecha o no)
    n_dates_far   = 0   # ambas fechas conocidas pero fuera de la ventana
    n_geo_conflict = 0  # sim+fecha ok, pero países/ciudades reconocidos no coinciden (DD-041)
    sim_near_misses:   list = []  # fecha cercana (o sin fecha), pero sim insuficiente
    date_near_misses:  list = []  # sim ≥ threshold, pero fecha fuera de ventana (DD-040)
    geo_conflicts_sample: list = []  # sim+fecha ok, bloqueados por geo_conflict (DD-041)

    for sim, i, j in tqdm(order, desc="  Pares"):
        ea, eb = with_emb[i], with_emb[j]
        if ea["id"] in merged_ids or eb["id"] in merged_ids:
            continue

        d1, d2 = ea.get("eventDate"), eb.get("eventDate")

        if d1 and d2:
            dates_ok = dates_really_close(d1, d2, date_window)
            if not dates_ok:
                if sim >= threshold and dry_run and len(date_near_misses) < 200:
                    date_near_misses.append({
                        "sim": sim, "date_a": d1, "date_b": d2,
                        "loc_a": ea.get("locationName"), "loc_b": eb.get("locationName"),
                        "title_a": ea.get("title", ea["id"]), "title_b": eb.get("title", eb["id"]),
                    })
                n_dates_far += 1
                continue  # fechas conocidas y lejanas -> nunca fusionar
            required = threshold
        else:
            # falta alguna fecha -> exige más similitud como evidencia compensatoria
            required = threshold_no_date

        if sim < required:
            if d1 and d2 and dry_run and len(sim_near_misses) < 200:
                sim_near_misses.append({
                    "sim": sim, "date_a": d1, "date_b": d2,
                    "loc_a": ea.get("locationName"), "loc_b": eb.get("locationName"),
                    "title_a": ea.get("title", ea["id"]), "title_b": eb.get("title", eb["id"]),
                })
            n_no_evidence += 1
            continue

        # DD-041: guardrail de conflicto geográfico — sim y fecha ya dieron
        # luz verde, pero si ambas ubicaciones mencionan un país/ciudad
        # reconocido y no coinciden en absoluto, no fusionar (ej. 'Colombia'
        # vs 'Francia', 'Ecuador' vs 'Paris'). Evidencia insuficiente en
        # cualquiera de los dos lados no bloquea — permisivo por diseño.
        loc_a, loc_b = ea.get("locationName"), eb.get("locationName")
        if geo_conflict(loc_a, loc_b):
            n_geo_conflict += 1
            if dry_run and len(geo_conflicts_sample) < 200:
                geo_conflicts_sample.append({
                    "sim": sim, "loc_a": loc_a, "loc_b": loc_b,
                    "title_a": ea.get("title", ea["id"]), "title_b": eb.get("title", eb["id"]),
                })
            continue

        # Canónico = el de mayor hotnessScore
        hotness_a = ea.get("hotnessScore") or 0.0
        hotness_b = eb.get("hotnessScore") or 0.0
        canonical, duplicate = (ea, eb) if hotness_a >= hotness_b else (eb, ea)

        if dry_run:
            print(
                f"  [dry-run] MERGE {duplicate['id']} → {canonical['id']}"
                f"  sim={sim:.3f}  dates={d1 or '-'} / {d2 or '-'}"
                f"\n             loc: '{loc_a or '-'}' / '{loc_b or '-'}'"
                f"\n             A: {ea.get('title') or ea['id']}"
                f"\n             B: {eb.get('title') or eb['id']}"
            )

        with driver.session() as session:
            redirected = merge_events(session, canonical["id"], duplicate["id"], dry_run)

        n_pairs      += 1
        n_redirected += redirected
        merged_ids.add(duplicate["id"])

    # ── Muestras de calibración (dry-run) ───────────────────────────────────
    if dry_run and sim_near_misses:
        sample = random.sample(sim_near_misses, min(5, len(sim_near_misses)))
        sample.sort(key=lambda x: -x["sim"])
        print(f"\n  📐 MUESTRA — evidencia de fecha ok, similitud insuficiente")
        print(f"  (útil para calibrar threshold={threshold}/threshold_no_date={threshold_no_date})")
        print(f"  {'─'*60}")
        for r in sample:
            print(f"  sim={r['sim']:.3f}  dates={r['date_a'] or '-'} / {r['date_b'] or '-'}")
            print(f"    A: {r['title_a']}  (loc: {r['loc_a'] or '-'})")
            print(f"    B: {r['title_b']}  (loc: {r['loc_b'] or '-'})")

    if dry_run and date_near_misses:
        sample = random.sample(date_near_misses, min(5, len(date_near_misses)))
        sample.sort(key=lambda x: -x["sim"])
        print(f"\n  📅 MUESTRA — similitud ≥{threshold}, fecha fuera de ventana ±{date_window}d (DD-040)")
        print(f"  (útil para decidir si {date_window} días se está quedando corto)")
        print(f"  {'─'*60}")
        for r in sample:
            print(f"  sim={r['sim']:.3f}  dates={r['date_a']} / {r['date_b']}")
            print(f"    A: {r['title_a']}  (loc: {r['loc_a'] or '-'})")
            print(f"    B: {r['title_b']}  (loc: {r['loc_b'] or '-'})")

    if dry_run and geo_conflicts_sample:
        sample = random.sample(geo_conflicts_sample, min(5, len(geo_conflicts_sample)))
        sample.sort(key=lambda x: -x["sim"])
        print(f"\n  🌍 MUESTRA — sim+fecha ok, bloqueados por conflicto geográfico (DD-041)")
        print(f"  (revisar si alguno es en realidad un falso positivo del gazetteer)")
        print(f"  {'─'*60}")
        for r in sample:
            print(f"  sim={r['sim']:.3f}  loc='{r['loc_a'] or '-'}' / '{r['loc_b'] or '-'}'")
            print(f"    A: {r['title_a']}")
            print(f"    B: {r['title_b']}")

    return n_pairs, n_redirected, n_no_evidence, n_dates_far, n_geo_conflict


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
        0.75, "--threshold",
        help="Similitud coseno mínima para considerar duplicado cuando AMBAS fechas existen y están cerca."
    ),
    threshold_no_date: float = typer.Option(
        0.85, "--threshold-no-date",
        help="Similitud coseno mínima cuando falta la fecha en alguno de los dos eventos "
             "(más exigente — sin fecha para corroborar, hace falta más evidencia semántica)."
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

    DD-040 — rediseño: compara TODO par de eventos por similitud de embedding
    (no se agrupa por locationName — eso perdía duplicados reales cuando el
    mismo venue se describía distinto entre posts, o cuando a un post le
    faltaba locationName del todo). Criterio de duplicado:
      • si ambas fechas existen: deben estar dentro de ±date_window días
        (obligatorio, sin excepción) Y similitud ≥ threshold
      • si falta alguna fecha: similitud ≥ threshold_no_date (más exigente,
        compensa la falta de corroboración por fecha)
      • locationName ya NO es un filtro de agrupamiento, pero DD-041 exige
        que, si ambas ubicaciones mencionan un país/ciudad reconocido, no
        se contradigan (ej. 'Colombia' vs 'Francia' bloquea; falta de
        evidencia en cualquiera de los dos lados no bloquea)

    Canónico = evento con mayor hotnessScore.
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    print(f"⚙️  Parámetros: threshold={threshold}  threshold_no_date={threshold_no_date}  "
          f"date_window=±{date_window}d  dry_run={dry_run}")

    n_pairs, n_redirected, n_no_evidence, n_dates_far, n_geo_conflict = resolve_duplicates(
        threshold=threshold,
        threshold_no_date=threshold_no_date,
        date_window=date_window,
        dry_run=dry_run,
    )

    tag = "[dry-run] " if dry_run else ""
    print(f"\n  {tag}Pares duplicados encontrados : {n_pairs}")
    print(f"  {tag}Relaciones redirigidas       : {n_redirected}")
    print(f"  {tag}Pares con similitud insuficiente : {n_no_evidence}  (dado el umbral que aplica según haya fecha o no)")
    print(f"  {tag}Pares con fechas lejanas     : {n_dates_far}  (ambas fechas conocidas, fuera de ventana)")
    print(f"  {tag}Pares con conflicto geográfico : {n_geo_conflict}  (DD-041 — sim+fecha ok, países no coinciden)")
    print(f"  {tag}Total descartados sin fusión : {n_no_evidence + n_dates_far + n_geo_conflict}")

    if summary:
        print_event_summary()

    driver.close()
    print("\n✅ Resolución de eventos completa.")


if __name__ == "__main__":
    app()
