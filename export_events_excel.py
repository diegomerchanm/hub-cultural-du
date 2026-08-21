"""
export_events_excel.py — Hub Cultural DU
Exporta todos los :Event a un .xlsx para análisis manual fuera de Neo4j.
Uso puntual/ad-hoc — no forma parte del pipeline.

Uso:
    python export_events_excel.py
    python export_events_excel.py --out mis_eventos.xlsx
    python export_events_excel.py --include-rejected
    python export_events_excel.py --include-pending
"""
import os

import pandas as pd
import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
app = typer.Typer(add_completion=False)

QUERY_BASE = """
    MATCH (e:Event)
    {where}
    RETURN e.id AS id, e.title AS titulo, e.category AS categoria,
           e.eventDate AS fecha, e.cityName AS ciudad, e.exactAddress AS direccion,
           e.locationName AS ubicacion_texto, e.description AS descripcion,
           e.isPublicInvitation AS es_invitacion_publica, e.isUpcoming AS es_proximo,
           e.priceRange AS rango_precio, e.hotnessScore AS hotness, e.eventScore AS score,
           e.sourceAuthor AS autor, e.sourcePostUrl AS url_post,
           e.sourcePostDate AS fecha_post, e.locationCapa3 AS capa3_ejecutada,
           e.llmReasoning AS razonamiento_llm, labels(e) AS etiquetas
    ORDER BY e.eventDate DESC
"""


@app.command()
def main(
    out: str = typer.Option("eventos_export.xlsx", "--out", help="Nombre del archivo .xlsx de salida."),
    include_rejected: bool = typer.Option(
        False, "--include-rejected",
        help="Incluir eventos marcados :Rejected (excluidos por defecto, igual que el dashboard).",
    ),
    include_pending: bool = typer.Option(
        False, "--include-pending",
        help="Incluir eventos marcados :PendingReview, aún sin revisar en review_events.py (excluidos por defecto).",
    ),
):
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI"),
        auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
    )
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK")

    conditions = []
    if not include_rejected:
        conditions.append("NOT 'Rejected' IN labels(e)")
    if not include_pending:
        conditions.append("NOT 'PendingReview' IN labels(e)")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = QUERY_BASE.format(where=where)

    with driver.session() as s:
        rows = s.run(query).data()
    driver.close()

    if not rows:
        print("⚠️  No se encontraron eventos.")
        return

    df = pd.DataFrame(rows)
    df["etiquetas"] = df["etiquetas"].apply(lambda v: ", ".join(v) if isinstance(v, list) else v)
    df.to_excel(out, index=False, sheet_name="Eventos")
    print(f"✅ {len(df)} eventos exportados a {out}")


if __name__ == "__main__":
    app()
