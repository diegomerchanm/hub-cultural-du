"""
5_visualize_dashboard.py — Hub Cultural DU
Cultural dashboard for the Colombian diaspora in Paris.
Reads live data from Neo4j; falls back to empty/placeholder state if unavailable.
"""

import os
from dotenv import load_dotenv

import dash
from dash import dcc, html, Input, Output, State
import dash_cytoscape as cyto
import plotly.graph_objects as go
import dash_bootstrap_components as dbc

load_dotenv()

# ---------------------------------------------------------------------------
# Neo4j driver (optional — dashboard renders with zeros if unavailable)
# ---------------------------------------------------------------------------
try:
    from neo4j import GraphDatabase

    _uri = os.getenv("NEO4J_URI", "")
    _user = os.getenv("NEO4J_USERNAME", "")
    _pwd = os.getenv("NEO4J_PASSWORD", "")
    if _uri and _user and _pwd:
        driver = GraphDatabase.driver(_uri, auth=(_user, _pwd))
        driver.verify_connectivity()
    else:
        driver = None
except Exception:
    driver = None

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
C = {
    "bg": "#ece9e3",
    "text": "#232730",
    "blue": "#2f5aa8",
    "yellow": "#e0b02e",
    "red": "#b0384a",
    "border": "#ded9cf",
    "card": "#ffffff",
    "sub": "#6b6a63",
    "panel_bg": "#f2efe8",
    "panel_border": "#e7e2d8",
}

REL_COLORS = {
    "MENTIONS":  "#2f5aa8",
    "TAGS_USER": "#e0b02e",
}

EVENT_COLORS = {
    "musical": "#2f5aa8",
    "visual": "#c49a2c",
    "escenico": "#7b5ea7",
    "audiovisual": "#a5603a",
    "formacion": "#4a8c6f",
    "festival": "#e0b02e",
    "comunitario": "#6b8c4a",
    "institucional": "#4a6b8c",
    "academico": "#5f6b8c",
    "gastronomico": "#b0384a",
    "politico": "#8f8d84",
}

FONT_SERIF = "'Newsreader', Georgia, serif"
FONT_SANS = "'IBM Plex Sans', system-ui, sans-serif"
FONT_MONO = "'IBM Plex Mono', 'Courier New', monospace"

GOOGLE_FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Newsreader:ital,wght@0,400;0,600;1,400&"
    "family=IBM+Plex+Sans:wght@400;500;600&"
    "family=IBM+Plex+Mono:wght@400;500&"
    "display=swap"
)

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _run(session, query):
    return list(session.run(query))


