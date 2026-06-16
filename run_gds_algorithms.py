import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Conexión exitosa a Neo4j Aura\n")

GRAPH_NAME = "red-cultural"

# ── 2. Cuentas políticas a penalizar ─────────────────────────────────────────
POLITICAL_ACCOUNTS = [
    "gustavopetrourrego",
    "infopresidencia",
    "registraduria",
    "cancilleriacol",
    "alfonso_prada",
    "estoescambio",
    "colombianosune",
    "minigualdadcol",
    "procolombiaco",
    "embajadacolfra",
    "embsuizacolombia",
    "embcolghana",
]

# ── 3. Marcar cuentas políticas ANTES de proyectar ───────────────────────────
def mark_political_accounts(tx):
    for username in POLITICAL_ACCOUNTS:
        tx.run("""
            MATCH (a:Account {username: $username})
            SET a:Political, a.politicalWeight = 0.1
        """, username=username)
    print(f"  🏛️  {len(POLITICAL_ACCOUNTS)} cuentas marcadas como :Political")

# ── 4. Detección semi-automática por hashtags políticos ──────────────────────
def detect_political_by_hashtags(tx):
    result = tx.run("""
        MATCH (a:Account)-[:PUBLISHED]->(p:Post)-[:HAS_HASHTAG]->(h:Hashtag)
        WHERE toLower(h.name) IN [
            'elecciones2026', 'voto', 'petro', 'gobierno',
            'colombia2026', 'presidencia', 'elecciones',
            'votaciones', 'democracia', 'congreso'
        ]
        WITH a, count(p) AS postsPoliticos
        WHERE postsPoliticos >= 2
        SET a.politicalScore = postsPoliticos
        RETURN a.username AS username, postsPoliticos
        ORDER BY postsPoliticos DESC
    """)
    rows = result.data()
    print(f"\n  📊 {len(rows)} cuentas detectadas con contenido político:")
    for r in rows:
        print(f"     @{r['username']} → {r['postsPoliticos']} posts políticos")
    return rows

# ── 5. Proyectar grafo en memoria ─────────────────────────────────────────────
def project_graph(session):
    # Eliminar si ya existe
    exists = session.run("""
        CALL gds.graph.exists($name) YIELD exists
    """, name=GRAPH_NAME).single()["exists"]

    if exists:
        session.run("CALL gds.graph.drop($name)", name=GRAPH_NAME)
        print(f"  🗑️  Grafo anterior '{GRAPH_NAME}' eliminado")

    result = session.run("""
        CALL gds.graph.project(
            $name,
            {
                Account: {
                    properties: ['followersCount', 'politicalWeight', 'politicalScore']
                }
            },
            {
                MENTIONS:      {orientation: 'NATURAL'},
                TAGS_USER:     {orientation: 'NATURAL'},
                RELATED_TO:    {orientation: 'NATURAL'},
                COAUTHORED_BY: {orientation: 'NATURAL'}
            }
        )
        YIELD graphName, nodeCount, relationshipCount
    """, name=GRAPH_NAME).single()

    print(f"\n  ✅ Grafo proyectado: '{result['graphName']}'")
    print(f"     Nodos      : {result['nodeCount']:,}")
    print(f"     Relaciones : {result['relationshipCount']:,}")

# ── 6. Degree Centrality ──────────────────────────────────────────────────────
def run_degree(session):
    print("\n📐 Degree Centrality...")
    session.run("""
        CALL gds.degree.write($name, {
            writeProperty: 'degreeCentrality',
            orientation: 'REVERSE'
        })
    """, name=GRAPH_NAME)

    # Top 15
    result = session.run("""
        MATCH (a:Account)
        WHERE a.degreeCentrality IS NOT NULL
        AND NOT a:Political
        RETURN a.username AS username,
               a.fullName AS fullName,
               a.followersCount AS followers,
               a.degreeCentrality AS degree
        ORDER BY degree DESC
        LIMIT 15
    """)
    print("\n  🏆 Top 15 por Degree (excluyendo políticos):")
    print(f"  {'Username':<35} {'Followers':>10} {'Degree':>8}")
    print(f"  {'-'*55}")
    for r in result:
        print(f"  @{r['username']:<34} {r['followers'] or 0:>10,} {r['degree']:>8.1f}")

# ── 7. PageRank ───────────────────────────────────────────────────────────────
def run_pagerank(session):
    print("\n📊 PageRank...")
    session.run("""
        CALL gds.pageRank.write($name, {
            writeProperty: 'pageRankScore',
            maxIterations: 20,
            dampingFactor: 0.85
        })
    """, name=GRAPH_NAME)

    result = session.run("""
        MATCH (a:Account)
        WHERE a.pageRankScore IS NOT NULL
        AND NOT a:Political
        RETURN a.username AS username,
               a.fullName AS fullName,
               a.followersCount AS followers,
               round(a.pageRankScore, 4) AS pageRank
        ORDER BY pageRank DESC
        LIMIT 15
    """)
    print("\n  🏆 Top 15 por PageRank (excluyendo políticos):")
    print(f"  {'Username':<35} {'Followers':>10} {'PageRank':>10}")
    print(f"  {'-'*57}")
    for r in result:
        print(f"  @{r['username']:<34} {r['followers'] or 0:>10,} {r['pageRank']:>10.4f}")

