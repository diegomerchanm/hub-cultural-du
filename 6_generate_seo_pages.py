"""
6_generate_seo_pages.py — Hub Cultural DU

Etapa 2 del plan de arquitectura de URLs/SEO (ver docs/decisions_es.md
DD-065/066): genera páginas HTML ESTÁTICAS reales (contenido presente en la
respuesta inicial, sin depender de que se ejecute JavaScript) para que los
crawlers de IA puedan indexar el catálogo de eventos.

Por qué hace falta esto y no alcanza con URLs bonitas: confirmado con una
búsqueda hecha el 2026-08-27, a mediados de 2026 GPTBot, ClaudeBot y
PerplexityBot NO ejecutan JavaScript -- solo leen el HTML que devuelve el
servidor en la primera respuesta. El sitio interactivo (index.html + app.js)
es 100% renderizado en cliente: un crawler que no ejecuta JS ve el mismo
esqueleto vacío en cualquier URL, sin importar cómo se vea esa URL. Estas
páginas resuelven eso -- contenido real + datos estructurados JSON-LD
(schema.org Event) ya presentes en el HTML, generadas en build time.

Alcance de Etapa 2 (deliberadamente acotado): solo por categoría
(`category`, 11 valores fijos, 100% poblado -- ver DD-066), no por país o
ciudad todavía. DD-066 encontró que 309 de 751 eventos (41%) no tienen NI
geoZone NI cityName -- Diego decidió (2026-08-27) postergar la limpieza de
esa fuente de datos para después de esta entrega, así que páginas por
país/ciudad (Etapa 3) esperan a que ese trabajo se haga. Categoría, en
cambio, es sólida hoy: no hace falta esperar nada para sacarle valor.

Cada página de categoría:
  - HTML semántico real con la lista de eventos próximos de esa categoría
    (nada de innerHTML vía JS) -- fecha, título, lugar, precio, descripción,
    link a la publicación original.
  - JSON-LD (schema.org Event, uno por evento, vía @graph) con los mismos
    datos, para que un motor de IA pueda citar fechas/lugares/precios
    directamente sin tener que "leer" el texto visible.
  - Cada evento linkea de vuelta al sitio interactivo real
    (/?evento=<id>, el deep-link de DD-063) -- estas páginas son una puerta
    de entrada para crawlers/buscadores, no un sitio paralelo.
  - Respeta el mismo gate de photoPermission fail-closed que ya usa el
    sitio interactivo (DD-060): sin permiso explícito, no se expone
    ev.imageUrl, ni siquiera en JSON-LD.

Categorías con pocos eventos próximos no generan página (--min-events,
default 3) -- contenido "delgado" (pocos ítems, casi vacío) es una señal
mala en SEO, mejor no publicar esa página que publicarla vacía. Esto es a
propósito el "sacrifiquemos eventos" que aceptó Diego: una categoría que
hoy no llega al mínimo simplemente no tiene página hasta que sí lo haga.

También genera site/categoria/index.html (hub que linkea a cada categoría)
y site/sitemap.xml. site/robots.txt es estático, no lo genera este script
(ver el archivo directamente).

Etapa 3 (agregado 2026-08-29, DD-072): páginas por zona geográfica dentro de
Francia -- site/francia/index.html (hub), site/francia/ile-de-france/,
site/francia/fuera-de-ile-de-france/, y site/francia/ile-de-france/paris/
(subpágina anidada, único caso con volumen real a nivel ciudad). Basado en
`geoZone`/`cityName` del propio :Event (extraídos por el LLM al crear el
evento), NO en la jerarquía :City/:Country que arma 4_enrich_locations.py vía
Nominatim -- esa jerarquía solo decide si el evento tiene pin de mapa (DD-045:
"aparecer en el sitio y tener pin en el mapa son dos cosas independientes"),
nunca si aparece en estas páginas. Por eso un fallo del geocodificador (visto
el 2026-08-28: 0/276 Location geocodificadas en una corrida real de Diego,
causa no confirmada) no bloquea esta etapa. Deliberadamente SIN página para
"Fuera de Francia" (16 eventos del catálogo vivo, dispersos en ciudades sin
volumen individual -- Madrid 4, el resto 1 cada una) -- decisión explícita de
Diego: en vez de forzar un pooling genérico ahí, prefiere que una entrega
futura arme una estructura propia por país (ej. /es/madrid/, /de/berlin/)
cuando haga falta. Ver docs/decisions_es.md DD-072 para el análisis completo
de por qué un menú tipo Meetup con ciudades no calzaba con los datos reales
(solo Paris tiene volumen; Marseille/Madrid/Montpellier rondan 3-4).

No necesita Neo4j -- lee directo site/data.json, que ya tiene todo lo
necesario (mismo espíritu que otros scripts client-side del proyecto).

Uso:
    python 6_generate_seo_pages.py --dry-run   # lista qué generaría, no escribe nada
    python 6_generate_seo_pages.py             # escribe los archivos en site/

Correr DESPUÉS de 5_export_dashboard_data.py (necesita site/data.json al
día) y ANTES de `cd site && npx wrangler deploy`.
"""
import json
from datetime import date
from html import escape
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)