def fetch_data():
    """Fetch all dashboard data from Neo4j. Returns safe empty dict on failure."""
    empty = {
        "events": [],
        "network": [],
        "locations": [],
        "top_accounts": [],
        "top_hashtags": [],
        "stats": {"accounts": 0, "posts": 0, "events": 0, "hashtags": 0},
    }
    if driver is None:
        return empty
    try:
        with driver.session() as s:
            events = _run(s, """
                MATCH (e:Event)
                WHERE NOT 'Rejected' IN labels(e)
                OPTIONAL MATCH (a:Account)-[:PARTICIPATED_IN|ORGANIZED]->(e)
                RETURN e.id AS id, e.title AS title, e.category AS eventType,
                       e.eventDate AS eventDate, e.locationName AS locationName,
                       e.hotnessScore AS hotnessScore,
                       collect(DISTINCT a.username)[0..5] AS accounts
                ORDER BY e.hotnessScore DESC
                LIMIT 20
            """)
            network = _run(s, """
                MATCH (a:Account)-[:PUBLISHED]->(p:Post)-[r:MENTIONS|TAGS_USER]->(b:Account)
                WHERE a <> b
                WITH a, b, type(r) AS relType, count(*) AS weight
                RETURN a.username AS source,
                       coalesce(a.culturalRelevanceScore, 0.0) AS sourceScore,
                       b.username AS target,
                       coalesce(b.culturalRelevanceScore, 0.0) AS targetScore,
                       relType, weight
                ORDER BY weight DESC
                LIMIT 100
            """)
            locations = _run(s, """
                MATCH (l:Location)
                WHERE l.lat IS NOT NULL AND l.lon IS NOT NULL
                OPTIONAL MATCH (l)<-[:LOCATED_AT]-(p:Post)-[:MENTIONS_EVENT]->(e:Event)
                WHERE NOT 'Rejected' IN labels(e)
                RETURN l.name AS name, l.lat AS lat, l.lon AS lon,
                       e.category AS eventType
                LIMIT 50
            """)
            top_accounts = _run(s, """
                MATCH (a:Account:Public)
                WHERE a.followersCount IS NOT NULL
                RETURN a.username AS username, a.followersCount AS followers,
                       a.culturalRelevanceScore AS score
                ORDER BY a.followersCount DESC
                LIMIT 10
            """)
            top_hashtags = _run(s, """
                MATCH (h:Hashtag)<-[:HAS_HASHTAG]-(p:Post)
                RETURN h.name AS tag, count(p) AS cnt
                ORDER BY cnt DESC
                LIMIT 10
            """)
            stats = {
                "accounts":  _run(s, "MATCH (a:Account) RETURN count(a) AS n")[0]["n"],
                "posts":     _run(s, "MATCH (p:Post) RETURN count(p) AS n")[0]["n"],
                "events":    _run(s, "MATCH (e:Event) WHERE NOT 'Rejected' IN labels(e) RETURN count(e) AS n")[0]["n"],
                "hashtags":  _run(s, "MATCH (h:Hashtag) RETURN count(h) AS n")[0]["n"],
            }
            return {
                "events": [dict(r) for r in events],
                "network": [dict(r) for r in network],
                "locations": [dict(r) for r in locations],
                "top_accounts": [dict(r) for r in top_accounts],
                "top_hashtags": [dict(r) for r in top_hashtags],
                "stats": stats,
            }
    except Exception:
        return empty


DATA = fetch_data()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_num(n):
    """Format integer with spaces as thousand separators."""
    if n is None:
        return "0"
    return f"{int(n):,}".replace(",", "\u00a0")


def event_color(etype):
    return EVENT_COLORS.get(str(etype).lower() if etype else "", C["sub"])


def card_style(**extra):
    base = {
        "background": C["card"],
        "borderRadius": "16px",
        "border": f"1px solid {C['border']}",
        "overflow": "hidden",
    }
    base.update(extra)
    return base


def pill_style(color=None):
    return {
        "display": "inline-block",
        "padding": "2px 10px",
        "borderRadius": "20px",
        "background": color or C["panel_bg"],
        "border": f"1px solid {C['panel_border']}",
        "fontFamily": FONT_MONO,
        "fontSize": "11px",
        "color": C["text"],
        "marginRight": "4px",
        "marginBottom": "4px",
    }


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def build_header():
    n_events = len(DATA["events"])
    bar = html.Div(
        style={"display": "flex", "height": "9px", "borderRadius": "4px", "overflow": "hidden", "marginBottom": "10px"},
        children=[
            html.Div(style={"flex": "2", "background": C["yellow"]}),
            html.Div(style={"flex": "1", "background": C["blue"]}),
            html.Div(style={"flex": "1", "background": C["red"]}),
        ],
    )
    left = html.Div([
        bar,
        html.H1("Hub Cultural", style={
            "fontFamily": FONT_SERIF, "fontSize": "48px", "fontWeight": "600",
            "color": C["text"], "margin": "0 0 4px 0", "lineHeight": "1.1",
        }),
        html.P("AGENDA CULTURAL · DIÁSPORA COLOMBIANA EN FRANCIA", style={
            "fontFamily": FONT_MONO, "fontSize": "12px", "color": C["blue"],
            "margin": "0", "letterSpacing": "0.06em",
        }),
    ], style={"flex": "1"})

    right = html.Div([
        html.Div([
            html.Span(fmt_num(n_events), style={
                "fontFamily": FONT_MONO, "fontSize": "52px", "fontWeight": "500",
                "color": C["text"], "lineHeight": "1",
            }),
            html.Span("eventos próximos", style={
                "fontFamily": FONT_SANS, "fontSize": "13px", "color": C["sub"],
                "display": "block", "marginTop": "4px",
            }),
        ], style={"textAlign": "right", "marginRight": "20px"}),
        html.Div(style={"width": "1px", "background": C["border"], "margin": "0 20px"}),
        html.Div("París · 2026", style={
            "fontFamily": FONT_MONO, "fontSize": "13px",
            "padding": "6px 18px", "borderRadius": "20px",
            "border": f"1px solid {C['border']}", "color": C["text"],
            "background": C["panel_bg"], "whiteSpace": "nowrap",
        }),
    ], style={"display": "flex", "alignItems": "center"})

    return html.Div([left, right], style={
        "display": "flex", "alignItems": "flex-end", "justifyContent": "space-between",
        "padding": "32px 0 24px 0",
        "borderBottom": f"1px solid {C['border']}",
        "marginBottom": "32px",
    })


