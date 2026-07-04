"""
Análisis de red LOCAL con NetworkX / python-igraph / leidenalg.

Motivación: el puerto 7687 (Bolt) está bloqueado en redes corporativas y
GDS no está disponible en AuraDB estándar. Con ~7k nodos el análisis local
es además superior para la mémoire: betweenness EXACTO (GDS usaba
samplingSize=100), semillas fijas (reproducible) y métricas que GDS no
tiene (E-I index, participation coefficient).

Red MULTIPLEX — dos capas tratadas por separado:
  · social      : MENTIONS / TAGS_USER / COAUTHORED_BY, proyectadas
                  Account→Account vía (autor)-[:PUBLISHED]->(post)-[rel]->(target).
                  (Nota: en el grafo crudo esas relaciones salen del Post,
                  NO de la cuenta — la proyección GDS anterior con solo
                  nodos Account únicamente veía RELATED_TO.)
  · algorithmic : RELATED_TO (recomendador de Instagram, no comportamiento
                  social observado — se analiza aparte, no se mezcla).

Uso (cada paso es independiente — `analyze` funciona OFFLINE desde CSV):
  python run_network_analysis.py export      # Neo4j → data_processed/*.csv
  python run_network_analysis.py analyze     # CSV → métricas → CSV
  python run_network_analysis.py writeback   # métricas → Neo4j (UNWIND batch)
  python run_network_analysis.py run-all     # los tres pasos en orden

Métricas por capa:
  PageRank exacto · Betweenness exacto (igraph, C) · Leiden multi-resolución
  (γ=0.5/1.0/1.5, seed fija) · WCC · k-core · Participation coefficient
  (Guimerà & Amaral 2005) · E-I Index (Krackhardt & Stern 1988) por tipo
  de actor y por comunidad.

Tipos de actor: heurística por username/fullName (ver ACTOR_RULES),
sobreescribible con data_processed/actor_types.csv (columnas:
username,actor_type) para la tipología curada ex ante de la mémoire.
"""

import math
import os
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import typer
from dotenv import load_dotenv

# ── Configuración ─────────────────────────────────────────────────────────────
OUT_DIR      = Path("data_processed")
SEED         = 42
GAMMAS       = [0.5, 1.0, 1.5]
SOCIAL_RELS  = ["MENTIONS", "TAGS_USER", "COAUTHORED_BY"]
LAYERS       = {
    "social":      {"edges_csv": "edges_social.csv",      "suffix": ""},
    "algorithmic": {"edges_csv": "edges_algorithmic.csv", "suffix": "Algo"},
}
NODES_CSV    = "nodes.csv"
ACTOR_TYPES_CSV = "actor_types.csv"   # override manual opcional

# Heurística provisional de tipología de actores (curar en actor_types.csv)
ACTOR_RULES = [
    ("institucional_estatal",
     r"consulado|embajada|emb[a-z]*col|cancilleria|presidencia|registraduria"
     r"|ministerio|gobierno|procolombia|infopresidencia|aecid|campusfrance"),
    ("institucional_cultural",
     r"alianzafrancesa|alianza_francesa|institut|if_colomb|cultur|museo"
     r"|galer[ií]a|teatro|filarm|biblioteca"),
    ("medio", r"radio|tv[^a-z]|noticias|revista|magazine|press|diario"),
    ("comercial", r"restaurant|resto|caf[eé]|tienda|food|gastro|market|shop|bar[^a-z]?"),
]
DEFAULT_ACTOR = "individual"

app = typer.Typer(add_completion=False, help="Análisis de red local (multiplex).")


# ── Neo4j (lazy: solo export/writeback lo necesitan) ─────────────────────────
def get_driver():
    from neo4j import GraphDatabase
    load_dotenv()
    uri, user, pwd = (os.getenv("NEO4J_URI"), os.getenv("NEO4J_USERNAME"),
                      os.getenv("NEO4J_PASSWORD"))
    if not all([uri, user, pwd]):
        raise ValueError("Credenciales Neo4j ausentes en .env")
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK")
    return driver


