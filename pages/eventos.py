"""
pages/eventos.py — Hub Cultural DU
Página principal: agenda de eventos culturales de la diáspora colombiana
en Francia. Navegación por tiempo (hoy / esta semana / este mes / todos los
próximos / pasados) como eje principal, categorías como filtro secundario
— inspirado en cómo la gente busca salidas (ver DD-XXX: la mayoría busca
para el mismo día o la misma semana, no con semanas de anticipación).
"Próximos" y "Pasados" están separados a propósito en vez de un único
"Todos" ambiguo — evita el bug donde "Todos" en realidad significaba
"de hoy en adelante" y escondía silenciosamente los eventos ya pasados.
"""

import dash
from dash import dcc, html, Input, Output, State

from dash_common import (
    C, DATA, FONT_SERIF, FONT_MONO, FONT_SANS,
    category_meta, fmt_num, card_style, instagram_url, date_bucket_bounds,
)
import plotly.graph_objects as go

dash.register_page(__name__, path="/", name="Eventos", title="Hub Cultural · Eventos")

TIME_TABS = [
    ("hoy", "Hoy"),
    ("semana", "Esta semana"),
    ("mes", "Este mes"),
    ("proximos", "Todos los próximos"),
    ("pasados", "Pasados"),
]
DEFAULT_TIME_TAB = "proximos"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def build_header():
    n_events = DATA["stats"].get("upcoming_clean", len(DATA["events"]))
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
        html.Span(fmt_num(n_events), style={
            "fontFamily": FONT_MONO, "fontSize": "52px", "fontWeight": "500",
            "color": C["text"], "lineHeight": "1",
        }),
        html.Span("eventos confirmados", style={
            "fontFamily": FONT_SANS, "fontSize": "13px", "color": C["sub"],
            "display": "block", "marginTop": "4px",
        }),
    ], style={"textAlign": "right"})

    return html.Div([left, right], style={
        "display": "flex", "alignItems": "flex-end", "justifyContent": "space-between",
        "padding": "32px 0 24px 0",
        "borderBottom": f"1px solid {C['border']}",
        "marginBottom": "24px",
    })


# ---------------------------------------------------------------------------
# Filtros: pestañas de tiempo + chips de categoría
# ---------------------------------------------------------------------------
def _tab_style(active: bool):
    return {
        "fontFamily": FONT_SANS, "fontSize": "14px", "fontWeight": "500",
        "padding": "8px 18px", "borderRadius": "20px",
        "border": f"1px solid {C['border']}",
        "background": C["text"] if active else C["card"],
        "color": "#ffffff" if active else C["text"],
        "cursor": "pointer", "marginRight": "8px",
    }


def build_time_tabs():
    return html.Div(
        [html.Button(
            label, id={"type": "time-tab", "index": key}, n_clicks=0,
            style=_tab_style(key == DEFAULT_TIME_TAB),
        ) for key, label in TIME_TABS],
        id="time-tabs", style={"display": "flex", "flexWrap": "wrap", "marginBottom": "14px"},
    )


def build_category_chips():
    cats = sorted({e.get("eventType") for e in DATA["events"] if e.get("eventType")})
    chips = []
    for cat in cats:
        meta = category_meta(cat)
        chips.append(html.Button(
            [html.I(className=f"ti {meta['icon']}", style={"marginRight": "5px"}), meta["label"]],
            id={"type": "cat-chip", "index": cat}, n_clicks=0,
            style={
                "fontFamily": FONT_MONO, "fontSize": "12px",
                "padding": "5px 12px", "borderRadius": "20px",
                "border": f"1px solid {meta['color']}55",
                "background": meta["color"] + "18", "color": C["text"],
                "cursor": "pointer", "marginRight": "6px", "marginBottom": "6px",
            },
        ))
    return html.Div(chips, id="category-chips", style={"display": "flex", "flexWrap": "wrap", "marginBottom": "24px"})