def build_featured_card(event):
    etype = event.get("eventType") or "cultural"
    color = event_color(etype)
    title = event.get("title") or "Evento cultural"
    date_raw = event.get("eventDate") or ""
    location = event.get("locationName") or ""
    accounts = event.get("accounts") or []

    gradient = f"linear-gradient(135deg, {color}ee 0%, {color}99 100%)"

    return html.Div([
        # Colored header band
        html.Div([
            html.Span(etype.upper(), style={
                "fontFamily": FONT_MONO, "fontSize": "11px", "color": "#ffffff",
                "background": "rgba(0,0,0,0.25)", "padding": "3px 10px",
                "borderRadius": "20px", "letterSpacing": "0.08em",
            }),
        ], style={
            "height": "160px", "background": gradient,
            "display": "flex", "alignItems": "flex-end", "padding": "16px",
        }),
        # Body
        html.Div([
            html.P(str(date_raw)[:10] if date_raw else "—", style={
                "fontFamily": FONT_MONO, "fontSize": "11px", "color": C["sub"],
                "margin": "0 0 8px 0",
            }),
            html.H2(title, style={
                "fontFamily": FONT_SERIF, "fontSize": "28px", "fontWeight": "600",
                "color": C["text"], "margin": "0 0 10px 0", "lineHeight": "1.25",
            }),
            html.P(location, style={
                "fontFamily": FONT_SANS, "fontSize": "13px", "color": C["sub"],
                "margin": "0 0 14px 0",
            }),
            html.Div([
                html.Span(f"@{a}", style=pill_style(color + "22")) for a in accounts if a
            ]),
        ], style={"padding": "20px"}),
    ], style=card_style())


def build_upcoming_card(event):
    etype = event.get("eventType") or "cultural"
    color = event_color(etype)
    title = event.get("title") or "Evento"
    date_raw = event.get("eventDate") or ""
    location = event.get("locationName") or ""

    return html.Div([
        html.Div(style={
            "width": "6px", "background": color,
            "borderRadius": "3px 0 0 3px", "flexShrink": "0",
        }),
        html.Div([
            html.Div([
                html.Span("●", style={"color": color, "fontSize": "8px", "marginRight": "6px"}),
                html.Span(f"{etype} · {str(date_raw)[:10] if date_raw else '—'}", style={
                    "fontFamily": FONT_MONO, "fontSize": "11px", "color": C["sub"],
                }),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "6px"}),
            html.P(title, style={
                "fontFamily": FONT_SERIF, "fontSize": "19px", "fontWeight": "600",
                "color": C["text"], "margin": "0 0 4px 0", "lineHeight": "1.3",
            }),
            html.P(location, style={
                "fontFamily": FONT_SANS, "fontSize": "12px", "color": C["sub"], "margin": "0",
            }),
        ], style={"padding": "14px 16px", "flex": "1"}),
    ], style={
        **card_style(display="flex", flexDirection="row", minHeight="90px"),
        "marginBottom": "12px",
    })


