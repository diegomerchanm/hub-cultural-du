"""
Verificación manual de 4_enrich_events_extract.py — NO forma parte del pipeline.
Muestra ejemplos reales de eventos ya escritos en Neo4j para revisión humana,
y señala casos frontera dignos de mirar (fechas fuera de rango razonable,
eventos sin ubicación, eventos con postCount alto — posible dedup exitoso).

Uso:
    python verify_events_extraction.py
"""
import os
from datetime import datetime

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
)

SAMPLE_SIZE = 10
# Rango razonable para eventDate: el proyecto cubre diáspora colombiana/latam
# en Francia, no tiene sentido un evento antes de 1950 o después de 2028.
DATE_MIN = "1950-01-01"
DATE_MAX = "2028-01-01"


def show_overview():
    print("=" * 70)
    print("RESUMEN GENERAL")
    print("=" * 70)
    with driver.session() as session:
        total = session.run("MATCH (e:Event) RETURN count(e) AS n").single()["n"]
        by_cat = session.run("""
            MATCH (e:Event)
            RETURN e.category AS cat, count(e) AS n
            ORDER BY n DESC
        """).data()
        invitation_true = session.run("""
            MATCH (e:Event) WHERE e.isPublicInvitation = true AND e.isUpcoming = true
            RETURN count(e) AS n
        """).single()["n"]
        merged = session.run("""
            MATCH (e:Event) WHERE e.postCount > 1
            RETURN count(e) AS n
        """).single()["n"]
        no_loc = session.run("""
            MATCH (e:Event) WHERE e.locationName IS NULL OR e.locationName = ''
            RETURN count(e) AS n
        """).single()["n"]

    print(f"  Total eventos: {total}")
    print(f"  Con is_public_invitation=True AND is_upcoming=True: {invitation_true}")
    print(f"  Con postCount > 1 (dedup fusionó >=2 posts): {merged}")
    print(f"  Sin locationName: {no_loc}")
    print("\n  Por categoría:")
    for r in by_cat:
        print(f"    {r['n']:>4}  {r['cat']}")


def show_random_sample():
    print("\n" + "=" * 70)
    print(f"MUESTRA ALEATORIA ({SAMPLE_SIZE} eventos)")
    print("=" * 70)
    with driver.session() as session:
        rows = session.run("""
            MATCH (e:Event)
            RETURN e.id AS id, e.title AS title, e.category AS category,
                   e.eventDate AS eventDate, e.locationName AS loc,
                   e.eventScore AS score, e.postCount AS postCount,
                   e.isPublicInvitation AS invitation, e.isUpcoming AS upcoming,
                   e.description AS description, e.sourceAuthor AS author
            ORDER BY rand() LIMIT $n
        """, n=SAMPLE_SIZE).data()
    for r in rows:
        print(f"\n  [{r['id']}] {r['title']}  (cat={r['category']})")
        print(f"    autor origen : @{r['author']}")
        print(f"    fecha        : {r['eventDate'] or '-'}   lugar: {r['loc'] or '-'}")
        print(f"    score        : {r['score']:.3f}   postCount: {r['postCount']}")
        print(f"    invitación   : {r['invitation']}   próximo: {r['upcoming']}")
        print(f"    descripción  : {(r['description'] or '')[:150]}")


def show_suspicious_dates():
    print("\n" + "=" * 70)
    print(f"FECHAS SOSPECHOSAS (fuera de [{DATE_MIN}, {DATE_MAX}])")
    print("=" * 70)
    with driver.session() as session:
        rows = session.run("""
            MATCH (e:Event)
            WHERE e.eventDate IS NOT NULL AND e.eventDate <> ''
              AND (e.eventDate < $dmin OR e.eventDate > $dmax)
            RETURN e.id AS id, e.title AS title, e.eventDate AS eventDate,
                   e.sourceAuthor AS author, e.description AS description
            ORDER BY e.eventDate
        """, dmin=DATE_MIN, dmax=DATE_MAX).data()
    print(f"  Total: {len(rows)}")
    for r in rows:
        print(f"    {r['eventDate']}  [{r['id']}] @{r['author']} — {r['title']}")
        print(f"      {(r['description'] or '')[:120]}")


def show_merged_events():
    """Eventos donde el dedup fusionó 2+ posts — para confirmar que la
    deduplicación por similitud coseno + ventana de fecha realmente está
    juntando posts que hablan del mismo evento, no cosas distintas."""
    print("\n" + "=" * 70)
    print("EVENTOS CON postCount > 1 (dedup fusionó varios posts)")
    print("=" * 70)
    with driver.session() as session:
        rows = session.run("""
            MATCH (e:Event) WHERE e.postCount > 1
            RETURN e.id AS id, e.title AS title, e.postCount AS postCount,
                   e.eventDate AS eventDate, e.locationName AS loc
            ORDER BY e.postCount DESC LIMIT 15
        """).data()
    print(f"  Total eventos fusionados: {len(rows)}")
    for r in rows:
        print(f"    postCount={r['postCount']:<3} [{r['id']}] {r['title']}  "
              f"date={r['eventDate'] or '-'}  loc={r['loc'] or '-'}")


def show_groq_quality_sample(n: int = 15):
    """Muestra solo eventos con datos de Capa 3 (sourceAuthor IS NOT NULL,
    o sea creados/enriquecidos por Groq), con el caption original al lado
    de la descripción/veredicto del LLM, para juzgar calidad manualmente.
    Sin esto, una muestra aleatoria simple sale dominada por los 486
    eventos del script anterior (sin Capa 3) y no dice nada sobre Groq.
    """
    print("\n" + "=" * 70)
    print(f"CALIDAD GROQ — muestra de eventos CON Capa 3 (N={n})")
    print("=" * 70)
    with driver.session() as session:
        total_groq = session.run(
            "MATCH (e:Event) WHERE e.sourceAuthor IS NOT NULL RETURN count(e) AS n"
        ).single()["n"]
        rows = session.run("""
            MATCH (p:Post)-[:MENTIONS_EVENT]->(e:Event)
            WHERE e.sourceAuthor IS NOT NULL
            WITH e, p ORDER BY rand() LIMIT $n
            RETURN e.id AS id, e.category AS category, e.eventDate AS eventDate,
                   e.locationName AS loc, e.isPublicInvitation AS invitation,
                   e.isUpcoming AS upcoming, e.description AS description,
                   e.llmReasoning AS reasoning, e.sourceAuthor AS author,
                   p.caption AS caption
        """, n=n).data()
    print(f"  Total eventos con Capa 3: {total_groq}")
    for r in rows:
        print(f"\n  [{r['id']}] cat={r['category']}  date={r['eventDate'] or '-'}  loc={r['loc'] or '-'}")
        print(f"    @{r['author']}")
        print(f"    invitación={r['invitation']}  próximo={r['upcoming']}")
        print(f"    razonamiento LLM : {(r['reasoning'] or '')[:180]}")
        print(f"    descripción LLM  : {(r['description'] or '')[:180]}")
        print(f"    caption original : {(r['caption'] or '')[:200].strip()}")


if __name__ == "__main__":
    driver.verify_connectivity()
    print("Conexión Neo4j OK\n")
    show_overview()
    show_random_sample()
    show_suspicious_dates()
    show_merged_events()
    show_groq_quality_sample()
    driver.close()