REPO_ROOT = Path(__file__).resolve().parent
SITE_DIR = REPO_ROOT / "site"
DATA_FILE = SITE_DIR / "data.json"

# Debe coincidir con el dominio real del sitio -- hoy es el subdominio
# gratuito de Cloudflare Workers (ver captura que mandó Diego, 2026-08-27).
# Actualizar acá el día que haya un dominio propio (site/wrangler.jsonc
# "Add Domain") -- afecta el canonical/JSON-LD/sitemap, no la navegación
# real del sitio, así que no rompe nada si queda desactualizado, solo hace
# que esos metadatos apunten al subdominio viejo.
BASE_URL = "https://hub-cultural-du.diegomerchanm.workers.dev"

# Mismo diccionario que CATEGORY_META en site/app.js -- duplicado a
# propósito (este script es Python, ese es JS, no hay forma limpia de
# compartir sin agregar un paso de build nuevo) -- si se agrega/renombra una
# categoría en app.js, actualizar acá también. label_plural es para el
# título de la página ("Gastronomía en Francia" lee mejor que "Gastronomico
# en Francia").
CATEGORY_META = {
    "gastronomico":  "Gastronomía",
    "institucional": "Eventos institucionales",
    "visual":        "Artes visuales",
    "comunitario":   "Comunidad",
    "musical":        "Música",
    "formacion":     "Talleres y formación",
    "audiovisual":   "Cine",
    "escenico":      "Teatro y danza",
    "festival":      "Festivales",
    "academico":     "Charlas y conferencias",
    "politico":      "Eventos cívicos",
}

PAGE_CSS = """
body{font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:800px;margin:0 auto;
  padding:24px;color:#232730;line-height:1.5}
a{color:#2f5aa8}
header{margin-bottom:24px}
.brand{font-weight:700;font-size:18px;text-decoration:none;color:inherit}
nav.crumb{font-size:13px;color:#6b6a63;margin-bottom:16px}
nav.crumb a{color:#6b6a63}
h1{font-size:26px;margin-bottom:4px}
.subtitle{color:#6b6a63;font-size:14px;margin-bottom:24px}
.ev{border-top:1px solid #e3e1da;padding:16px 0}
.ev h2{font-size:17px;margin:0 0 4px}
.ev h2 a{color:inherit;text-decoration:none}
.ev .meta{font-size:13px;color:#6b6a63;margin:0 0 6px}
.ev p{font-size:14px;margin:0 0 6px}
footer{margin-top:32px;padding-top:16px;border-top:1px solid #e3e1da;font-size:12px;color:#6b6a63}
.cat-grid{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.cat-grid a{display:block;padding:10px 16px;border:1px solid #e3e1da;border-radius:8px;text-decoration:none}
"""

FOOTER_TEXT = (
    "Hub Cultural · Diáspora latinoamericana en Francia · Eventos detectados automáticamente "
    "a partir de Instagram — verificá la fuente antes de asistir."
)


def load_events():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return data.get("events", [])


def is_upcoming(ev) -> bool:
    d = ev.get("eventDate")
    if not d:
        return False
    try:
        return date.fromisoformat(d[:10]) >= date.today()
    except ValueError:
        return False