def build_events_section():
    events = DATA["events"]
    featured = events[0] if events else {}
    upcoming = events[1:4]

    while len(upcoming) < 3:
        upcoming.append({})

    left = build_featured_card(featured)
    right = html.Div([build_upcoming_card(e) for e in upcoming])

    return html.Div([
        html.H2("Próximos eventos", style={
            "fontFamily": FONT_SERIF, "fontSize": "22px", "color": C["text"],
            "margin": "0 0 16px 0",
        }),
        html.Div([left, right], style={
            "display": "grid",
            "gridTemplateColumns": "1.35fr 1fr",
            "gap": "20px",
        }),
    ], style={"marginBottom": "36px"})


def build_cytoscape_elements():
    edges   = DATA["network"]
    elements = []
    seen_nodes: dict = {}   # username → max score seen

    for row in edges:
        src = row.get("source")
        tgt = row.get("target")
        if not src or not tgt:
            continue
        seen_nodes[src] = max(seen_nodes.get(src, 0.0), float(row.get("sourceScore") or 0.0))
        seen_nodes[tgt] = max(seen_nodes.get(tgt, 0.0), float(row.get("targetScore") or 0.0))

    for username, score in seen_nodes.items():
        # Node size: 24px base + up to 24px extra from percentile score
        size = 24 + int(score * 24)
        elements.append({"data": {
            "id":    f"a_{username}",
            "label": f"@{username}",
            "type":  "account",
            "score": round(score, 3),
            "size":  size,
        }})

    for row in edges:
        src = row.get("source")
        tgt = row.get("target")
        rel = row.get("relType") or "MENTIONS"
        wt  = int(row.get("weight") or 1)
        if not src or not tgt:
            continue
        elements.append({"data": {
            "source":  f"a_{src}",
            "target":  f"a_{tgt}",
            "relType": rel,
            "weight":  wt,
            "color":   REL_COLORS.get(rel, C["sub"]),
            "width":   min(1 + wt * 0.4, 6),
        }})

    return elements


_ACCOUNT_NODE_STYLE = {
    "shape": "ellipse",
    "width":  "data(size)",
    "height": "data(size)",
    "label":  "data(label)",
    "background-color": "#ffffff",
    "border-width": "2px",
    "border-color": C["blue"],
    "color": C["text"],
    "font-size": "8px",
    "font-family": FONT_MONO,
    "text-valign": "bottom",
    "text-halign": "center",
    "text-margin-y": "4px",
}

_EDGE_STYLE = {
    "line-color":   "data(color)",
    "width":        "data(width)",
    "opacity":      0.55,
    "curve-style":  "bezier",
    "target-arrow-shape": "triangle",
    "target-arrow-color": "data(color)",
    "arrow-scale":  0.7,
}

BASE_STYLESHEET = [
    {"selector": "node[type='account']", "style": _ACCOUNT_NODE_STYLE},
    {"selector": "edge",                 "style": _EDGE_STYLE},
]


