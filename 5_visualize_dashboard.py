"""
5_visualize_dashboard.py — Hub Cultural DU
Punto de entrada del dashboard multi-página. Toda la lógica de tokens,
conexión a Neo4j y fetch_data() vive en dash_common.py; cada página vive
en pages/ (dash.register_page): "/" = Eventos, "/red-analisis" = Red y
análisis. Este archivo solo arma el shell (nav + page_container).
"""

import dash
from dash import dcc, html
import dash_cytoscape as cyto
import dash_bootstrap_components as dbc

from dash_common import C, GOOGLE_FONTS, FONT_SERIF, FONT_SANS, FONT_MONO

cyto.load_extra_layouts()

app = dash.Dash(
    __name__,
    use_pages=True,
    pages_folder="pages",
    external_stylesheets=[
        GOOGLE_FONTS,
        dbc.themes.BOOTSTRAP,
        "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@2/dist/tabler-icons.min.css",
    ],
    suppress_callback_exceptions=True,
    title="Hub Cultural DU",
)


def build_nav():
    links = []
    for page in dash.page_registry.values():
        links.append(dcc.Link(
            page["name"],
            href=page["relative_path"],
            style={
                "fontFamily": FONT_MONO, "fontSize": "13px", "color": C["text"],
                "padding": "6px 16px", "borderRadius": "20px",
                "border": f"1px solid {C['border']}", "background": C["card"],
                "textDecoration": "none", "marginRight": "8px",
            },
        ))
    return html.Div(links, style={"display": "flex", "padding": "20px 0 0 0"})


app.layout = html.Div([
    html.Div([
        build_nav(),
        dash.page_container,

        html.Div(style={"borderTop": f"1px solid {C['border']}", "marginTop": "40px", "paddingTop": "16px"}),
        html.P("Hub Cultural DU · Diáspora colombiana en París · 2026", style={
            "fontFamily": FONT_MONO, "fontSize": "11px", "color": C["sub"],
            "textAlign": "center", "paddingBottom": "32px",
        }),
    ], style={"maxWidth": "1400px", "margin": "0 auto", "padding": "0 32px"}),
], style={
    "background": C["bg"], "minHeight": "100vh",
    "fontFamily": FONT_SANS, "color": C["text"],
})


if __name__ == "__main__":
    app.run(debug=True, port=8050)
