"""
plot_eventscore_boxplot.py — Hub Cultural DU
Boxplot interactivo del eventScore de los :Event, para explorar la
distribución de puntajes fuera de Neo4j. Uso puntual/ad-hoc — no forma
parte del pipeline.

Uso:
    python plot_eventscore_boxplot.py
    python plot_eventscore_boxplot.py --out boxplot.html
    python plot_eventscore_boxplot.py --by-category
"""
import os

import pandas as pd
import plotly.express as px
import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
app = typer.Typer(add_completion=False)

QUERY = """
    MATCH (e:Event)
    WHERE NOT 'Rejected' IN labels(e) AND e.eventScore IS NOT NULL
    RETURN e.id AS id, e.title AS titulo, e.category AS categoria, e.eventScore AS score
"""


@app.command()
def main(
    out: str = typer.Option("eventscore_boxplot.html", "--out", help="Archivo .html de salida."),
    by_category: bool = typer.Option(
        False, "--by-category",
        help="Un boxplot separado por categoría en vez de uno solo global.",
    ),
):
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI"),
        auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
    )
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK")

    with driver.session() as s:
        rows = s.run(QUERY).data()
    driver.close()

    if not rows:
        print("⚠️  No hay eventos con eventScore.")
        return

    df = pd.DataFrame(rows)
    print(f"\n{len(df)} eventos con score.\n")
    print(df["score"].describe())

    if by_category:
        fig = px.box(
            df, x="categoria", y="score", points="all", hover_data=["titulo"],
            title="Distribución de eventScore por categoría",
        )
    else:
        fig = px.box(
            df, y="score", points="all", hover_data=["titulo", "categoria"],
            title="Distribución de eventScore — todos los eventos",
        )

    fig.write_html(out)
    print(f"\n✅ Guardado en {out} — ábrelo en el navegador (interactivo: pasa el mouse sobre cada punto para ver el evento).")


if __name__ == "__main__":
    app()