def build_network_panel():
    elements   = build_cytoscape_elements()
    rel_types  = sorted({r.get("relType") for r in DATA["network"] if r.get("relType")})

    chip_bar = html.Div(
        [html.Button(
            rel, id={"type": "chip", "index": rel},
            n_clicks=0,
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
        id="cyto-graph",
        elements=elements,
        layout={"name": "cose", "animate": False, "nodeRepulsion": 8000,
                "idealEdgeLength": 80, "gravity": 0.25},
        style={"width": "100%", "height": "380px", "borderRadius": "12px",
               "background": C["panel_bg"]},
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
        html.H3("Red de cuentas (MENTIONS · TAGS_USER)", style={
            "fontFamily": FONT_SERIF, "fontSize": "16px", "color": C["text"],
            "margin": "0 0 12px 0",
        }),
        chip_bar,
        cytoscape,
        legend,
    ], style={
        "background": C["panel_bg"],
        "borderRadius": "12px",
        "border": f"1px solid {C['panel_border']}",
        "padding": "20px",
    })


def build_map_panel():
    locs = DATA["locations"]
    if locs:
        lats = [r["lat"] for r in locs if r.get("lat") is not None]
        lons = [r["lon"] for r in locs if r.get("lon") is not None]
        names = [r.get("name") or "" for r in locs]
        colors = [event_color(r.get("eventType")) for r in locs]
        texts = [
            f"{r.get('name','')}<br>{r.get('eventType','')}" for r in locs
        ]
    else:
        lats, lons, names, colors, texts = [], [], [], [], []

    fig = go.Figure(go.Scattermapbox(
        lat=lats, lon=lons,
        mode="markers",
        marker=go.scattermapbox.Marker(size=12, color=colors, opacity=0.85),
        text=texts,
        hovertemplate="%{text}<extra></extra>",
    ))
    fig.update_layout(
        mapbox={
            "style": "open-street-map",
            "center": {"lat": 48.8566, "lon": 2.3522},
            "zoom": 11,
        },
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        height=420,
    )

    return html.Div([
        html.H3("Mapa de ubicaciones", style={
            "fontFamily": FONT_SERIF, "fontSize": "16px", "color": C["text"],
            "margin": "0 0 12px 0",
        }),
        dcc.Graph(figure=fig, config={"displayModeBar": False}, style={"borderRadius": "12px", "overflow": "hidden"}),
    ], style={
        "background": C["panel_bg"],
        "borderRadius": "12px",
        "border": f"1px solid {C['panel_border']}",
        "padding": "20px",
    })


def build_network_section():
    return html.Div([
        html.H2("Red · Mapa", style={
            "fontFamily": FONT_SERIF, "fontSize": "22px", "color": C["text"],
            "margin": "0 0 16px 0",
        }),
        html.Div([
            build_network_panel(),
            build_map_panel(),
        ], style={
            "display": "grid",
            "gridTemplateColumns": "1.4fr 1fr",
            "gap": "20px",
        }),
    ], style={"marginBottom": "36px"})


def stat_card(label, value):
    return html.Div([
        html.Div(fmt_num(value), style={
            "fontFamily": FONT_MONO, "fontSize": "36px", "fontWeight": "500",
            "color": C["text"], "lineHeight": "1",
        }),
        html.Div(label, style={
            "fontFamily": FONT_SANS, "fontSize": "13px", "color": C["sub"],
            "marginTop": "6px",
        }),
    ], style={
        **card_style(padding="20px 24px"),
        "textAlign": "center",
    })


def build_bar_chart(items, label_key, value_key, color, title):
    if not items:
        fig = go.Figure()
    else:
        labels = [str(r.get(label_key) or "") for r in reversed(items)]
        values = [int(r.get(value_key) or 0) for r in reversed(items)]
        fig = go.Figure(go.Bar(
            x=values, y=labels,
            orientation="h",
            marker_color=color,
            hovertemplate="%{y}: %{x:,}<extra></extra>",
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        height=300,
        font={"family": FONT_MONO, "size": 11, "color": C["text"]},
        xaxis={"showgrid": False, "zeroline": False, "showticklabels": False},
        yaxis={"showgrid": False, "zeroline": False},
        showlegend=False,
    )
    return html.Div([
        html.H4(title, style={
            "fontFamily": FONT_SERIF, "fontSize": "16px", "color": C["text"],
            "margin": "0 0 12px 0",
        }),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
    ], style={
        "background": C["panel_bg"],
        "borderRadius": "12px",
        "border": f"1px solid {C['panel_border']}",
        "padding": "20px",
    })


def build_analysis_section():
    stats = DATA["stats"]
    stat_cards = html.Div([
        stat_card("Cuentas analizadas", stats["accounts"]),
        stat_card("Posts procesados", stats["posts"]),
        stat_card("Eventos detectados", stats["events"]),
        stat_card("Hashtags únicos", stats["hashtags"]),
    ], style={
        "display": "grid",
        "gridTemplateColumns": "repeat(4, 1fr)",
        "gap": "16px",
        "marginBottom": "20px",
    })

    charts = html.Div([
        build_bar_chart(DATA["top_accounts"], "username", "followers", C["blue"], "Top 10 cuentas por seguidores"),
        build_bar_chart(DATA["top_hashtags"], "tag", "cnt", C["yellow"], "Top 10 hashtags"),
    ], style={
        "display": "grid",
        "gridTemplateColumns": "1fr 1fr",
        "gap": "20px",
    })

    divider = html.Div([
        html.Div(style={"flex": "1", "borderTop": f"1px solid {C['border']}"}),
        html.Span("Panel de análisis · uso interno", style={
            "fontFamily": FONT_MONO, "fontSize": "11px", "color": C["sub"],
            "padding": "0 16px", "whiteSpace": "nowrap",
            "letterSpacing": "0.05em",
        }),
        html.Div(style={"flex": "1", "borderTop": f"1px solid {C['border']}"}),
    ], style={"display": "flex", "alignItems": "center", "marginBottom": "24px"})

    return html.Div([divider, stat_cards, charts])


# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------
cyto.load_extra_layouts()

app = dash.Dash(
    __name__,
    external_stylesheets=[
        GOOGLE_FONTS,
        dbc.themes.BOOTSTRAP,
    ],
    suppress_callback_exceptions=True,
    title="Hub Cultural DU",
)

app.layout = html.Div([
    dcc.Store(id="active-filter", data=None),

    html.Div([
        build_header(),
        build_events_section(),
        build_network_section(),
        build_analysis_section(),

        # Footer
        html.Div(style={"borderTop": f"1px solid {C['border']}", "marginTop": "40px", "paddingTop": "16px"}),
        html.P("Hub Cultural DU · Diáspora colombiana en París · 2026", style={
            "fontFamily": FONT_MONO, "fontSize": "11px", "color": C["sub"],
            "textAlign": "center", "paddingBottom": "32px",
        }),
    ], style={
        "maxWidth": "1400px",
        "margin": "0 auto",
        "padding": "0 32px",
    }),
], style={
    "background": C["bg"],
    "minHeight": "100vh",
    "fontFamily": FONT_SANS,
    "color": C["text"],
})

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@app.callback(
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

    # Toggle: clicking the active filter resets it
    new_filter = None if clicked_type == current_filter else clicked_type

    if new_filter is None:
        stylesheet = BASE_STYLESHEET
    else:
        highlight_color = REL_COLORS.get(new_filter, C["sub"])
        stylesheet = [
            # All nodes dim by default
            {"selector": "node[type='account']",
             "style": {**_ACCOUNT_NODE_STYLE, "opacity": 0.15}},
            # All edges dim by default
            {"selector": "edge",
             "style": {**_EDGE_STYLE, "opacity": 0.08}},
            # Highlight edges of the selected relType
            {"selector": f"edge[relType='{new_filter}']",
             "style": {**_EDGE_STYLE, "opacity": 0.85,
                       "line-color": highlight_color,
                       "target-arrow-color": highlight_color,
                       "width": 2.5}},
            # Highlight nodes that have at least one active edge
            # (Cytoscape CSS :selected approach — we target connected nodes via
            #  the source/target data already in the graph; dim override lifted)
            {"selector": f"node[type='account']:childless",
             "style": {}},   # no-op; real lift is done server-side below
        ]
        # Build set of node IDs touched by active-relType edges, then add
        # per-node selectors to lift opacity only for those nodes.
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
                "style": {**_ACCOUNT_NODE_STYLE, "opacity": 1,
                          "border-color": highlight_color},
            })

    return stylesheet, new_filter


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=8050)
