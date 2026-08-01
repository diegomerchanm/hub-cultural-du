"""
Backfill de Capa 3 (LLM) sobre eventos :Event que les falte enriquecimiento
LLM — identificados por e.sourceAuthor IS NULL (nunca pasaron por Capa 3) O
e.locationCapa3 IS NULL (pasaron por Capa 3 antes de que existiera la
extracción de ciudad/dirección, ver DD-033 update 6, o el intento anterior
falló por completo en ambos proveedores cloud y nunca llegó a evaluarse).
e.locationCapa3 solo se marca true cuando el LLM respondió de verdad — un
evento sin ciudad/dirección porque el caption legítimamente no las menciona
también cuenta como resuelto (no vuelve a la cola); uno que falló por
rate-limit/proveedores caídos se queda pendiente para reintentar. NO forma
parte del pipeline principal; standalone, interrumpible y reanudable.

Uso:
    python backfill_events_capa3.py
    python backfill_events_capa3.py --max-events 100
    python backfill_events_capa3.py --dry-run
"""
import os
from datetime import datetime

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase
from tqdm import tqdm

from importlib import import_module

_extract = import_module("4_enrich_events_extract")
llm_enrich_event      = _extract.llm_enrich_event
EVENT_DATE_CLAMP_DAYS = _extract.EVENT_DATE_CLAMP_DAYS
LLM_REJECT_PENALTY    = _extract.LLM_REJECT_PENALTY
LLM_UNKNOWN_PENALTY   = _extract.LLM_UNKNOWN_PENALTY
_llm_call_failed      = _extract._llm_call_failed

load_dotenv()
driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
)

app = typer.Typer(add_completion=False)


def _fetch_pending(session, max_events: int) -> list:
    query = """
        MATCH (p:Post)-[:MENTIONS_EVENT]->(e:Event)
        WHERE e.sourceAuthor IS NULL
           OR e.locationCapa3 IS NULL
        WITH e, collect(p)[0] AS p
        RETURN e.id AS eid, e.eventScore AS eventScore,
               p.caption AS caption, p.timestamp AS timestamp, p.url AS url,
               [(p)<-[:PUBLISHED]-(a:Account) | a.username][0] AS author,
               [(p)-[:TAGGED_AT]->(loc:Location) | loc.name][0] AS taggedLocation
        ORDER BY eid
    """
    if max_events:
        query += " LIMIT $max_events"
    return session.run(query, max_events=max_events).data()


def _clamp_event_date(event_date, post_timestamp) -> "str | None":
    """Mismo clamp de sanidad que run_extraction() en 4_enrich_events_extract.py:
    descarta clean_date si se aleja del timestamp del post más de
    EVENT_DATE_CLAMP_DAYS. Parseo fallido => no clampea, deja pasar tal cual."""
    if not event_date:
        return event_date
    try:
        ed = datetime.fromisoformat(event_date.replace("Z", "+00:00")).replace(tzinfo=None)
        pd = datetime.fromisoformat((post_timestamp or "").replace("Z", "+00:00")).replace(tzinfo=None)
        if abs((ed - pd).days) > EVENT_DATE_CLAMP_DAYS:
            return None
    except (ValueError, TypeError):
        pass
    return event_date