# Mismo diccionario que CITY_SYNONYMS en site/app.js (DD-066) -- duplicado
# por el mismo motivo que CATEGORY_META arriba (Python vs JS, sin paso de
# build compartido). Sin esto, estas páginas estáticas reintroducirían la
# misma inconsistencia que DD-066 arregló del lado del sitio interactivo
# (Paris/París, Marseille/Marsella, etc. mostrados como ciudades distintas)
# porque leen site/data.json crudo, no pasan por app.js.
CITY_SYNONYMS = {
    "París": "Paris",
    "Boulogne Billancourt": "Boulogne-Billancourt",
    "Montréal": "Montreal",
    "Marsella": "Marseille",
    "Mexico City": "Ciudad de México",
    "Cartagena de Indias": "Cartagena",
    "Venecia": "Venice",
    "null": None,
}


def canonical_city(raw):
    if not raw:
        return raw
    return CITY_SYNONYMS.get(raw, raw)


# Mismo diccionario que GEO_ZONE_SYNONYMS en site/app.js (DD-056/DD-072) --
# duplicado por el mismo motivo que CITY_SYNONYMS arriba. Sin esto, esta
# página estática contaría "Île-de-France" y "Francia (fuera de Île-de-
# France)"/"Francia fuera de IDF" como zonas distintas cuando son la misma.
GEO_ZONE_SYNONYMS = {"Francia (fuera de Île-de-France)": "Francia fuera de IDF"}


def canonical_geo_zone(raw):
    if not raw:
        return raw
    return GEO_ZONE_SYNONYMS.get(raw, raw)


# Etapa 3 (DD-072): solo estas dos zonas tienen página propia -- "Fuera de
# Francia" y "No confirmado" quedan fuera de las páginas de geo a propósito
# (ver docstring del módulo). Cada tupla es (slug de URL, label para
# mostrar, valor canónico de geoZone contra el que se filtra) -- separados
# porque el valor de geoZone en los datos ("Francia fuera de IDF") no es un
# buen título de página ("Francia fuera de Île-de-France" lee mejor).
FRANCIA_ZONES = [
    ("ile-de-france", "Île-de-France", "Île-de-France"),
    ("fuera-de-ile-de-france", "Francia fuera de Île-de-France", "Francia fuera de IDF"),
]
PARIS_LABEL = "Paris"


def event_location_text(ev) -> str:
    # Fallback en cadena hasta "Francia" a secas (2026-08-27, encontrado con
    # el Rich Results Test de Google: "location" es obligatorio para que un
    # Event sea válido -- sin este fallback, 6 de los primeros 32 eventos
    # probados quedaban marcados como error crítico por no tener NINGÚN dato
    # de ubicación, el mismo 41% sin geoZone ni cityName ya documentado en
    # DD-066). "Francia" es verdadero -- sabemos que el evento es ahí, no
    # inventa precisión que no tenemos -- y esto es un fallback del
    # generador, no una limpieza del origen de datos (que Diego pidió
    # postergar), así que entra en el alcance de esta etapa.
    return (
        ev.get("exactAddress")
        or ev.get("locationName")
        or canonical_city(ev.get("cityName"))
        or ev.get("geoZone")
        or "Francia"
    )


def event_url(ev) -> str:
    return f"{BASE_URL}/?evento={ev['id']}"


def event_jsonld(ev) -> dict:
    item = {
        "@type": "Event",
        "name": ev.get("title") or "",
        "startDate": ev.get("eventDate"),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "url": event_url(ev),
    }
    if ev.get("description"):
        item["description"] = ev["description"]
    loc = event_location_text(ev)
    if loc:
        item["location"] = {"@type": "Place", "name": loc, "address": ev.get("exactAddress") or loc}
    # DD-060: mismo gate fail-closed que el sitio interactivo -- sin
    # photoPermission === True explícito, nunca se expone imageUrl, ni
    # siquiera acá.
    if ev.get("photoPermission") is True and ev.get("imageUrl"):
        item["image"] = ev["imageUrl"]
    if ev.get("isFree"):
        item["isAccessibleForFree"] = True
    if ev.get("sourceAuthor"):
        item["organizer"] = {"@type": "Organization", "name": f"@{ev['sourceAuthor']}"}
    return item


