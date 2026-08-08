"""
Diagnóstico puntual v2: cobertura del geotag de Instagram y resultado de
cityName/exactAddress sobre eventos ya procesados (sourceAuthor IS NOT NULL
como proxy, ya que locationCapa3 todavía no se ha poblado en ninguna corrida
real). Uso único — no forma parte del pipeline.

Uso:
    python check_geotag_coverage.py
"""
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
)

with driver.session() as s:
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    r = s.run("""
        MATCH (p:Post)-[:MENTIONS_EVENT]->(e:Event)
        WITH DISTINCT p
        RETURN count(p) AS total_posts,
               count(CASE WHEN exists((p)-[:TAGGED_AT]->()) THEN 1 END) AS con_geotag
    """).single()
    print(f"Posts vinculados a eventos: {r['total_posts']}  |  con geotag TAGGED_AT: {r['con_geotag']}")

    r3 = s.run("""
        MATCH (e:Event)
        WHERE e.sourceAuthor IS NOT NULL
        RETURN count(e) AS total,
               count(CASE WHEN e.cityName IS NOT NULL AND e.cityName <> '' THEN 1 END) AS con_ciudad,
               count(CASE WHEN e.exactAddress IS NOT NULL AND e.exactAddress <> '' THEN 1 END) AS con_direccion,
               count(CASE WHEN (e.cityName IS NULL OR e.cityName='') AND (e.exactAddress IS NULL OR e.exactAddress='') THEN 1 END) AS sin_nada
    """).single()
    print(f"\nEventos con sourceAuthor definido (ya pasaron por Capa 3 alguna vez): {r3['total']}")
    print(f"  con ciudad: {r3['con_ciudad']}  |  con dirección exacta: {r3['con_direccion']}  |  sin ninguna: {r3['sin_nada']}")

    # Cuántos se podrían marcar como resueltos de una vez (ya tienen ciudad
    # o dirección real) sin necesidad de reprocesar — candidatos a migración
    # retroactiva de locationCapa3.
    r4 = s.run("""
        MATCH (e:Event)
        WHERE e.sourceAuthor IS NOT NULL
          AND ((e.cityName IS NOT NULL AND e.cityName <> '') OR (e.exactAddress IS NOT NULL AND e.exactAddress <> ''))
        RETURN count(e) AS n
    """).single()
    print(f"\nCandidatos seguros a marcar locationCapa3=true sin reprocesar (ya tienen ciudad o dirección real): {r4['n']}")

    sample = s.run("""
        MATCH (e:Event)
        WHERE e.exactAddress IS NOT NULL AND e.exactAddress <> ''
        RETURN e.id AS id, e.cityName AS city, e.exactAddress AS address
        ORDER BY e.id
        LIMIT 15
    """).data()
    print("\nMuestra de dirección exacta ya resuelta:")
    for row in sample:
        print(f"  [{row['id']}] ciudad={row['city']!r}  dirección={row['address']!r}")

driver.close()