# ── 1. EXPORT ─────────────────────────────────────────────────────────────────
@app.command()
def export():
    """Exporta nodos y aristas (por capa) de Neo4j a data_processed/*.csv."""
    OUT_DIR.mkdir(exist_ok=True)
    driver = get_driver()

    with driver.session() as session:
        print("\n📤 Exportando nodos...")
        nodes = session.run("""
            MATCH (a:Account)
            RETURN a.username                     AS username,
                   coalesce(a.fullName, '')       AS fullName,
                   coalesce(a.followersCount, 0)  AS followers,
                   (a:Political)                  AS political,
                   (a:Public)                     AS public,
                   coalesce(a.politicalScore, 0)  AS politicalScore
        """).data()
        pd.DataFrame(nodes).to_csv(OUT_DIR / NODES_CSV, index=False)
        print(f"  ✅ {len(nodes):,} nodos → {OUT_DIR / NODES_CSV}")

        # Capa social: proyección Account→Account vía posts/IGTV del autor.
        print("\n📤 Exportando capa SOCIAL (autor→post→target)...")
        social = session.run("""
            MATCH (a:Account)-[:PUBLISHED]->(p)-[r:MENTIONS|TAGS_USER|COAUTHORED_BY]->(b:Account)
            WHERE a.username <> b.username
            RETURN a.username AS source, b.username AS target,
                   type(r)    AS relType, count(*) AS weight
        """).data()
        pd.DataFrame(social).to_csv(OUT_DIR / LAYERS["social"]["edges_csv"], index=False)
        print(f"  ✅ {len(social):,} aristas → {OUT_DIR / LAYERS['social']['edges_csv']}")

        print("\n📤 Exportando capa ALGORITHMIC (RELATED_TO)...")
        algo = session.run("""
            MATCH (a:Account)-[:RELATED_TO]->(b:Account)
            WHERE a.username <> b.username
            RETURN a.username AS source, b.username AS target,
                   'RELATED_TO' AS relType, count(*) AS weight
        """).data()
        pd.DataFrame(algo).to_csv(OUT_DIR / LAYERS["algorithmic"]["edges_csv"], index=False)
        print(f"  ✅ {len(algo):,} aristas → {OUT_DIR / LAYERS['algorithmic']['edges_csv']}")

    driver.close()
    print("\n✅ Export completo. `analyze` ya puede correr OFFLINE.")


# ── 2. Helpers de análisis ────────────────────────────────────────────────────
def assign_actor_types(nodes_df: pd.DataFrame) -> pd.Series:
    """Tipología de actor: override manual > flag :Political > heurística regex."""
    override = {}
    override_path = OUT_DIR / ACTOR_TYPES_CSV
    if override_path.exists():
        odf = pd.read_csv(override_path)
        override = dict(zip(odf["username"], odf["actor_type"]))
        print(f"  📋 Override manual: {len(override)} cuentas desde {override_path}")

    def classify(row):
        if row["username"] in override:
            return override[row["username"]]
        if bool(row.get("political")):
            return "institucional_estatal"
        text = f"{row['username']} {row['fullName']}".lower()
        for actor_type, pattern in ACTOR_RULES:
            if re.search(pattern, text):
                return actor_type
        return DEFAULT_ACTOR

    return nodes_df.apply(classify, axis=1)


def build_igraph(edges_df: pd.DataFrame, directed: bool = True):
    """Grafo igraph con pesos agregados (colapsa multi-aristas por tipo)."""
    import igraph as ig
    agg = edges_df.groupby(["source", "target"], as_index=False)["weight"].sum()
    names = sorted(set(agg["source"]) | set(agg["target"]))
    idx = {u: i for i, u in enumerate(names)}
    g = ig.Graph(
        n=len(names),
        edges=[(idx[s], idx[t]) for s, t in zip(agg["source"], agg["target"])],
        directed=directed,
    )
    g.vs["name"] = names
    g.es["weight"] = agg["weight"].tolist()
    return g


def participation_coefficient(g_und, membership: list) -> list:
    """Guimerà & Amaral (2005): P_i = 1 − Σ_s (k_is / k_i)².

    k_is = fuerza (suma de pesos) del nodo i hacia la comunidad s.
    P≈0: conexiones concentradas en su comunidad (hub provincial).
    P→1: conexiones repartidas entre comunidades (conector/broker).
    """
    P = []
    for v in range(g_und.vcount()):
        k_i = 0.0
        k_is = defaultdict(float)
        for e in g_und.incident(v, mode="ALL"):
            edge = g_und.es[e]
            w = edge["weight"]
            other = edge.target if edge.source == v else edge.source
            k_i += w
            k_is[membership[other]] += w
        P.append(0.0 if k_i == 0 else 1.0 - sum((k / k_i) ** 2 for k in k_is.values()))
    return P