def event_html(ev) -> str:
    loc = event_location_text(ev)
    price = f" · {escape(ev['priceRange'])}" if ev.get("priceRange") else ""
    desc = escape((ev.get("description") or "")[:280])
    src_link = (
        f'<p><a href="{escape(ev["sourcePostUrl"])}" rel="noopener">Publicación original en Instagram</a></p>'
        if ev.get("sourcePostUrl") else ""
    )
    return f"""  <article class="ev">
    <h2><a href="{escape(event_url(ev))}">{escape(ev.get("title") or "(sin título)")}</a></h2>
    <p class="meta">{escape(ev.get("eventDate") or "")}{" · " + escape(loc) if loc else ""}{price}</p>
    <p>{desc}</p>
    {src_link}
  </article>"""


def render_listing_page(*, canonical: str, breadcrumb_html: str, h1: str,
                         meta_description: str, events: list, note: str,
                         live_query: str) -> str:
    """Template compartido por páginas de categoría (DD-067) y de geo
    (DD-072) -- misma estructura (HTML semántico + JSON-LD @graph), lo único
    que cambia entre una y otra es breadcrumb/título/descripción/link de
    vuelta al sitio interactivo. `live_query` (ej. "?tema=visual",
    "?geo=ile-de-france", "?buscar=Paris") es el deep-link nuevo de DD-072
    (ver GEO_SLUG_TO_ZONE/applyInitialFiltersFromUrl en site/app.js) --
    string vacío cae al link genérico a "/" sin filtro (caso de "Otros",
    donde no hay un único tema/zona que aplicarle al link)."""
    jsonld = {"@context": "https://schema.org", "@graph": [event_jsonld(ev) for ev in events]}
    events_html = "\n".join(event_html(ev) for ev in events)
    note_html = f'<p class="subtitle">{escape(note)}</p>' if note else ""
    live_link = f"/{live_query}" if live_query else "/"
    live_link_text = "Ver estos eventos, con filtros y mapa, en el sitio interactivo →" if live_query else "Ver el sitio completo, con filtros y mapa →"
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(h1)} — Hub Cultural</title>
<meta name="description" content="{escape(meta_description)}">
<link rel="canonical" href="{canonical}">
<style>{PAGE_CSS}</style>
</head>
<body>
<header><a class="brand" href="/">🗺️ Hub Cultural</a></header>
<nav class="crumb">{breadcrumb_html}</nav>
<h1>{escape(h1)}</h1>
<p class="subtitle">{len(events)} eventos próximos detectados automáticamente a partir de Instagram.</p>
{note_html}
{events_html}
<footer>
  <p>{FOOTER_TEXT}</p>
  <p><a href="{live_link}">{live_link_text}</a></p>
</footer>
<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>
</body>
</html>
"""


def render_category_page(slug: str, label: str, events: list, note: str = "") -> str:
    live_query = f"?tema={slug}" if slug != OTROS_SLUG else ""
    return render_listing_page(
        canonical=f"{BASE_URL}/categoria/{slug}/",
        breadcrumb_html=f'<a href="/">Inicio</a> › <a href="/categoria/">Categorías</a> › {escape(label)}',
        h1=f"{label} — eventos de la diáspora latinoamericana en Francia",
        meta_description=f"{len(events)} eventos próximos de {label.lower()} de la diáspora latinoamericana en Francia: fechas, lugares y precios.",
        events=events, note=note, live_query=live_query,
    )


def render_geo_page(*, path: str, label: str, events: list, breadcrumb_html: str,
                     live_query: str, note: str = "") -> str:
    return render_listing_page(
        canonical=f"{BASE_URL}/francia/{path}/",
        breadcrumb_html=breadcrumb_html,
        h1=f"{label} — eventos de la diáspora latinoamericana",
        meta_description=f"{len(events)} eventos próximos de la diáspora latinoamericana en {label}: fechas, lugares y precios.",
        events=events, note=note, live_query=live_query,
    )


def render_francia_hub_page(entries: list) -> str:
    """entries: lista de (slug, label, n) -- mismas dos zonas de
    FRANCIA_ZONES, nunca incluye Paris (subpágina anidada de
    ile-de-france, no un tercer link al mismo nivel)."""
    links = "\n".join(
        f'  <a href="/francia/{slug}/">{escape(label)} <span style="color:#6b6a63">({n})</span></a>'
        for slug, label, n in entries
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Francia — eventos por zona — Hub Cultural</title>
<meta name="description" content="Eventos culturales de la diáspora latinoamericana en Francia, organizados por zona geográfica.">
<link rel="canonical" href="{BASE_URL}/francia/">
<style>{PAGE_CSS}</style>
</head>
<body>
<header><a class="brand" href="/">🗺️ Hub Cultural</a></header>
<nav class="crumb"><a href="/">Inicio</a> › Francia</nav>
<h1>Francia — eventos por zona</h1>
<div class="cat-grid">
{links}
</div>
<footer><p>{FOOTER_TEXT}</p></footer>
</body>
</html>
"""