@app.command()
def main(
    max_events: int = typer.Option(
        0, "--max-events",
        help="Límite de eventos a procesar. 0 = todos (default).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Consultar y mostrar diagnóstico sin escribir en Neo4j.",
    ),
):
    """Backfill de Capa 3 (LLM) sobre :Event con sourceAuthor IS NULL.
    Reanudable: cada corrida retoma automáticamente sobre lo que quede sin
    sourceAuthor, sin flag adicional que mantener.
    """
    driver.verify_connectivity()
    print("✅ Conexión Neo4j OK\n")

    with driver.session() as session:
        pending = _fetch_pending(session, max_events)

    print(f"🔎 {len(pending)} eventos pendientes de Capa 3 (sourceAuthor sin definir o Capa 3 de ubicación nunca ejecutada)")
    if not pending:
        driver.close()
        return

    dates_clamped = 0
    updated = 0

    with driver.session() as session:
        for row in tqdm(pending, desc="  Backfill Capa 3"):
            eid       = row["eid"]
            caption   = row["caption"] or ""
            timestamp = row["timestamp"] or ""
            author    = row["author"]

            llm_out = llm_enrich_event(caption, timestamp, label=f"{eid}")

            is_public_invitation = llm_out.get("is_public_invitation")
            is_upcoming          = llm_out.get("is_upcoming")
            if is_public_invitation is None or is_upcoming is None:
                llm_penalty = LLM_UNKNOWN_PENALTY
            else:
                llm_penalty = 1.0 if (is_public_invitation and is_upcoming) else LLM_REJECT_PENALTY

            clean_date = _clamp_event_date(llm_out.get("clean_date"), timestamp)
            if llm_out.get("clean_date") and not clean_date:
                dates_clamped += 1

            new_score = round((row["eventScore"] or 0.0) * llm_penalty, 4)

            llm_city          = llm_out.get("city")
            # Fallback al geotag propio de Instagram (Post -[:TAGGED_AT]->
            # Location) cuando el caption no menciona dirección explícita —
            # mismo criterio que 4_enrich_events_extract.py (DD-033).
            llm_exact_address = llm_out.get("exact_address") or row.get("taggedLocation")
            new_location      = llm_exact_address or llm_city or None

            if dry_run:
                print(f"\n  [{eid}] @{author}  penalty={llm_penalty}  "
                      f"score {row['eventScore']} -> {new_score}")
                print(f"    invitación={is_public_invitation}  próximo={is_upcoming}  "
                      f"fecha={clean_date or '-'}  gratis={llm_out.get('is_free')}")
                print(f"    título: {llm_out.get('title') or '(sin título del LLM)'}")
                print(f"    ciudad: {llm_city or '-'}  dirección: {llm_exact_address or '-'}")
                continue

            session.run("""
                MATCH (e:Event {id: $eid})
                SET e.sourceAuthor       = $sourceAuthor,
                    e.description        = $description,
                    e.isPublicInvitation = $isPublicInvitation,
                    e.isUpcoming         = $isUpcoming,
                    e.isFree             = $isFree,
                    e.llmReasoning       = $llmReasoning,
                    e.sourcePostUrl      = $sourcePostUrl,
                    e.sourcePostDate     = $sourcePostDate,
                    e.eventScore         = $eventScore,
                    e.cityName           = $cityName,
                    e.exactAddress       = $exactAddress,
                    e.locationCapa3      = $locationCapa3
            """, eid=eid,
                 sourceAuthor=author or "",
                 description=llm_out.get("clean_description") or "",
                 isPublicInvitation=is_public_invitation,
                 isUpcoming=is_upcoming,
                 isFree=llm_out.get("is_free"),
                 llmReasoning=llm_out.get("reasoning") or "",
                 sourcePostUrl=row["url"],
                 sourcePostDate=timestamp,
                 eventScore=new_score,
                 cityName=llm_city,
                 exactAddress=llm_exact_address,
                 # Solo se marca "resuelto" si el LLM realmente respondió —
                 # si ambos proveedores fallaron (ver _llm_call_failed), el
                 # evento debe seguir pendiente para reintentar más tarde,
                 # no quedar marcado como ya evaluado sin haberlo sido.
                 locationCapa3=not _llm_call_failed(llm_out))

            # El título viejo era el nombre de la categoría (script anterior,
            # sin Capa 3) — lo reemplazamos por el editorial del LLM cuando
            # esté disponible; si Groq no lo devolvió, dejamos el existente.
            if llm_out.get("title"):
                session.run(
                    "MATCH (e:Event {id: $eid}) SET e.title = $title",
                    eid=eid, title=llm_out["title"],
                )

            if clean_date:
                session.run(
                    "MATCH (e:Event {id: $eid}) SET e.eventDate = $eventDate",
                    eid=eid, eventDate=clean_date,
                )

            # locationName viejo (pre-Capa 3) venía del fallback ingenuo de
            # spaCy NER — sobreescribimos con dirección/ciudad del LLM cuando
            # esté disponible, igual que hacemos con title (DD-033 update 6).
            if new_location:
                session.run(
                    "MATCH (e:Event {id: $eid}) SET e.locationName = $locationName",
                    eid=eid, locationName=new_location,
                )

            updated += 1

    driver.close()
    print(f"\n{'═'*60}")
    print(f"  ✅ Eventos actualizados : {updated}")
    print(f"  🗓️  Fechas clampeadas   : {dates_clamped}  (>{EVENT_DATE_CLAMP_DAYS}d del post)")


if __name__ == "__main__":
    app()