def ei_index(g_und, groups: list) -> tuple[float, dict]:
    """Krackhardt & Stern (1988): (E − I) / (E + I), ponderado por peso.

    +1 = todas las aristas externas al grupo (apertura total)
    −1 = todas internas (enclaustramiento). Global y por grupo.
    """
    E_by = defaultdict(float)
    I_by = defaultdict(float)
    E_tot = I_tot = 0.0
    for edge in g_und.es:
        w  = edge["weight"]
        gs = groups[edge.source]
        gt = groups[edge.target]
        if gs == gt:
            I_tot += w
            I_by[gs] += w
        else:
            E_tot += w
            E_by[gs] += w
            E_by[gt] += w
    glob = (E_tot - I_tot) / (E_tot + I_tot) if (E_tot + I_tot) else 0.0
    per_group = {}
    for grp in set(groups):
        e, i = E_by.get(grp, 0.0), I_by.get(grp, 0.0)
        per_group[grp] = (e - i) / (e + i) if (e + i) else 0.0
    return glob, per_group


def analyze_layer(layer: str, edges_df: pd.DataFrame, nodes_df: pd.DataFrame) -> pd.DataFrame:
    """Corre todas las métricas sobre una capa; devuelve DataFrame por nodo."""
    import igraph as ig
    import leidenalg as la

    print(f"\n{'═'*60}\n  🔬 CAPA: {layer.upper()}\n{'═'*60}")
    if edges_df.empty:
        print("  ⚠️  Capa vacía — omitida.")
        return pd.DataFrame()

    g = build_igraph(edges_df, directed=True)
    print(f"  Grafo: {g.vcount():,} nodos · {g.ecount():,} aristas (dirigido, ponderado)")

    # WCC — sanity check de fragmentación, ANTES de interpretar centralidades
    comps = g.connected_components(mode="weak")
    sizes = sorted(comps.sizes(), reverse=True)
    print(f"  🧩 WCC: {len(sizes)} componentes · gigante = {sizes[0]:,} nodos "
          f"({sizes[0]/g.vcount():.1%} del total)")
    wcc_id = comps.membership

    # PageRank EXACTO (dirigido, ponderado)
    pagerank = g.pagerank(damping=0.85, weights="weight")

    # Betweenness EXACTO — igraph en C (nx puro tardaría minutos)
    print("  🌉 Betweenness exacto...")
    betweenness = g.betweenness(directed=True)

    # Grafo no dirigido colapsado para Leiden / k-core / P / E-I
    g_und = g.as_undirected(combine_edges={"weight": "sum"})
    g_und.simplify(combine_edges={"weight": "sum"})

    # Leiden multi-resolución con semilla fija (reproducible)
    leiden_cols = {}
    for gamma in GAMMAS:
        part = la.find_partition(
            g_und, la.RBConfigurationVertexPartition,
            weights="weight", resolution_parameter=gamma, seed=SEED,
        )
        leiden_cols[gamma] = part.membership
        print(f"  🔵 Leiden γ={gamma}: {len(part)} comunidades · "
              f"modularidad={g_und.modularity(part.membership, weights='weight'):.4f}")

    # k-core (sobre grafo simple no dirigido)
    kcore = g_und.coreness(mode="all")

    # Participation coefficient con la partición γ=1.0
    print("  🔀 Participation coefficient (γ=1.0)...")
    part_coef = participation_coefficient(g_und, leiden_cols[1.0])

    df = pd.DataFrame({
        "username":       g.vs["name"],
        "pageRank":       pagerank,
        "betweenness":    betweenness,
        "wccId":          wcc_id,
        "kCore":          kcore,
        "participation":  part_coef,
        **{f"leidenG{str(gm).replace('.', '')}": leiden_cols[gm] for gm in GAMMAS},
    })

    # E-I Index por tipo de actor y por comunidad (γ=1.0)
    meta = nodes_df.set_index("username")
    actor_types = [
        meta["actorType"].get(u, DEFAULT_ACTOR) for u in g.vs["name"]
    ]
    df["actorType"] = actor_types

    ei_global_actor, ei_by_actor = ei_index(g_und, actor_types)
    ei_global_comm, ei_by_comm   = ei_index(g_und, leiden_cols[1.0])

    print(f"\n  🌐 E-I Index ({layer}) por TIPO DE ACTOR — global: {ei_global_actor:+.4f}")
    for grp, val in sorted(ei_by_actor.items(), key=lambda x: x[1]):
        n_grp = actor_types.count(grp)
        print(f"     {grp:<24} {val:+.4f}  (n={n_grp})")
    print(f"  🌐 E-I Index ({layer}) por COMUNIDAD γ=1.0 — global: {ei_global_comm:+.4f}")

    ei_rows = (
        [{"layer": layer, "grouping": "actorType", "group": g_, "ei": v,
          "ei_global": ei_global_actor} for g_, v in ei_by_actor.items()]
        + [{"layer": layer, "grouping": "leidenG10", "group": g_, "ei": v,
            "ei_global": ei_global_comm} for g_, v in ei_by_comm.items()]
    )
    pd.DataFrame(ei_rows).to_csv(OUT_DIR / f"ei_index_{layer}.csv", index=False)

    # Top-10 conectores: participation alto + betweenness alto
    top = df.sort_values(["participation", "betweenness"], ascending=False).head(10)
    print(f"\n  🏆 Top 10 conectores entre comunidades ({layer}):")
    print(f"  {'Username':<32} {'P':>6} {'Betw.':>10} {'kCore':>6} {'Tipo':<22}")
    for _, r in top.iterrows():
        print(f"  @{r['username']:<31} {r['participation']:>6.3f} "
              f"{r['betweenness']:>10.1f} {r['kCore']:>6} {r['actorType']:<22}")

    return df