# ---------------------------------------------------------------------------
# Tarjeta de evento
# ---------------------------------------------------------------------------
def build_event_card(event):
    etype = event.get("eventType") or "cultural"
    meta = category_meta(etype)
    title = event.get("title") or meta["label"]
    date_raw = str(event.get("eventDate") or "")[:10]
    city = event.get("cityName") or ""
    address = event.get("exactAddress") or ""
    if address.startswith("@"):
        # El LLM a veces devuelve una mención de cuenta (@handle) del caption
        # como si fuera la dirección — suele apuntar al lugar correcto (se
        # puede buscar en Google/Maps), pero no es una dirección postal
        # utilizable tal cual. Se deja visible como pista, sin presentarla
        # como si fuera una calle/número.
        location = f"Revisar en la publicación (mencionan {address})"
    elif address:
        # La dirección exacta ya suele incluir la ciudad (viene del caption
        # o del geotag de Instagram) — no la repetimos aparte.
        location = address
    elif city:
        # Ciudad conocida pero sin dirección exacta — se dice explícitamente
        # en vez de mostrar solo la ciudad como si fuera el dato completo.
        location = f"{city} · dirección exacta no especificada"
    else:
        # No debería ocurrir (la consulta ya filtra eventos sin locationName),
        # pero se deja un texto explícito en vez de ocultar la línea en silencio.
        location = event.get("locationName") or "Ubicación no especificada"
    description = event.get("description") or ""
    author = event.get("sourceAuthor")
    url = event.get("sourcePostUrl")
    price_range = (event.get("priceRange") or "").strip()

    badges = [html.Span([html.I(className=f"ti {meta['icon']}", style={"marginRight": "4px"}), meta["label"]],
                         style={
                             "fontSize": "11px", "padding": "3px 10px", "borderRadius": "20px",
                             "background": meta["color"] + "22", "color": C["text"], "fontWeight": "500",
                         })]
    if price_range:
        # "Gratis"/"Entrada libre" (o cualquier variante) se resalta en verde
        # como antes hacía el booleano isFree; cualquier otro texto de precio
        # (ej. "30€ individual / 50€ grupo") se muestra tal cual llegó de Capa 3.
        is_free_text = price_range.lower() in ("gratis", "entrada libre", "free", "libre")
        badges.append(html.Span(price_range, style={
            "fontSize": "11px", "padding": "3px 10px", "borderRadius": "20px",
            "background": "#4a8c6f22" if is_free_text else meta["color"] + "22",
            "color": C["text"], "marginLeft": "6px",
        }))

    footer_children = []
    if author:
        footer_children.append(html.A(
            f"@{author}", href=instagram_url(author), target="_blank",
            style={"fontSize": "12px", "color": C["blue"], "textDecoration": "none"},
        ))
    if url:
        footer_children.append(html.A(
            ["Publicación original ", html.I(className="ti ti-external-link", style={"fontSize": "11px"})],
            href=url, target="_blank",
            style={"fontSize": "12px", "color": C["sub"], "textDecoration": "none"},
        ))

    return html.Div([
        html.Div([
            *badges,
            html.Span(date_raw or "—", style={
                "fontFamily": FONT_MONO, "fontSize": "11px", "color": C["sub"], "float": "right",
            }),
        ], style={"marginBottom": "10px"}),
        html.P(title, style={
            "fontFamily": FONT_SERIF, "fontSize": "18px", "fontWeight": "600",
            "color": C["text"], "margin": "0 0 8px 0", "lineHeight": "1.3",
        }),
        html.P(description, style={
            "fontFamily": FONT_SANS, "fontSize": "13px", "color": C["sub"],
            "margin": "0 0 10px 0", "lineHeight": "1.5",
        }) if description else None,
        html.P([html.I(className="ti ti-map-pin", style={"marginRight": "4px", "fontSize": "12px"}), location], style={
            "fontFamily": FONT_SANS, "fontSize": "12px", "color": C["sub"], "margin": "0 0 12px 0",
        }) if location else None,
        html.Div(footer_children, style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "center",
            "borderTop": f"1px solid {C['border']}", "paddingTop": "10px",
        }) if footer_children else None,
    ], style={**card_style(padding="18px 20px"), "marginBottom": "0"})


def build_events_grid(events):
    if not events:
        return html.Div("No hay eventos confirmados en este rango — prueba con otro filtro de tiempo.", style={
            "fontFamily": FONT_SANS, "fontSize": "14px", "color": C["sub"],
            "padding": "40px", "textAlign": "center",
        })
    return html.Div(
        [build_event_card(e) for e in events],
        style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(280px, 1fr))", "gap": "16px"},
    )


