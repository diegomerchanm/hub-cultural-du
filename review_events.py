"""
review_events.py — Hub Cultural DU
Staging zone: revisión interactiva de eventos nuevos (:PendingReview) antes de
que salgan al sitio. Aprobar / editar / rechazar. "Rechazar" nunca borra el
nodo — solo lo marca :Rejected (mismo mecanismo que 5_export_dashboard_data.py
y export_events_excel.py ya usan para excluir eventos del sitio/export).

Desde 2026-08-27 (pedido de Diego) también incluye, en una segunda pestaña,
el panel de control de la pipeline (control_panel.py) -- correr cualquier
script del proyecto desde acá, con su propio dry-run, logs en vivo, e
historial. Vive en un módulo aparte a propósito, para no mezclar dos
responsabilidades bien distintas en un solo archivo; esta pestaña solo lo
importa y lo llama.

Uso:
    streamlit run review_events.py

No forma parte del pipeline batch — es una herramienta manual para Diego.
"""
import os

import streamlit as st
from dotenv import load_dotenv
from neo4j import GraphDatabase

from control_panel import render_control_panel

load_dotenv()

st.set_page_config(page_title="Hub Cultural — Panel de Diego", page_icon="🗂️", layout="centered")

CATEGORY_LABELS = {
    "gastronomico": "🍽️ Gastronómico",
    "institucional": "🏛️ Institucional",
    "visual": "🎨 Visual",
    "comunitario": "🤝 Comunitario",
    "musical": "🎵 Musical",
    "formacion": "📚 Formación",
    "audiovisual": "🎬 Audiovisual",
    "escenico": "🎭 Escénico",
    "festival": "🎉 Festival",
    "academico": "🎓 Académico",
    "politico": "📢 Político",
}


@st.cache_resource
def get_driver():
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI"),
        auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
    )
    driver.verify_connectivity()
    return driver


GEO_LABELS = {
    "Île-de-France": "🇫🇷 Île-de-France",
    "Francia fuera IDF": "🇫🇷 Francia (fuera IDF)",
    "Fuera de Francia": "🌍 Fuera de Francia",
}
GEO_SIN_DATO = "— sin geoZone —"


def fetch_pending():
    query = """
        MATCH (e:Event)
        WHERE 'PendingReview' IN labels(e)
        RETURN e.id AS id, e.title AS title, e.description AS description,
               e.type AS type, e.eventDate AS eventDate, e.locationName AS locationName,
               e.cityName AS cityName, e.priceRange AS priceRange,
               e.sourceAuthor AS sourceAuthor, e.sourcePostUrl AS sourcePostUrl,
               e.hotnessScore AS hotnessScore, e.eventScore AS eventScore,
               e.confidence AS confidence, e.layer1Score AS layer1Score,
               e.postCount AS postCount,
               e.geoZone AS geoZone
        ORDER BY e.eventDate ASC
    """
    with get_driver().session() as s:
        return s.run(query).data()


def approve(event_id: str):
    query = "MATCH (e:Event {id: $id}) REMOVE e:PendingReview"
    with get_driver().session() as s:
        s.run(query, id=event_id)


def reject(event_id: str):
    query = "MATCH (e:Event {id: $id}) SET e:Rejected REMOVE e:PendingReview"
    with get_driver().session() as s:
        s.run(query, id=event_id)


def reject_bulk(event_ids: list[str]):
    query = "MATCH (e:Event) WHERE e.id IN $ids SET e:Rejected REMOVE e:PendingReview"
    with get_driver().session() as s:
        s.run(query, ids=event_ids)


def approve_bulk(event_ids: list[str]):
    query = "MATCH (e:Event) WHERE e.id IN $ids REMOVE e:PendingReview"
    with get_driver().session() as s:
        s.run(query, ids=event_ids)


def save_edits(event_id: str, title: str, description: str, event_type: str,
               event_date: str, location_name: str, city_name: str, price_range: str):
    query = """
        MATCH (e:Event {id: $id})
        SET e.title = $title,
            e.description = $description,
            e.type = $type,
            e.eventDate = $eventDate,
            e.locationName = $locationName,
            e.cityName = $cityName,
            e.priceRange = $priceRange
    """
    with get_driver().session() as s:
        s.run(
            query, id=event_id, title=title, description=description, type=event_type,
            eventDate=event_date, locationName=location_name, cityName=city_name,
            priceRange=price_range,
        )