# ── 8. Leiden Community Detection ────────────────────────────────────────────
def run_leiden(session):
    print("\n🔵 Leiden Community Detection...")
    session.run("""
        CALL gds.leiden.write($name, {
            writeProperty: 'communityId',
            maxLevels: 10,
            gamma: 1.0,
            theta: 0.01
        })
    """, name=GRAPH_NAME)

    result = session.run("""
        MATCH (a:Account)
        WHERE a.communityId IS NOT NULL
        WITH a.communityId AS community, count(*) AS size,
             collect(a.username)[0..5] AS members
        ORDER BY size DESC
        LIMIT 10
        RETURN community, size, members
    """)
    print("\n  🏘️  Top 10 comunidades detectadas:")
    print(f"  {'Community ID':<15} {'Tamaño':>8}  Miembros (muestra)")
    print(f"  {'-'*70}")
    for r in result:
        members = ', '.join([f"@{m}" for m in r['members']])
        print(f"  {r['community']:<15} {r['size']:>8}  {members}")

# ── 9. Betweenness Centrality ────────────────────────────────────────────────
def run_betweenness(session):
    print("\n🌉 Betweenness Centrality...")
    session.run("""
        CALL gds.betweenness.write($name, {
            writeProperty: 'betweennessScore',
            samplingSize: 100
        })
    """, name=GRAPH_NAME)

    result = session.run("""
        MATCH (a:Account)
        WHERE a.betweennessScore IS NOT NULL
        AND NOT a:Political
        RETURN a.username AS username,
               a.followersCount AS followers,
               round(a.pageRankScore, 4) AS pageRank,
               round(a.betweennessScore, 2) AS betweenness
        ORDER BY betweenness DESC
        LIMIT 15
    """)
    print("\n  🏆 Top 15 Brokers — alto betweenness (excluyendo políticos):")
    print(f"  {'Username':<35} {'Followers':>10} {'PageRank':>10} {'Betweenness':>12}")
    print(f"  {'-'*70}")
    for r in result:
        print(f"  @{r['username']:<34} {r['followers'] or 0:>10,} {r['pageRank'] or 0:>10.4f} {r['betweenness']:>12.2f}")

# ── 10. Score de relevancia cultural compuesto ───────────────────────────────
def compute_cultural_relevance(session):
    print("\n⭐ Calculando score de relevancia cultural...")
    session.run("""
        MATCH (a:Account:Public)
        WHERE a.pageRankScore IS NOT NULL
        WITH a,
            // Normalización simple entre 0-1 por propiedad
            coalesce(a.pageRankScore, 0)     AS pr,
            coalesce(a.degreeCentrality, 0)  AS deg,
            coalesce(a.betweennessScore, 0)  AS bet,
            coalesce(a.followersCount, 0)    AS fol,
            // Penalización política
            CASE WHEN a:Political OR coalesce(a.politicalScore, 0) > 3
                 THEN 0.1 ELSE 1.0 END AS politicalPenalty
        SET a.culturalRelevanceScore = 
            politicalPenalty * (
                (pr  * 0.35) +
                (deg * 0.25) +
                (bet * 0.20) +
                (log(fol + 1) * 0.20)
            )
    """)

    result = session.run("""
        MATCH (a:Account:Public)
        WHERE a.culturalRelevanceScore IS NOT NULL
        AND NOT a:Political
        RETURN a.username AS username,
               a.fullName AS fullName,
               a.followersCount AS followers,
               a.communityId AS community,
               round(a.culturalRelevanceScore, 6) AS score
        ORDER BY score DESC
        LIMIT 20
    """)
    print("\n  🌟 Top 20 — Relevancia Cultural (score compuesto):")
    print(f"  {'Username':<35} {'Followers':>10} {'Community':>10} {'Score':>12}")
    print(f"  {'-'*72}")
    for r in result:
        print(f"  @{r['username']:<34} {r['followers'] or 0:>10,} {r['community'] or '?':>10} {r['score']:>12.6f}")

# ── 11. Main ──────────────────────────────────────────────────────────────────
def main():
    with driver.session() as session:

        # Fase 4: Limpieza política
        print("🧹 Fase 4 — Limpieza & Filtro Político")
        print("="*55)
        session.execute_write(mark_political_accounts)
        session.execute_write(detect_political_by_hashtags)

        # Fase 3: Proyección y algoritmos
        print("\n\n🔬 Fase 3 — Algoritmos GDS")
        print("="*55)
        project_graph(session)
        run_degree(session)
        run_pagerank(session)
        run_leiden(session)
        run_betweenness(session)
        compute_cultural_relevance(session)

    driver.close()
    print("\n\n✅ Pipeline completo. Scores almacenados en Neo4j.")
    print("   Propiedades escritas por nodo:")
    print("   · degreeCentrality")
    print("   · pageRankScore")
    print("   · communityId")
    print("   · betweennessScore")
    print("   · culturalRelevanceScore")

if __name__ == "__main__":
    main()