# ---------------------------------------------------------------------------
# Mapa
# ---------------------------------------------------------------------------
def build_map_panel():
    locs = DATA["locations"]
    if locs:
        lats = [r["lat"] for r in locs if r.get("lat") is not None]
        lons = [r["lon"] for r in locs if r.get("lon") is not None]
        colors = [category_meta(r.get("eventType"))["color"] for r in locs]
        texts = [f"{r.get('name','')}<br>{r.get('eventType','')}" for r in locs]
    else:
        lats, lons, colors, texts = [], [], [], []

    fig = go.Figure(go.Scattermapbox(
        lat=lats, lon=lons, mode="markers",
        marker=go.scattermapbox.Marker(size=12, color=colors, opacity=0.85),
        text=texts, hovertemplate="%{text}<extra></extra>",
    ))
    fig.update_layout(
        mapbox={"style": "open-street-map", "center": {"lat": 48.8566, "lon": 2.3522}, "zoom": 11},
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)", height=380,
    )
    return html.Div([
        html.H3("Mapa de ubicaciones", style={
            "fontFamily": FONT_SERIF, "fontSize": "16px", "color": C["text"], "margin": "0 0 12px 0",
        }),
        dcc.Graph(figure=fig, config={"displayModeBar": False}, style={"borderRadius": "12px", "overflow": "hidden"}),
    ], style={
        "background": C["panel_bg"], "borderRadius": "12px",
        "border": f"1px solid {C['panel_border']}", "padding": "20px", "marginTop": "32px",
    })


layout = html.Div([
    dcc.Store(id="events-store", data=DATA["events"]),
    dcc.Store(id="active-time", data=DEFAULT_TIME_TAB),
    dcc.Store(id="active-cats", data=[]),

    build_header(),
    build_time_tabs(),
    build_category_chips(),
    html.Div(id="events-grid-container", children=build_events_grid(DATA["events"])),
    build_map_panel(),
])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@dash.callback(
    Output("active-time", "data"),
    Input({"type": "time-tab", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def set_time_filter(n_clicks_list):
    # dash.callback_context.triggered_id da directamente el dict de la
    # componente que disparó el callback ({"type": "time-tab", "index": "hoy"})
    # — más confiable que parsear el string prop_id a mano (el approach
    # anterior, con matching por substring, era frágil).
    triggered_id = dash.callback_context.triggered_id
    if not triggered_id:
        return dash.no_update
    return triggered_id["index"]


@dash.callback(
    Output({"type": "time-tab", "index": dash.ALL}, "style"),
    Input("active-time", "data"),
    State({"type": "time-tab", "index": dash.ALL}, "id"),
)
def highlight_active_tab(active_time, ids):
    """Resalta visualmente el tab de tiempo activo. Antes el estilo de los
    botones se fijaba una sola vez al renderizar y nunca se actualizaba —
    por eso "Todos" se veía siempre resaltado sin importar en qué filtro
    estuvieras parado en realidad."""
    return [_tab_style(id_dict["index"] == active_time) for id_dict in ids]


@dash.callback(
    Output("active-cats", "data"),
    Input({"type": "cat-chip", "index": dash.ALL}, "n_clicks"),
    State("active-cats", "data"),
    prevent_initial_call=True,
)
def toggle_category(n_clicks_list, current):
    triggered_id = dash.callback_context.triggered_id
    if not triggered_id:
        return dash.no_update
    cat = triggered_id["index"]
    current = list(current or [])
    if cat in current:
        current.remove(cat)
    else:
        current.append(cat)
    return current


@dash.callback(
    Output("events-grid-container", "children"),
    Input("active-time", "data"),
    Input("active-cats", "data"),
    State("events-store", "data"),
)
def filter_events(time_key, active_cats, events):
    events = events or []
    bounds = date_bucket_bounds()
    lo, hi = bounds.get(time_key or DEFAULT_TIME_TAB, bounds[DEFAULT_TIME_TAB])
    filtered = [e for e in events if lo <= str(e.get("eventDate") or "")[:10] <= hi]
    if active_cats:
        filtered = [e for e in filtered if e.get("eventType") in active_cats]
    return build_events_grid(filtered)