def _score_range_filter(label, field, all_events, events, col, cazeria_label=None):
    """
    Slider de rango + atajo opcional de "10% más bajo", genérico para
    cualquier campo numérico de :Event (eventScore, confidence, layer1Score).
    El rango se calcula sobre TODOS los pendientes (all_events), no sobre
    `events` ya filtrado, para que sea estable sin importar qué otro filtro
    esté activo -- mismo criterio que el filtro de eventScore original.
    Devuelve (events_filtrados, filtro_activo: bool).
    """
    values = [e[field] for e in all_events if e.get(field) is not None]
    if not values:
        return events, False
    vmin, vmax = min(values), max(values)
    if vmin >= vmax:
        return events, False
    if cazeria_label:
        c1, c2 = col.columns([3, 1])
        v_range = c1.slider(label, min_value=float(vmin), max_value=float(vmax), value=(float(vmin), float(vmax)), step=0.01, key=f"slider_{field}")
        sorted_vals = sorted(values)
        p10 = sorted_vals[max(0, int(len(sorted_vals) * 0.10) - 1)]
        cazeria = c2.checkbox(f"🔎 {cazeria_label} (≤ {p10:.3f})", key=f"cazeria_{field}")
        if cazeria:
            return [e for e in events if e.get(field) is not None and e[field] <= p10], True
    else:
        v_range = col.slider(label, min_value=float(vmin), max_value=float(vmax), value=(float(vmin), float(vmax)), step=0.01, key=f"slider_{field}")
    filtered = [e for e in events if e.get(field) is not None and v_range[0] <= e[field] <= v_range[1]]
    return filtered, v_range != (vmin, vmax)