def render_hub_page(entries: list) -> str:
    links = "\n".join(
        f'  <a href="/categoria/{slug}/">{escape(label)} <span style="color:#6b6a63">({n})</span></a>'
        for slug, label, n in entries
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Categorías de eventos — Hub Cultural</title>
<meta name="description" content="Eventos culturales de la diáspora latinoamericana en Francia, organizados por categoría.">
<link rel="canonical" href="{BASE_URL}/categoria/">
<style>{PAGE_CSS}</style>
</head>
<body>
<header><a class="brand" href="/">🗺️ Hub Cultural</a></header>
<nav class="crumb"><a href="/">Inicio</a> › Categorías</nav>
<h1>Categorías de eventos</h1>
<div class="cat-grid">
{links}
</div>
<footer><p>{FOOTER_TEXT}</p></footer>
</body>
</html>
"""


INDEX_FILE = SITE_DIR / "index.html"
LINKS_START = "<!-- SEO_CATEGORY_LINKS_START -->"
LINKS_END = "<!-- SEO_CATEGORY_LINKS_END -->"
GEO_LINKS_START = "<!-- SEO_GEO_LINKS_START -->"
GEO_LINKS_END = "<!-- SEO_GEO_LINKS_END -->"


def _update_marked_block(start_marker: str, end_marker: str, links_html: str, warn_label: str):
    """Reescribe un bloque marcado de index.html entre `start_marker`/
    `end_marker` -- mismo mecanismo para categorías (DD-067) y geo (DD-072),
    en bloques SEPARADOS a propósito (no un solo bloque compartido) para que
    cada uno se pueda regenerar independiente sin pisar al otro. index.html
    no ejecuta JS al servirse, así que estos <a href> reales son la única
    forma de que un crawler descubra estas páginas siguiendo links -- sin
    esto, solo serían alcanzables si alguien ya conoce la URL exacta (o vía
    sitemap.xml, que no todos los crawlers de IA consultan)."""
    html = INDEX_FILE.read_text(encoding="utf-8")
    if start_marker not in html or end_marker not in html:
        print(f"⚠️  No encontré los marcadores {warn_label} en index.html -- no actualicé esos links.")
        return
    before, rest = html.split(start_marker, 1)
    _, after = rest.split(end_marker, 1)
    INDEX_FILE.write_text(f"{before}{start_marker}{links_html}{end_marker}{after}", encoding="utf-8")


def update_index_links(entries: list):
    """Categorías -- ver docstring de _update_marked_block."""
    links = " · ".join(f'<a href="/categoria/{slug}/">{escape(label)}</a>' for slug, label, _ in entries)
    links = f'<a href="/categoria/">Categorías</a> · {links}' if links else '<a href="/categoria/">Categorías</a>'
    _update_marked_block(LINKS_START, LINKS_END, links, "SEO_CATEGORY_LINKS")


def update_index_geo_links(entries: list):
    """Zonas de Francia (DD-072) -- ver docstring de _update_marked_block.
    `entries` son (slug, label, n) de FRANCIA_ZONES más, si tiene página,
    Paris con su slug anidado "ile-de-france/paris"."""
    links = " · ".join(f'<a href="/francia/{slug}/">{escape(label)}</a>' for slug, label, _ in entries)
    links = f'<a href="/francia/">Francia</a> · {links}' if links else '<a href="/francia/">Francia</a>'
    _update_marked_block(GEO_LINKS_START, GEO_LINKS_END, links, "SEO_GEO_LINKS")


def render_sitemap(urls: list) -> str:
    items = "\n".join(f"  <url><loc>{escape(u)}</loc></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}\n</urlset>\n'


# "Otros" (2026-08-27, pedido de Diego): en vez de simplemente no generar
# página para una categoría que no llega al mínimo, sus eventos se juntan
# acá en una página temporal compartida. "Temporal" es literal, no solo de
# palabra -- no hay ningún estado guardado en ningún lado que recuerde qué
# categorías estuvieron alguna vez en Otros: cada corrida recalcula todo
# desde cero a partir de los conteos actuales, así que en cuanto una
# categoría junte sola --min-events eventos, esa misma corrida ya le arma su
# propia página y deja de aportarle nada a Otros. Mismo criterio de
# contenido-no-delgado que las páginas normales: si lo agrupado ACÁ tampoco
# llega al mínimo, tampoco se genera Otros -- no tiene sentido mudar el
# problema de contenido vacío de un lado a otro.
OTROS_SLUG = "otros"
OTROS_LABEL = "Otras categorías"
OTROS_NOTE = (
    "Estos eventos son de categorías que todavía no juntan suficientes eventos próximos "
    "para tener su propia página -- en cuanto la tengan, se muestran ahí en vez de acá."
)


@app.command()
def main(
    min_events: int = typer.Option(3, "--min-events", help="No genera página (ni siquiera 'Otros') por debajo de este mínimo de eventos próximos -- evita contenido delgado"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo lista qué generaría, no escribe nada"),
):
    if not DATA_FILE.exists():
        raise typer.Exit(f"No existe {DATA_FILE} -- corré primero 5_export_dashboard_data.py")

    events = load_events()
    upcoming_all = [ev for ev in events if is_upcoming(ev)]
    upcoming = [ev for ev in upcoming_all if ev.get("category")]

    by_category = {}
    for ev in upcoming:
        by_category.setdefault(ev["category"], []).append(ev)
    for evs in by_category.values():
        evs.sort(key=lambda e: e.get("eventDate") or "")

    print(f"📊 {len(upcoming)} eventos próximos con categoría, sobre {len(events)} eventos totales.\n")

    # (slug, label, events, note) -- una tupla por página a generar,
    # incluyendo "Otros" si corresponde. Se arma toda la lista ANTES de
    # escribir nada, así hub/sitemap/footer siempre reflejan exactamente lo
    # que se generó, dry-run o no.
    pages = []
    otros_events = []
    for slug, label in CATEGORY_META.items():
        evs = by_category.get(slug, [])
        n = len(evs)
        if n < min_events:
            print(f"↪️  {slug:<14} {n:>3} eventos — por debajo del mínimo ({min_events}), va a \"{OTROS_LABEL}\" (temporal)")
            otros_events.extend(evs)
            continue
        print(f"✅ {slug:<14} {n:>3} eventos — {'(dry-run, no escribe)' if dry_run else 'generando página'}")
        pages.append((slug, label, evs, ""))

    otros_events.sort(key=lambda e: e.get("eventDate") or "")
    if otros_events:
        if len(otros_events) >= min_events:
            print(f"✅ {OTROS_SLUG:<14} {len(otros_events):>3} eventos combinados — {'(dry-run, no escribe)' if dry_run else 'generando página temporal'}")
            pages.append((OTROS_SLUG, OTROS_LABEL, otros_events, OTROS_NOTE))
        else:
            print(f"⏭️  {OTROS_SLUG:<14} {len(otros_events):>3} eventos combinados — todavía por debajo del mínimo, sin página")

    hub_entries = [(slug, label, len(evs)) for slug, label, evs, _ in pages]

    # ── Etapa 3 (DD-072): páginas de geo, en paralelo a las de categoría ──
    # `upcoming_all` (sin requisito de category) porque geo es un eje
    # independiente -- un evento sin category igual puede tener geoZone.
    print()
    idf_events, fuera_events = [], []
    for ev in upcoming_all:
        zone = canonical_geo_zone(ev.get("geoZone"))
        if zone == "Île-de-France":
            idf_events.append(ev)
        elif zone == "Francia fuera de IDF":
            fuera_events.append(ev)
        # "Fuera de Francia" / "No confirmado" / sin geoZone: fuera de
        # alcance de estas páginas a propósito, ver docstring del módulo.
    idf_events.sort(key=lambda e: e.get("eventDate") or "")
    fuera_events.sort(key=lambda e: e.get("eventDate") or "")
    paris_events = sorted(
        (ev for ev in idf_events if canonical_city(ev.get("cityName")) == PARIS_LABEL),
        key=lambda e: e.get("eventDate") or "",
    )

    zone_events = {"ile-de-france": idf_events, "fuera-de-ile-de-france": fuera_events}
    geo_pages = []  # (path, label, events, breadcrumb_html, live_query, note)
    for slug, label, _geo_value in FRANCIA_ZONES:
        evs = zone_events[slug]
        n = len(evs)
        if n < min_events:
            print(f"⏭️  francia/{slug:<24} {n:>3} eventos — por debajo del mínimo ({min_events}), sin página")
            continue
        print(f"✅ francia/{slug:<24} {n:>3} eventos — {'(dry-run, no escribe)' if dry_run else 'generando página'}")
        breadcrumb = f'<a href="/">Inicio</a> › <a href="/francia/">Francia</a> › {escape(label)}'
        geo_pages.append((slug, label, evs, breadcrumb, f"?geo={slug}", ""))

    n_paris = len(paris_events)
    if n_paris >= min_events and "ile-de-france" in {p[0] for p in geo_pages}:
        print(f"✅ francia/ile-de-france/paris     {n_paris:>3} eventos — {'(dry-run, no escribe)' if dry_run else 'generando página'}")
        paris_breadcrumb = (
            '<a href="/">Inicio</a> › <a href="/francia/">Francia</a> › '
            '<a href="/francia/ile-de-france/">Île-de-France</a> › Paris'
        )
        paris_note = "Subconjunto de Île-de-France específicamente en París — ver también todos los eventos de la región completa en la página de Île-de-France."
        geo_pages.append(("ile-de-france/paris", PARIS_LABEL, paris_events, paris_breadcrumb, "?buscar=Paris", paris_note))
    elif n_paris:
        print(f"⏭️  francia/ile-de-france/paris     {n_paris:>3} eventos — por debajo del mínimo ({min_events}) o sin página de Île-de-France, sin página")

    geo_hub_entries = [(slug, label, len(evs)) for slug, label, evs, _, _, _ in geo_pages if slug in zone_events]

    sitemap_urls = (
        [f"{BASE_URL}/", f"{BASE_URL}/categoria/"] + [f"{BASE_URL}/categoria/{slug}/" for slug, *_ in pages]
        + [f"{BASE_URL}/francia/"] + [f"{BASE_URL}/francia/{path}/" for path, *_ in geo_pages]
    )

    written = 0
    if not dry_run:
        for slug, label, evs, note in pages:
            out_dir = SITE_DIR / "categoria" / slug
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "index.html").write_text(render_category_page(slug, label, evs, note), encoding="utf-8")
            written += 1
        for path, label, evs, breadcrumb, live_query, note in geo_pages:
            out_dir = SITE_DIR / "francia" / path
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "index.html").write_text(
                render_geo_page(path=path, label=label, events=evs, breadcrumb_html=breadcrumb, live_query=live_query, note=note),
                encoding="utf-8",
            )
            written += 1

    if dry_run:
        print(f"\n[dry-run] Generaría {len(pages)} páginas de categoría + {len(geo_pages)} páginas de geo + hubs + sitemap. Nada escrito.")
        return

    (SITE_DIR / "categoria").mkdir(exist_ok=True)
    (SITE_DIR / "categoria" / "index.html").write_text(render_hub_page(hub_entries), encoding="utf-8")
    (SITE_DIR / "francia").mkdir(exist_ok=True)
    (SITE_DIR / "francia" / "index.html").write_text(render_francia_hub_page(geo_hub_entries), encoding="utf-8")
    (SITE_DIR / "sitemap.xml").write_text(render_sitemap(sitemap_urls), encoding="utf-8")
    update_index_links(hub_entries)
    update_index_geo_links([(path, label, len(evs)) for path, label, evs, _, _, _ in geo_pages])

    print(f"\n✅ {written} páginas ({len(pages)} categoría + {len(geo_pages)} geo) + hubs (categoria/, francia/) + sitemap.xml + links actualizados en index.html")


if __name__ == "__main__":
    app()
