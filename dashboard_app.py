"""
dashboard_app.py — Hub Cultural DU
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
                OPTIONAL MATCH (a:Account)-[:PARTICIPATED_IN|ORGANIZED]->(e)
                RETURN e.id AS id, e.title AS title, e.eventType AS eventType,
                       e.eventDate AS eventDate, e.locationName AS locationName,
                       e.hotnessScore AS hotnessScore,
                       collect(DISTINCT a.username)[0..5] AS accounts
                ORDER BY e.hotnessScore DESC
                LIMIT 20
            """)
            network = _run(s, """
                MATCH (e:Event)
                OPTIONAL MATCH (a:Account)-[:PARTICIPATED_IN|ORGANIZED|SUPPORTED]->(e)
                RETURN e.id AS eid, e.title AS etitle, e.eventType AS etype,
                       collect(DISTINCT a.username)[0..8] AS accounts
                LIMIT 12
            """)
            locations = _run(s, """
                MATCH (l:Location)
                WHERE l.lat IS NOT NULL AND l.lon IS NOT NULL
                OPTIONAL MATCH (l)<-[:LOCATED_AT]-(p:Post)-[:MENTIONS_EVENT]->(e:Event)
                RETURN l.name AS name, l.lat AS lat, l.lon AS lon,
                       e.eventType AS eventType
                LIMIT 50
            """)
            top_accounts = _run(s, """
                MATCH (a:Account:Public)
                WHERE a.followersCount IS NOT NULL
                RETURN a.username AS username, a.followersCount AS followers,
                       a.culturalRelevanceScore AS score
                ORDER BY coalesce(a.culturalRelevanceScore, 0) DESC
                LIMIT 10
            """)
            top_hashtags = _run(s, """
                MATCH (h:Hashtag)<-[:HAS_HASHTAG]-(p:Post)
                RETURN h.name AS tag, count(p) AS cnt
                ORDER BY cnt DESC
                LIMIT 10
            """)
            stats = {
                "accounts": _run(s, "MATCH (a:Account) RETURN count(a) AS n")[0]["n"],
                "posts": _run(s, "MATCH (p:Post) RETURN count(p) AS n")[0]["n"],
                "events": _run(s, "MATCH (e:Event) RETURN count(e) AS n")[0]["n"],
                "hashtags": _run(s, "MATCH (h:Hashtag) RETURN count(h) AS n")[0]["n"],
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


def build_cytoscape_elements(active_filter=None):
    network = DATA["network"]
    elements = []
    seen_accounts = set()

    for row in network:
        eid = row.get("eid") or ""
        etitle = (row.get("etitle") or "")[:40]
        etype = row.get("etype") or "cultural"
        accounts = row.get("accounts") or []
        color = event_color(etype)

        elements.append({"data": {
            "id": f"e_{eid}", "label": etitle, "type": "event",
            "eventType": etype, "color": color,
        }})

        for username in accounts:
            if not username:
                continue
            if username not in seen_accounts:
                seen_accounts.add(username)
                elements.append({"data": {
                    "id": f"a_{username}", "label": f"@{username}",
                    "type": "account", "color": color,
                }})
            elements.append({"data": {
                "source": f"a_{username}", "target": f"e_{eid}",
                "eventType": etype, "color": color,
            }})

    return elements


def build_network_panel():
    elements = build_cytoscape_elements()
    present_types = sorted({
        r.get("etype") or "cultural"
        for r in DATA["network"] if r.get("etype")
    })

    chip_bar = html.Div(
        [html.Button(
            etype, id={"type": "chip", "index": etype},
            n_clicks=0,
            style={
                "fontFamily": FONT_MONO, "fontSize": "11px",
                "padding": "4px 12px", "borderRadius": "20px",
                "border": f"1px solid {event_color(etype)}",
                "background": event_color(etype) + "22",
                "color": C["text"], "cursor": "pointer",
                "marginRight": "6px", "marginBottom": "6px",
            },
        ) for etype in present_types],
        style={"marginBottom": "12px", "display": "flex", "flexWrap": "wrap"},
    )

    cytoscape = cyto.Cytoscape(
        id="cyto-graph",
        elements=elements,
        layout={"name": "cose", "animate": False, "nodeRepulsion": 6000},
        style={"width": "100%", "height": "380px", "borderRadius": "12px",
               "background": C["panel_bg"]},
        stylesheet=[
            {
                "selector": "node[type='event']",
                "style": {
                    "shape": "round-rectangle",
                    "width": "60px", "height": "28px",
                    "label": "data(label)",
                    "background-color": "data(color)",
                    "color": "#ffffff",
                    "font-size": "9px",
                    "font-family": FONT_MONO,
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-wrap": "wrap",
                    "text-max-width": "55px",
                },
            },
            {
                "selector": "node[type='account']",
                "style": {
                    "shape": "ellipse",
                    "width": "36px", "height": "36px",
                    "label": "data(label)",
                    "background-color": "#ffffff",
                    "border-width": "2px",
                    "border-color": "data(color)",
                    "color": C["text"],
                    "font-size": "8px",
                    "font-family": FONT_MONO,
                    "text-valign": "bottom",
                    "text-halign": "center",
                    "text-margin-y": "4px",
                },
            },
            {
                "selector": "edge",
                "style": {
                    "line-color": "data(color)",
                    "opacity": 0.6,
                    "width": 1.5,
                    "curve-style": "bezier",
                },
            },
        ],
    )

    legend = html.Div(
        [html.Span([
            html.Span(style={
                "display": "inline-block", "width": "10px", "height": "10px",
                "borderRadius": "3px", "background": event_color(et),
                "marginRight": "4px", "verticalAlign": "middle",
            }),
            html.Span(et, style={"fontFamily": FONT_MONO, "fontSize": "10px", "color": C["sub"]}),
        ], style={"marginRight": "12px"}) for et in present_types],
        style={"marginTop": "10px", "display": "flex", "flexWrap": "wrap"},
    )

    return html.Div([
        html.H3("Red de actores", style={
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

    # Identify which chip was clicked
    triggered_id = ctx.triggered[0]["prop_id"]
    clicked_type = None
    for chip_id, n in zip(chip_ids, n_clicks_list):
        id_str = str(chip_id.get("index", ""))
        if id_str and id_str in triggered_id:
            clicked_type = id_str
            break

    # Toggle: clicking active filter deactivates it
    new_filter = None if clicked_type == current_filter else clicked_type

    if new_filter is None:
        # Reset — all elements full opacity
        stylesheet = [
            {
                "selector": "node[type='event']",
                "style": {
                    "shape": "round-rectangle",
                    "width": "60px", "height": "28px",
                    "label": "data(label)",
                    "background-color": "data(color)",
                    "color": "#ffffff",
                    "font-size": "9px",
                    "font-family": FONT_MONO,
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-wrap": "wrap",
                    "text-max-width": "55px",
                    "opacity": 1,
                },
            },
            {
                "selector": "node[type='account']",
                "style": {
                    "shape": "ellipse",
                    "width": "36px", "height": "36px",
                    "label": "data(label)",
                    "background-color": "#ffffff",
                    "border-width": "2px",
                    "border-color": "data(color)",
                    "color": C["text"],
                    "font-size": "8px",
                    "font-family": FONT_MONO,
                    "text-valign": "bottom",
                    "text-halign": "center",
                    "text-margin-y": "4px",
                    "opacity": 1,
                },
            },
            {
                "selector": "edge",
                "style": {
                    "line-color": "data(color)",
                    "opacity": 0.6,
                    "width": 1.5,
                    "curve-style": "bezier",
                },
            },
        ]
    else:
        # Dim non-matching nodes and edges
        stylesheet = [
            {
                "selector": "node[type='event']",
                "style": {
                    "shape": "round-rectangle",
                    "width": "60px", "height": "28px",
                    "label": "data(label)",
                    "background-color": "data(color)",
                    "color": "#ffffff",
                    "font-size": "9px",
                    "font-family": FONT_MONO,
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-wrap": "wrap",
                    "text-max-width": "55px",
                    "opacity": 0.15,
                },
            },
            {
                "selector": "node[type='account']",
                "style": {
                    "shape": "ellipse",
                    "width": "36px", "height": "36px",
                    "label": "data(label)",
                    "background-color": "#ffffff",
                    "border-width": "2px",
                    "border-color": "data(color)",
                    "color": C["text"],
                    "font-size": "8px",
                    "font-family": FONT_MONO,
                    "text-valign": "bottom",
                    "text-halign": "center",
                    "text-margin-y": "4px",
                    "opacity": 0.15,
                },
            },
            {
                "selector": "edge",
                "style": {
                    "line-color": "data(color)",
                    "opacity": 0.1,
                    "width": 1.5,
                    "curve-style": "bezier",
                },
            },
            # Highlight matching event nodes
            {
                "selector": f"node[type='event'][eventType='{new_filter}']",
                "style": {"opacity": 1},
            },
            # Highlight edges attached to matching events
            {
                "selector": f"edge[eventType='{new_filter}']",
                "style": {"opacity": 0.8, "width": 2},
            },
            # Highlight account nodes connected to matching events (via edge eventType)
            {
                "selector": f"node[type='account']",
                "style": {},  # handled by edge-based selection above
            },
        ]

    return stylesheet, new_filter


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=8050)
