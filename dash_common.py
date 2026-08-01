"""
dash_common.py — Hub Cultural DU
Tokens de diseño, conexión a Neo4j y helpers compartidos entre las páginas
del dashboard (5_visualize_dashboard.py + pages/*.py). Nada aquí depende de
Dash Pages — es solo el módulo común importado por ambos lados.
"""

import os
from collections import Counter
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Neo4j driver (opcional — todo lo que use fetch_data() cae a listas vacías
# si no hay conexión, para que el dashboard nunca truene por eso).
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

# Categorías del pipeline -> color + etiqueta pública (menú/chips).
# El nombre interno (category en Neo4j) sigue siendo el que ya usa
# 4_enrich_events_extract.py — esto es solo la capa de presentación.
CATEGORY_META = {
    "gastronomico":  {"label": "Gastronomía",           "color": "#b0384a", "icon": "ti-tools-kitchen-2"},
    "institucional": {"label": "Institucional",          "color": "#2f5aa8", "icon": "ti-building-bank"},
    "visual":        {"label": "Arte y exposiciones",    "color": "#7b5ea7", "icon": "ti-palette"},
    "comunitario":   {"label": "Comunidad",              "color": "#4a8c6f", "icon": "ti-users"},
    "musical":       {"label": "Música",                 "color": "#a5603a", "icon": "ti-music"},
    "formacion":     {"label": "Talleres",                "color": "#c49a2c", "icon": "ti-school"},
    "audiovisual":   {"label": "Cine",                   "color": "#5f6b8c", "icon": "ti-movie"},
    "escenico":      {"label": "Teatro y danza",         "color": "#8f8d84", "icon": "ti-masks-theater"},
    "festival":      {"label": "Festivales",              "color": "#e0b02e", "icon": "ti-confetti"},
    "academico":     {"label": "Charlas y conferencias", "color": "#6b8c4a", "icon": "ti-microphone-2"},
    "politico":      {"label": "Cívico",                 "color": "#8f8d84", "icon": "ti-ballot"},
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


def category_meta(cat):
    key = (cat or "").lower()
    return CATEGORY_META.get(key, {"label": (cat or "Cultural").title(), "color": C["sub"], "icon": "ti-star"})


def event_color(etype):
    return category_meta(etype)["color"]


def fmt_num(n):
    if n is None:
        return "0"
    return f"{int(n):,}".replace(",", " ")


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
# Data fetching
# ---------------------------------------------------------------------------

def _run(session, query, **params):
    return list(session.run(query, **params))


WEEKDAY_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def compute_event_insights(events):
    """Estadísticas curadas calculadas en Python sobre la lista de eventos
    'limpios' ya traída por fetch_data() — sin Cypher adicional, para no
    arriesgar que un query nuevo tumbe todo el fetch si algo no calza con
    el esquema real. Pensado para un público no analista: cada número debe
    poder leerse como una frase, no como una gráfica que hay que interpretar."""
    cat_counter = Counter()
    weekday_counter = Counter()
    month_counter = Counter()
    organizer_counter = Counter()
    location_counter = Counter()

    for e in events:
        cat = e.get("eventType")
        if cat:
            cat_counter[cat] += 1
        author = e.get("sourceAuthor")
        if author:
            organizer_counter[author] += 1
        loc = e.get("locationName")
        if loc:
            location_counter[loc] += 1
        raw_date = str(e.get("eventDate") or "")[:10]
        if raw_date:
            try:
                d = datetime.strptime(raw_date, "%Y-%m-%d").date()
                weekday_counter[WEEKDAY_ES[d.weekday()]] += 1
                month_counter[raw_date[:7]] += 1
            except ValueError:
                pass

    return {
        "top_category": cat_counter.most_common(1)[0] if cat_counter else None,
        "category_breakdown": cat_counter.most_common(),
        "top_weekday": weekday_counter.most_common(1)[0] if weekday_counter else None,
        "weekday_breakdown": [(wd, weekday_counter.get(wd, 0)) for wd in WEEKDAY_ES],
        "top_organizer": organizer_counter.most_common(1)[0] if organizer_counter else None,
        "top_organizers": organizer_counter.most_common(5),
        "top_location": location_counter.most_common(1)[0] if location_counter else None,
        "monthly_trend": sorted(month_counter.items()),
    }


def fetch_data():
    """Trae todos los datos del dashboard desde Neo4j. Cae a listas vacías
    si no hay conexión — el dashboard siempre debe poder arrancar."""
    empty = {
        "events": [],
        "network": [],
        "locations": [],
        "top_accounts": [],
        "top_hashtags": [],
        "top_central_accounts": [],
        "top_bridge_accounts": [],
        "insights": compute_event_insights([]),
        "stats": {"accounts": 0, "posts": 0, "events": 0, "hashtags": 0, "upcoming_clean": 0},
    }
    if driver is None:
        return empty
    try:
        with driver.session() as s:
            # Eventos "limpios": invitación real confirmada por Capa 3,
            # con fecha y ubicación dentro de rango sensato — nada de los
            # ~24 casos de fecha absurda ni de los eventos pre-Capa 3.
            events = _run(s, """
                MATCH (e:Event)
                WHERE NOT 'Rejected' IN labels(e)
                  AND e.isPublicInvitation = true
                  AND e.isUpcoming = true
                  AND e.eventDate IS NOT NULL AND e.eventDate <> ''
                  AND e.eventDate >= '2026-01-01' AND e.eventDate <= '2027-12-31'
                  AND e.locationName IS NOT NULL AND e.locationName <> ''
                RETURN e.id AS id, e.title AS title, e.category AS eventType,
                       e.eventDate AS eventDate, e.locationName AS locationName,
                       e.cityName AS cityName, e.exactAddress AS exactAddress,
                       e.hotnessScore AS hotnessScore, e.description AS description,
                       e.isFree AS isFree, e.sourcePostUrl AS sourcePostUrl,
                       e.sourceAuthor AS sourceAuthor
                ORDER BY e.eventDate ASC, e.hotnessScore DESC
                LIMIT 100
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

            # Insights de grafo (relevancia cultural, cuentas puente). En try/except
            # propio: si algún nombre de propiedad no calza con lo que ya escribió
            # run_gds_algorithms.py en tu instancia real, esto solo se queda vacío
            # en vez de tumbar todo fetch_data().
            try:
                top_central_accounts = [dict(r) for r in _run(s, """
                    MATCH (a:Account:Public)
                    WHERE a.culturalRelevanceScore IS NOT NULL
                    RETURN a.username AS username, a.culturalRelevanceScore AS score,
                           a.followersCount AS followers
                    ORDER BY a.culturalRelevanceScore DESC
                    LIMIT 5
                """)]
            except Exception:
                top_central_accounts = []

            try:
                top_bridge_accounts = [dict(r) for r in _run(s, """
                    MATCH (a:Account:Public)
                    WHERE a.betweennessPct IS NOT NULL
                    RETURN a.username AS username, a.betweennessPct AS betweennessPct
                    ORDER BY a.betweennessPct DESC
                    LIMIT 5
                """)]
            except Exception:
                top_bridge_accounts = []

            stats = {
                "accounts":  _run(s, "MATCH (a:Account) RETURN count(a) AS n")[0]["n"],
                "posts":     _run(s, "MATCH (p:Post) RETURN count(p) AS n")[0]["n"],
                "events":    _run(s, "MATCH (e:Event) WHERE NOT 'Rejected' IN labels(e) RETURN count(e) AS n")[0]["n"],
                "hashtags":  _run(s, "MATCH (h:Hashtag) RETURN count(h) AS n")[0]["n"],
                "upcoming_clean": _run(s, """
                    MATCH (e:Event)
                    WHERE NOT 'Rejected' IN labels(e)
                      AND e.isPublicInvitation = true AND e.isUpcoming = true
                      AND e.eventDate IS NOT NULL AND e.eventDate <> ''
                      AND e.eventDate >= '2026-01-01' AND e.eventDate <= '2027-12-31'
                      AND e.locationName IS NOT NULL AND e.locationName <> ''
                    RETURN count(e) AS n
                """)[0]["n"],
            }
            events_list = [dict(r) for r in events]
            return {
                "events": events_list,
                "network": [dict(r) for r in network],
                "locations": [dict(r) for r in locations],
                "top_accounts": [dict(r) for r in top_accounts],
                "top_hashtags": [dict(r) for r in top_hashtags],
                "top_central_accounts": top_central_accounts,
                "top_bridge_accounts": top_bridge_accounts,
                "insights": compute_event_insights(events_list),
                "stats": stats,
            }
    except Exception:
        return empty


DATA = fetch_data()


def instagram_url(username):
    if not username:
        return None
    return f"https://instagram.com/{username}"


def date_bucket_bounds():
    """Rangos ISO (string) para 'hoy' / 'esta semana' / 'este mes', anclados
    a la fecha real del sistema (no a una fecha fija del estudio) — el
    dashboard es una vista en vivo, a diferencia del análisis del mémoire."""
    today = date.today()
    week_end = today + timedelta(days=(6 - today.weekday()))
    month_end = date(today.year + (today.month == 12), (today.month % 12) + 1, 1) - timedelta(days=1)
    yesterday = today - timedelta(days=1)
    return {
        "hoy":      (today.isoformat(), today.isoformat()),
        "semana":   (today.isoformat(), week_end.isoformat()),
        "mes":      (today.isoformat(), month_end.isoformat()),
        # Separados explícitamente en vez de un solo "todos" ambiguo: "próximos"
        # es todo lo que falta por venir (sin techo de fecha), "pasados" es todo
        # lo anterior a hoy (sin piso de fecha, salvo el que ya impone la query
        # de fetch_data en Neo4j). Un "todos" único mezclaba ambos sentidos y
        # confundía qué se estaba mirando.
        "proximos": (today.isoformat(), "2099-12-31"),
        "pasados":  ("0000-01-01", yesterday.isoformat()),
    }
