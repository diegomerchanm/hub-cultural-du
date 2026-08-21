"""
review_events.py — Hub Cultural DU
Staging zone: revisión interactiva de eventos nuevos (:PendingReview) antes de
que salgan al sitio. Aprobar / editar / rechazar. "Rechazar" nunca borra el
nodo — solo lo marca :Rejected (mismo mecanismo que 5_export_dashboard_data.py
y export_events_excel.py ya usan para excluir eventos del sitio/export).

Uso:
    streamlit run review_events.py

No forma parte del pipeline batch — es una herramienta manual para Diego.
"""
import os

import streamlit as st
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

st.set_page_config(page_title="Hub Cultural — Revisión de eventos", page_icon="🗂️", layout="centered")

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


def fetch_pending():
    query = """
        MATCH (e:Event)
        WHERE 'PendingReview' IN labels(e)
        RETURN e.id AS id, e.title AS title, e.description AS description,
               e.type AS type, e.eventDate AS eventDate, e.locationName AS locationName,
               e.cityName AS cityName, e.priceRange AS priceRange,
               e.sourceAuthor AS sourceAuthor, e.sourcePostUrl AS sourcePostUrl,
               e.hotnessScore AS hotnessScore, e.eventScore AS eventScore
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


st.title("🗂️ Revisión de eventos")

events = fetch_pending()
st.caption(f"{len(events)} evento(s) pendiente(s) de revisión")

if not events:
    st.success("No hay eventos pendientes por ahora. 🎉")
    st.stop()

for ev in events:
    eid = ev["id"]
    edit_key = f"editing_{eid}"
    if edit_key not in st.session_state:
        st.session_state[edit_key] = False

    with st.container(border=True):
        cat_label = CATEGORY_LABELS.get(ev.get("type"), ev.get("type") or "sin categoría")
        st.markdown(f"**{ev.get('title') or '(sin título)'}**  \n{cat_label}")
        st.caption(
            f"📅 {ev.get('eventDate') or '?'} · 📍 {ev.get('locationName') or '?'}"
            f"{', ' + ev['cityName'] if ev.get('cityName') else ''} · 💶 {ev.get('priceRange') or '?'}"
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
