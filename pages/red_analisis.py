"""
pages/red_analisis.py — Hub Cultural DU
Página de análisis: red de cuentas (grafo Cytoscape), estadísticas del
pipeline y rankings (top cuentas / hashtags). Separada de la agenda de
eventos porque es un público distinto (análisis de red vs. "qué hacer
esta semana") — ver decisión de Diego de dividir el dashboard en dos.
"""

import dash
from dash import dcc, html, Input, Output, State
import dash_cytoscape as cyto
import plotly.graph_objects as go

from dash_common import C, DATA, REL_COLORS, FONT_SERIF, FONT_SANS, FONT_MONO, fmt_num, category_meta, card_style

cyto.load_extra_layouts()

dash.register_page(__name__, path="/red-analisis", name="Red y análisis", title="Hub Cultural · Red y análisis")


# ---------------------------------------------------------------------------
# En números — estadísticas curadas, legibles como frase, no como gráfica
# a interpretar. Pensadas para el público general del hub, no para un
# analista: cada tarjeta es un hallazgo ya masticado sobre la acumulación
# de eventos + lo que ya calcula run_gds_algorithms.py sobre la red.
# ---------------------------------------------------------------------------
def build_insight_card(label, value, detail=None, icon=None):
    return html.Div([
        html.Div([
            html.I(className=f"ti {icon}", style={"fontSize": "16px", "color": C["blue"], "marginRight": "6px"}) if icon else None,
            html.Span(label, style={
                "fontFamily": FONT_MONO, "fontSize": "11px", "color": C["sub"], "letterSpacing": "0.05em",
            }),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),
        html.Div(value, style={
            "fontFamily": FONT_SERIF, "fontSize": "20px", "fontWeight": "600",
            "color": C["text"], "lineHeight": "1.3",
        }),
        html.Div(detail, style={
            "fontFamily": FONT_SANS, "fontSize": "12px", "color": C["sub"], "marginTop": "6px",
        }) if detail else None,
    ], style=card_style(padding="18px 20px"))


def build_insights_section():
    insights = DATA["insights"]
    top_central = DATA["top_central_accounts"]
    top_bridges = DATA["top_bridge_accounts"]
    cards = []

    if insights.get("top_organizer"):
        author, cnt = insights["top_organizer"]
        cards.append(build_insight_card(
            "CUENTA MÁS ACTIVA", f"@{author}", f"{cnt} eventos publicados", icon="ti-crown",
        ))

    if insights.get("top_category"):
        cat, cnt = insights["top_category"]
        meta = category_meta(cat)
        cards.append(build_insight_card(
            "CATEGORÍA MÁS FRECUENTE", meta["label"], f"{cnt} eventos", icon=meta["icon"],
        ))

    if insights.get("top_weekday"):
        wd, cnt = insights["top_weekday"]
        cards.append(build_insight_card(
            "MEJOR DÍA PARA SALIR", wd, f"{cnt} eventos históricamente", icon="ti-calendar-event",
        ))

    if insights.get("top_location"):
        loc, cnt = insights["top_location"]
        cards.append(build_insight_card(
            "LUGAR CON MÁS EVENTOS", loc, f"{cnt} eventos", icon="ti-map-pin",
        ))

    if top_central:
        top = top_central[0]
        cards.append(build_insight_card(
            "CUENTA MÁS CONECTADA", f"@{top['username']}",
            "Mayor relevancia cultural en la red (PageRank + grado + intermediación)",
            icon="ti-affiliate",
        ))

    if top_bridges:
        top = top_bridges[0]
        cards.append(build_insight_card(
            "CUENTA PUENTE", f"@{top['username']}",
            "Conecta entre sí a distintos grupos de la comunidad",
            icon="ti-git-branch",
        ))

    if not cards:
        return html.Div()

    return html.Div([
        html.H2("En números", style={
            "fontFamily": FONT_SERIF, "fontSize": "22px", "color": C["text"], "margin": "0 0 6px 0",
        }),
        html.P("Lo que dice la acumulación de eventos y la red — sin jerga técnica.", style={
            "fontFamily": FONT_SANS, "fontSize": "13px", "color": C["sub"], "margin": "0 0 16px 0",
        }),
        html.Div(cards, style={
            "display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(220px, 1fr))",
            "gap": "16px", "marginBottom": "20px",
        }),
        build_monthly_chart(insights),
    ], style={"marginBottom": "36px"})


def build_monthly_chart(insights):
    trend = insights.get("monthly_trend") or []
    if not trend:
        fig = go.Figure()
    else:
        months = [m for m, _ in trend]
        counts = [c for _, c in trend]
        fig = go.Figure(go.Bar(
            x=months, y=counts, marker_color=C["blue"],
            hovertemplate="%{x}: %{y} eventos<extra></extra>",
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 8, "r": 8, "t": 8, "b": 8}, height=240,
        font={"family": FONT_MONO, "size": 11, "color": C["text"]},
        xaxis={"showgrid": False}, yaxis={"showgrid": False, "zeroline": False},
        showlegend=False,
    )
    return html.Div([
        html.H4("Eventos por mes", style={
            "fontFamily": FONT_SERIF, "fontSize": "16px", "color": C["text"], "margin": "0 0 12px 0",
        }),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
    ], style={
        "background": C["panel_bg"], "borderRadius": "12px",
        "border": f"1px solid {C['panel_border']}", "padding": "20px",
    })


# ---------------------------------------------------------------------------
# Grafo de cuentas
# ---------------------------------------------------------------------------
def build_cytoscape_elements():
    edges = DATA["network"]
    elements = []
    seen_nodes: dict = {}

    for row in edges:
        src = row.get("source")
        tgt = row.get("target")
        if not src or not tgt:
            continue
        seen_nodes[src] = max(seen_nodes.get(src, 0.0), float(row.get("sourceScore") or 0.0))
        seen_nodes[tgt] = max(seen_nodes.get(tgt, 0.0), float(row.get("targetScore") or 0.0))

    for username, score in seen_nodes.items():
        size = 24 + int(score * 24)
        elements.append({"data": {
            "id": f"a_{username}", "label": f"@{username}", "type": "account",
            "score": round(score, 3), "size": size,
        }})

    for row in edges:
        src = row.get("source")
        tgt = row.get("target")
        rel = row.get("relType") or "MENTIONS"
        wt = int(row.get("weight") or 1)
        if not src or not tgt:
            continue
        elements.append({"data": {
            "source": f"a_{src}", "target": f"a_{tgt}", "relType": rel, "weight": wt,
            "color": REL_COLORS.get(rel, C["sub"]), "width": min(1 + wt * 0.4, 6),
        }})

    return elements


_ACCOUNT_NODE_STYLE = {
    "shape": "ellipse", "width": "data(size)", "height": "data(size)",
    "label": "data(label)", "background-color": "#ffffff",
    "border-width": "2px", "border-color": C["blue"], "color": C["text"],
    "font-size": "8px", "font-family": FONT_MONO,
    "text-valign": "bottom", "text-halign": "center", "text-margin-y": "4px",
}

_EDGE_STYLE = {
    "line-color": "data(color)", "width": "data(width)", "opacity": 0.55,
    "curve-style": "bezier", "target-arrow-shape": "triangle",
    "target-arrow-color": "data(color)", "arrow-scale": 0.7,
}

BASE_STYLESHEET = [
    {"selector": "node[type='account']", "style": _ACCOUNT_NODE_STYLE},
    {"selector": "edge", "style": _EDGE_STYLE},
]


def build_network_panel():
    elements = build_cytoscape_elements()
    rel_types = sorted({r.get("relType") for r in DATA["network"] if r.get("relType")})

    chip_bar = html.Div(
        [html.Button(
            rel, id={"type": "chip", "index": rel}, n_clicks=0,
            style={
                "fontFamily": FONT_MONO, "fontSize": "11px",
                "padding": "4px 12px", "borderRadius": "20px",
                "border": f"1px solid {REL_COLORS.get(rel, C['sub'])}",
                "background": REL_COLORS.get(rel, C["sub"]) + "22",
                "color": C["text"], "cursor": "pointer",
                "marginRight": "6px", "marginBottom": "6px",
            },
        ) for rel in rel_types],
        style={"marginBottom": "12px", "display": "flex", "flexWrap": "wrap"},
    )

    cytoscape = cyto.Cytoscape(
        id="cyto-graph", elements=elements,
        layout={"name": "cose", "animate": False, "nodeRepulsion": 8000,
                "idealEdgeLength": 80, "gravity": 0.25},
        style={"width": "100%", "height": "480px", "borderRadius": "12px", "background": C["panel_bg"]},
        stylesheet=BASE_STYLESHEET,
    )

    legend = html.Div(
        [html.Span([
            html.Span(style={
                "display": "inline-block", "width": "28px", "height": "3px",
                "background": REL_COLORS.get(rel, C["sub"]),
                "marginRight": "5px", "verticalAlign": "middle",
            }),
            html.Span(rel, style={"fontFamily": FONT_MONO, "fontSize": "10px", "color": C["sub"]}),
        ], style={"marginRight": "18px"}) for rel in rel_types],
        style={"marginTop": "10px", "display": "flex", "flexWrap": "wrap"},
    )

    return html.Div([
        html.H3("Mapa de conexiones", style={
            "fontFamily": FONT_SERIF, "fontSize": "16px", "color": C["text"], "margin": "0 0 4px 0",
        }),
        html.P("Cada punto es una cuenta; entre más grande, más central es en la comunidad. Las líneas son menciones y etiquetas entre publicaciones.", style={
            "fontFamily": FONT_SANS, "fontSize": "12px", "color": C["sub"], "margin": "0 0 14px 0",
        }),
        chip_bar, cytoscape, legend,
    ], style={
        "background": C["panel_bg"], "borderRadius": "12px",
        "border": f"1px solid {C['panel_border']}", "padding": "20px", "marginBottom": "36px",
    })


# ---------------------------------------------------------------------------
# Estadísticas + rankings
# ---------------------------------------------------------------------------
def stat_card(label, value):
    return html.Div([
        html.Div(fmt_num(value), style={
            "fontFamily": FONT_MONO, "fontSize": "36px", "fontWeight": "500",
            "color": C["text"], "lineHeight": "1",
        }),
        html.Div(label, style={"fontFamily": FONT_SANS, "fontSize": "13px", "color": C["sub"], "marginTop": "6px"}),
    ], style={
        "background": C["card"], "borderRadius": "16px", "border": f"1px solid {C['border']}",
        "padding": "20px 24px", "textAlign": "center",
    })


def build_bar_chart(items, label_key, value_key, color, title):
    if not items:
        fig = go.Figure()
    else:
        labels = [str(r.get(label_key) or "") for r in reversed(items)]
        values = [int(r.get(value_key) or 0) for r in reversed(items)]
        fig = go.Figure(go.Bar(
            x=values, y=labels, orientation="h", marker_color=color,
            hovertemplate="%{y}: %{x:,}<extra></extra>",
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 8, "r": 8, "t": 8, "b": 8}, height=300,
        font={"family": FONT_MONO, "size": 11, "color": C["text"]},
        xaxis={"showgrid": False, "zeroline": False, "showticklabels": False},
        yaxis={"showgrid": False, "zeroline": False}, showlegend=False,
    )
    return html.Div([
        html.H4(title, style={"fontFamily": FONT_SERIF, "fontSize": "16px", "color": C["text"], "margin": "0 0 12px 0"}),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
    ], style={
        "background": C["panel_bg"], "borderRadius": "12px",
        "border": f"1px solid {C['panel_border']}", "padding": "20px",
    })


def build_analysis_section():
    stats = DATA["stats"]
    stat_cards = html.Div([
        stat_card("Cuentas analizadas", stats["accounts"]),
        stat_card("Posts procesados", stats["posts"]),
        stat_card("Eventos detectados", stats["events"]),
        stat_card("Hashtags únicos", stats["hashtags"]),
    ], style={"display": "grid", "gridTemplateColumns": "repeat(4, 1fr)", "gap": "16px", "marginBottom": "20px"})

    charts = html.Div([
        build_bar_chart(DATA["top_accounts"], "username", "followers", C["blue"], "Top 10 cuentas por seguidores"),
        build_bar_chart(DATA["top_hashtags"], "tag", "cnt", C["yellow"], "Top 10 hashtags"),
    ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "20px"})

    return html.Div([stat_cards, charts])


layout = html.Div([
    dcc.Store(id="active-filter", data=None),
    html.H1("Red y análisis", style={
        "fontFamily": FONT_SERIF, "fontSize": "32px", "fontWeight": "600",
        "color": C["text"], "margin": "32px 0 8px 0",
    }),
    html.P("Radiografía de la comunidad cultural colombiana en Francia.", style={
        "fontFamily": FONT_SANS, "fontSize": "14px", "color": C["sub"], "margin": "0 0 24px 0",
    }),
    build_insights_section(),
    build_network_panel(),
    build_analysis_section(),
])


@dash.callback(
    Output("cyto-graph", "stylesheet"),
    Output("active-filter", "data"),
    Input({"type": "chip", "index": dash.ALL}, "n_clicks"),
    State("active-filter", "data"),
    State({"type": "chip", "index": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def filter_graph(n_clicks_list, current_filter, chip_ids):
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update, dash.no_update

    triggered_id = ctx.triggered[0]["prop_id"]
    clicked_type = None
    for chip_id, n in zip(chip_ids, n_clicks_list):
        id_str = str(chip_id.get("index", ""))
        if id_str and id_str in triggered_id:
            clicked_type = id_str
            break

    new_filter = None if clicked_type == current_filter else clicked_type

    if new_filter is None:
        stylesheet = BASE_STYLESHEET
    else:
        highlight_color = REL_COLORS.get(new_filter, C["sub"])
        stylesheet = [
            {"selector": "node[type='account']", "style": {**_ACCOUNT_NODE_STYLE, "opacity": 0.15}},
            {"selector": "edge", "style": {**_EDGE_STYLE, "opacity": 0.08}},
            {"selector": f"edge[relType='{new_filter}']",
             "style": {**_EDGE_STYLE, "opacity": 0.85, "line-color": highlight_color,
                       "target-arrow-color": highlight_color, "width": 2.5}},
        ]
        touched = set()
        for row in DATA["network"]:
            if row.get("relType") == new_filter:
                if row.get("source"):
                    touched.add(f"a_{row['source']}")
                if row.get("target"):
                    touched.add(f"a_{row['target']}")
        for node_id in touched:
            stylesheet.append({
                "selector": f"node[id='{node_id}']",
                "style": {**_ACCOUNT_NODE_STYLE, "opacity": 1, "border-color": highlight_color},
            })

    return stylesheet, new_filter
