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
    geo_choice = st.selectbox("Filtrar por zona geográfica", geo_options)

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

    st.caption(f"{len(events)} evento(s) con este filtro")

    if geo_choice != "Todos" and events:
        if st.button(f"❌ Rechazar los {len(events)} eventos visibles ({geo_choice})", type="primary"):
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