# ── 3. ANALYZE ────────────────────────────────────────────────────────────────
@app.command()
def analyze():
    """Corre todas las métricas por capa desde los CSV (100% offline)."""
    nodes_path = OUT_DIR / NODES_CSV
    if not nodes_path.exists():
        raise typer.Exit(f"Falta {nodes_path} — corre primero `export`.")

    nodes_df = pd.read_csv(nodes_path).fillna({"fullName": ""})
    nodes_df["actorType"] = assign_actor_types(nodes_df)
    dist = nodes_df["actorType"].value_counts()
    print("  👥 Tipos de actor:", ", ".join(f"{k}={v}" for k, v in dist.items()))

    for layer, cfg in LAYERS.items():
        edges_path = OUT_DIR / cfg["edges_csv"]
        if not edges_path.exists():
            print(f"  ⚠️  {edges_path} no existe — capa '{layer}' omitida.")
            continue
        df = analyze_layer(layer, pd.read_csv(edges_path), nodes_df)
        if not df.empty:
            out = OUT_DIR / f"metrics_{layer}.csv"
            df.to_csv(out, index=False)
            print(f"\n  💾 Métricas → {out}")

    print("\n✅ Análisis completo. `writeback` para subir a Neo4j cuando haya red.")


# ── 4. WRITEBACK ──────────────────────────────────────────────────────────────
# Mapeo columna CSV → propiedad Neo4j (la capa social usa nombres base;
# la algorítmica lleva sufijo 'Algo' para no pisarla — red multiplex).
WRITE_PROPS = ["pageRank", "betweenness", "wccId", "kCore", "participation",
               "leidenG05", "leidenG10", "leidenG15", "actorType"]
PROP_RENAME = {"pageRank": "pageRankExact", "betweenness": "betweennessExact",
               "participation": "participationCoef"}


@app.command()
def writeback(batch: int = typer.Option(500, help="Filas por lote UNWIND.")):
    """Escribe las métricas locales de vuelta a Neo4j en batches UNWIND."""
    driver = get_driver()
    with driver.session() as session:
        for layer, cfg in LAYERS.items():
            path = OUT_DIR / f"metrics_{layer}.csv"
            if not path.exists():
                print(f"  ⚠️  {path} no existe — capa '{layer}' omitida.")
                continue
            df = pd.read_csv(path)
            suffix = cfg["suffix"]
            rows = []
            for _, r in df.iterrows():
                props = {}
                for col in WRITE_PROPS:
                    if col not in df.columns:
                        continue
                    prop = PROP_RENAME.get(col, col) + suffix
                    val = r[col]
                    props[prop] = val.item() if hasattr(val, "item") else val
                rows.append({"username": r["username"], "props": props})

            print(f"\n📥 Writeback capa '{layer}': {len(rows):,} nodos "
                  f"(lotes de {batch})...")
            for start in range(0, len(rows), batch):
                session.run("""
                    UNWIND $rows AS row
                    MATCH (a:Account {username: row.username})
                    SET a += row.props
                """, rows=rows[start:start + batch])
            print(f"  ✅ Capa '{layer}' escrita.")
    driver.close()
    print("\n✅ Writeback completo.")


# ── 5. RUN-ALL ────────────────────────────────────────────────────────────────
@app.command(name="run-all")
def run_all():
    """export → analyze → writeback."""
    export()
    analyze()
    writeback()


if __name__ == "__main__":
    app()