def render_review_tab():
    st.title("🗂️ Revisión de eventos")

    all_events = fetch_pending()
    st.caption(f"{len(all_events)} evento(s) pendiente(s) de revisión en total")

    if not all_events:
        st.success("No hay eventos pendientes por ahora. 🎉")
        return

    # Filtro por geoZone (2026-08-25): geoZone viene heredado de la cuenta que
    # publicó el evento (categorización manual) — no es infalible (solo existe
    # si la cuenta pasó por load_manual_account_categorization.py, y describe
    # la cuenta, no necesariamente la ubicación exacta del evento), pero Diego
    # reportó que en la práctica TODO lo de "Fuera de Francia" se estaba
    # rechazando uno por uno — este filtro + el botón de rechazo masivo de abajo
    # existen para no tener que hacer eso a mano evento por evento.
    geo_options = ["Todos"] + list(GEO_LABELS.values()) + [GEO_SIN_DATO]
    filt_col1, filt_col2, filt_col3, filt_col4 = st.columns(4)
    geo_choice = filt_col1.selectbox("Zona geográfica", geo_options)
    fecha_choice = filt_col2.selectbox("Fecha", ["Todos", "Con fecha", "Sin fecha"])
    geozone_presence_choice = filt_col3.selectbox("¿Tiene geoZone?", ["Todos", "Con geoZone", "Sin geoZone"])
    direccion_choice = filt_col4.selectbox("¿Tiene dirección?", ["Todos", "Con dirección", "Sin dirección"])

    if geo_choice == "Todos":
        events = all_events
    else:
        target_raw = GEO_SIN_DATO if geo_choice == GEO_SIN_DATO else next(
            k for k, v in GEO_LABELS.items() if v == geo_choice
        )
        if geo_choice == GEO_SIN_DATO:
            events = [e for e in all_events if not e.get("geoZone")]
        else:
            events = [e for e in all_events if e.get("geoZone") == target_raw]

    # Filtro de fecha (2026-09-07, pedido de Diego): independiente del
    # filtro de zona geográfica de arriba (ese filtra por VALOR de
    # geoZone; este filtra por si el campo existe o no, útil para
    # encontrar eventos que necesitan revisión manual de fecha).
    if fecha_choice == "Con fecha":
        events = [e for e in events if e.get("eventDate")]
    elif fecha_choice == "Sin fecha":
        events = [e for e in events if not e.get("eventDate")]

    if geozone_presence_choice == "Con geoZone":
        events = [e for e in events if e.get("geoZone")]
    elif geozone_presence_choice == "Sin geoZone":
        events = [e for e in events if not e.get("geoZone")]

    # "Dirección" = e.locationName (el texto de ubicación que extrae el
    # LLM en 4_enrich_events_extract.py) — independiente de geoZone, que
    # se hereda de la cuenta y no del texto del propio evento (por eso
    # puede haber eventos con dirección pero sin geoZone, o viceversa).
    if direccion_choice == "Con dirección":
        events = [e for e in events if e.get("locationName")]
    elif direccion_choice == "Sin dirección":
        events = [e for e in events if not e.get("locationName")]

    # Filtro de rango de eventScore (2026-09-07, pedido de Diego). eventScore
    # combina detección (Capa 2a, 60%) + hotness normalizado (40%), penalizado
    # si la Capa 3 no confirmó invitación pública futura — ver
    # 4_enrich_events_extract.py::compute_event_score. Rango calculado sobre
    # TODOS los pendientes (no solo los ya filtrados arriba), para que el
    # slider y el atajo de "cacería" sean estables sin importar qué otro
    # filtro esté activo.
    score_filter_active = False
    all_scores = [e["eventScore"] for e in all_events if e.get("eventScore") is not None]
    if all_scores:
        score_min, score_max = min(all_scores), max(all_scores)
        sorted_scores = sorted(all_scores)
        p10 = sorted_scores[max(0, int(len(sorted_scores) * 0.10) - 1)]
        score_col1, score_col2 = st.columns([3, 1])
        if score_min < score_max:
            score_range = score_col1.slider(
                "Rango de eventScore", min_value=float(score_min), max_value=float(score_max),
                value=(float(score_min), float(score_max)), step=0.01,
            )
        else:
            score_range = (score_min, score_max)
        cazeria = score_col2.checkbox(f"🔎 Cacería: solo el 10% más bajo (≤ {p10:.3f})")
        if cazeria:
            events = [e for e in events if e.get("eventScore") is not None and e["eventScore"] <= p10]
            score_filter_active = True
        else:
            events = [e for e in events if e.get("eventScore") is not None and score_range[0] <= e["eventScore"] <= score_range[1]]
            score_filter_active = score_range != (score_min, score_max)

    # Dimensiones de calidad adicionales (2026-09-07, pedido de Diego,
    # sugeridas en el mensaje anterior): eventScore mezcla detección +
    # popularidad + penalización LLM en un solo número -- estos tres las
    # separan para poder cazar patrones distintos:
    #   - confidence: qué tan segura estuvo la Capa 2a (NLI) de que el texto
    #     es un evento, SIN el componente de popularidad. Un eventScore
    #     decente con confidence baja es sospechoso (lo salvó el hotness).
    #   - layer1Score: similitud contra las 100 frases de referencia de la
    #     Capa 1. Uno que pasó raspando (L1 bajo) pero llegó lejos igual es
    #     un patrón raro, vale la pena mirarlo.
    #   - postCount = 1: el badge "Confirmado x2" del sitio usa esta misma
    #     señal -- un evento visto en un solo post nunca fue corroborado por
    #     una segunda mención. Combinado con eventScore bajo es la cacería
    #     más afilada de las cuatro.
    qual_col1, qual_col2 = st.columns(2)
    events, conf_active = _score_range_filter("Confianza L2 (NLI)", "confidence", all_events, events, qual_col1, cazeria_label="Cacería: 10% más baja")
    events, l1_active = _score_range_filter("layer1Score (Capa 1)", "layer1Score", all_events, events, qual_col2, cazeria_label="Cacería: 10% más bajo")

    solo_1_post = st.checkbox("📌 Solo eventos confirmados por 1 sola publicación (postCount = 1, sin corroborar)")
    postcount_active = solo_1_post
    if solo_1_post:
        events = [e for e in events if (e.get("postCount") or 1) == 1]

    st.caption(f"{len(events)} evento(s) con este filtro")

    filters_active = (
        geo_choice != "Todos" or fecha_choice != "Todos"
        or geozone_presence_choice != "Todos" or direccion_choice != "Todos"
        or score_filter_active or conf_active or l1_active or postcount_active
    )
    if filters_active and events:
        bulk_col1, bulk_col2 = st.columns(2)
        if bulk_col1.button(f"✅ Aprobar los {len(events)} eventos visibles", use_container_width=True):
            approve_bulk([e["id"] for e in events])
            st.rerun()
        if bulk_col2.button(f"❌ Rechazar los {len(events)} eventos visibles", type="primary", use_container_width=True):
            reject_bulk([e["id"] for e in events])
            st.rerun()

    if not events:
        st.info("Ningún evento pendiente coincide con este filtro.")
        return

    for ev in events:
        eid = ev["id"]
        edit_key = f"editing_{eid}"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = False

        with st.container(border=True):
            cat_label = CATEGORY_LABELS.get(ev.get("type"), ev.get("type") or "sin categoría")
            st.markdown(f"**{ev.get('title') or '(sin título)'}**  \n{cat_label}")
            geo_label = GEO_LABELS.get(ev.get("geoZone"), GEO_SIN_DATO)
            st.caption(
                f"📅 {ev.get('eventDate') or '?'} · 📍 {ev.get('locationName') or '?'}"
                f"{', ' + ev['cityName'] if ev.get('cityName') else ''} · 💶 {ev.get('priceRange') or '?'} · {geo_label}"
            )
            score_bits = [f"eventScore {ev['eventScore']:.3f}" if ev.get("eventScore") is not None else None]
            if ev.get("confidence") is not None:
                score_bits.append(f"confianza L2 {ev['confidence']:.2f}")
            if ev.get("postCount"):
                score_bits.append(f"{ev['postCount']} post(s)")
            score_bits = [b for b in score_bits if b]
            if score_bits:
                st.caption("📊 " + " · ".join(score_bits))
            st.write(ev.get("description") or "_(sin descripción)_")
            st.caption(f"Fuente: @{ev.get('sourceAuthor') or '?'} — {ev.get('sourcePostUrl') or ''}")

            if st.session_state[edit_key]:
                with st.form(key=f"form_{eid}"):
                    new_title = st.text_input("Título", value=ev.get("title") or "")
                    new_desc = st.text_area("Descripción", value=ev.get("description") or "")
                    new_type = st.selectbox(
                        "Categoría", list(CATEGORY_LABELS.keys()),
                        index=list(CATEGORY_LABELS.keys()).index(ev["type"]) if ev.get("type") in CATEGORY_LABELS else 0,
                    )
                    new_date = st.text_input("Fecha (YYYY-MM-DD)", value=ev.get("eventDate") or "")
                    new_loc = st.text_input("Ubicación", value=ev.get("locationName") or "")
                    new_city = st.text_input("Ciudad", value=ev.get("cityName") or "")
                    new_price = st.text_input("Rango de precio", value=ev.get("priceRange") or "")

                    col1, col2 = st.columns(2)
                    if col1.form_submit_button("💾 Guardar cambios", use_container_width=True):
                        save_edits(eid, new_title, new_desc, new_type, new_date, new_loc, new_city, new_price)
                        st.session_state[edit_key] = False
                        st.rerun()
                    if col2.form_submit_button("Cancelar", use_container_width=True):
                        st.session_state[edit_key] = False
                        st.rerun()
            else:
                col1, col2, col3 = st.columns(3)
                if col1.button("✅ Aprobar", key=f"approve_{eid}", use_container_width=True):
                    approve(eid)
                    st.rerun()
                if col2.button("✏️ Editar", key=f"edit_{eid}", use_container_width=True):
                    st.session_state[edit_key] = True
                    st.rerun()
                if col3.button("❌ Rechazar", key=f"reject_{eid}", use_container_width=True):
                    reject(eid)
                    st.rerun()


tab_review, tab_control = st.tabs(["🗂️ Revisión de eventos", "🎛️ Panel de control"])

with tab_review:
    render_review_tab()

with tab_control:
    render_control_panel()
