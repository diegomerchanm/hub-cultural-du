"""
Backfill único — traduce al francés el vocabulario de eventArtTags ya
existente en eventos creados ANTES de que 4_enrich_events_extract.py
empezara a generar eventArtTagsFr en la misma llamada del LLM (ver
docs/decisions_es.md DD-054).

Por qué un script aparte y no una corrida más de la extracción normal:
eventArtTags es un tag LIBRE por EVENTO (DD-042, no una taxonomía fija),
pero el vocabulario real que aparece en el grafo es chico y se repite
mucho entre eventos (ej. "Música", "Danza", "Fotografía" aparecen en
decenas de eventos distintos). Traducir evento por evento con el pipeline
completo de extracción sería re-invocar el LLM sobre cada post de nuevo
(carísimo e innecesario); acá se hace al revés: se junta el vocabulario
ÚNICO de tags que todavía no tienen traducción, se traduce ese vocabulario
en UNA sola llamada al LLM, y el resultado (un diccionario ES→FR) se
aplica a todos los eventos que lo necesiten.

Idempotente: solo toca eventos con eventArtTagsFr ausente o de largo
distinto a eventArtTags (mismo criterio de "necesita backfill" en ambos
pasos, query y escritura) — correr de nuevo sobre datos ya backfilleados
no hace nada.

Uso:
    python backfill_art_tags_fr.py --dry-run   # ver el diccionario propuesto y cuántos eventos se tocarían
    python backfill_art_tags_fr.py             # escribir eventArtTagsFr en Neo4j
"""

import json
import os
import time

import requests
import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

# ── 1. Credenciales ───────────────────────────────────────────────────────────
load_dotenv()
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")

if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
    raise ValueError("Error: credenciales Neo4j ausentes en .env")
if not GROQ_API_KEY:
    raise ValueError(
        "Error: GROQ_API_KEY ausente en .env — este script hace una sola "
        "llamada de traducción vía Groq (mismo proveedor default que "
        "4_enrich_events_extract.py), no tiene fallback a otros proveedores "
        "porque no vale la pena la complejidad para una corrida de una vez."
    )

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "openai/gpt-oss-120b"

# Query compartida entre el paso de lectura del vocabulario y el paso de
# escritura — "necesita backfill" es el mismo criterio en los dos lugares:
# eventArtTagsFr ausente, o de largo distinto a eventArtTags (señal de que
# quedó a mitad de una corrida anterior, o de un evento viejo sin ningún
# campo *Fr todavía).
NEEDS_BACKFILL_WHERE = """
    e.eventArtTags IS NOT NULL AND size(e.eventArtTags) > 0
    AND (e.eventArtTagsFr IS NULL OR size(e.eventArtTagsFr) <> size(e.eventArtTags))
"""


def fetch_vocabulary(session) -> list[str]:
    """Vocabulario único de tags en español entre los eventos que necesitan
    backfill — no todo el eventArtTags del grafo, para no gastar tokens
    traduciendo tags que ya tienen su versión en francés."""
    result = session.run(f"""
        MATCH (e:Event)
        WHERE {NEEDS_BACKFILL_WHERE}
        UNWIND e.eventArtTags AS tag
        RETURN DISTINCT tag
        ORDER BY tag
    """)
    return [r["tag"] for r in result]


def translate_vocabulary(tags: list[str]) -> dict[str, str]:
    """Una sola llamada a Groq: pide un objeto JSON {tag_es: tag_fr} para
    todo el vocabulario junto. Sin reintentos elaborados ni fallback a otro
    proveedor (a diferencia de 4_enrich_events_extract.py) — si falla, el
    script no escribe nada y Diego lo puede correr de nuevo tal cual
    (idempotente, no hay estado a medio camino que limpiar)."""
    prompt = (
        "Traducí al FRANCÉS cada una de estas etiquetas cortas de disciplinas/"
        "medios artísticos y culturales, usadas como filtro clickeable en un "
        "sitio bilingüe ES/FR de eventos culturales. Mismo criterio que "
        "traducciones de título/descripción del mismo proyecto: corta (máx 3 "
        "palabras), sin paréntesis ni comas dentro de la traducción, y los "
        "nombres propios (de personas, lugares, movimientos con nombre "
        "propio) se dejan sin traducir. Responde ÚNICAMENTE con un objeto "
        "JSON plano donde cada clave es EXACTAMENTE una de las etiquetas de "
        "abajo (copiada tal cual, sin modificarla) y el valor es su "
        "traducción al francés — una entrada por cada etiqueta, ninguna de "
        "más ni de menos.\n\n"
        f"Etiquetas:\n{json.dumps(tags, ensure_ascii=False, indent=2)}"
    )
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                GROQ_ENDPOINT,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model":           GROQ_MODEL,
                    "messages":        [{"role": "user", "content": prompt}],
                    "temperature":     0.0,
                    "response_format": {"type": "json_object"},
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if not content:
                raise ValueError("respuesta vacía de Groq")
            mapping = json.loads(content)
            if not isinstance(mapping, dict):
                raise ValueError(f"se esperaba un objeto JSON, vino {type(mapping).__name__}")
            return mapping
        except Exception as e:
            print(f"  ⚠️  Intento {attempt}/3 falló: {type(e).__name__}: {e}")
            if attempt < 3:
                time.sleep(2 * attempt)
    raise RuntimeError("No se pudo traducir el vocabulario tras 3 intentos — nada escrito en Neo4j.")


def apply_backfill(session, mapping: dict[str, str]) -> int:
    """Recalcula eventArtTagsFr para cada evento que lo necesita, mapeando
    cada tag de eventArtTags por el diccionario — si algún tag quedó sin
    traducción en el mapping (no debería pasar si translate_vocabulary
    validó bien, pero por las dudas), cae al tag en español antes que dejar
    un hueco (mismo criterio de fallback que evTitle/evDescription en
    app.js: mejor mostrar español que nada)."""
    with session.begin_transaction() as tx:
        events = list(tx.run(f"""
            MATCH (e:Event)
            WHERE {NEEDS_BACKFILL_WHERE}
            RETURN e.id AS id, e.eventArtTags AS tags
        """))
        updates = [
            {"id": r["id"], "tagsFr": [mapping.get(tag, tag) for tag in r["tags"]]}
            for r in events
        ]
        tx.run("""
            UNWIND $updates AS u
            MATCH (e:Event {id: u.id})
            SET e.eventArtTagsFr = u.tagsFr
        """, updates=updates)
        tx.commit()
    return len(updates)


app = typer.Typer(add_completion=False)


@app.command()
def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Mostrar el diccionario de traducción propuesto y cuántos eventos se tocarían, sin escribir en Neo4j."
    ),
):
    with driver.session() as session:
        print("🔍 Buscando vocabulario de eventArtTags sin traducción...")
        tags = fetch_vocabulary(session)

        if not tags:
            print("  ✅ Nada que traducir — todos los eventos con eventArtTags ya tienen eventArtTagsFr al día.")
            return

        print(f"  📚 {len(tags)} etiquetas únicas sin traducción: {', '.join(tags)}")
        print("\n🌐 Traduciendo con Groq (una sola llamada)...")
        mapping = translate_vocabulary(tags)

        missing = [t for t in tags if t not in mapping]
        if missing:
            print(f"  ⚠️  El LLM no devolvió traducción para {len(missing)} etiqueta(s): {missing}"
                  f" — se van a dejar en español para esas (fallback), no rompe el resto.")

        print("\n  Diccionario propuesto:")
        for tag in tags:
            print(f"    {tag:<30} → {mapping.get(tag, tag + ' (sin traducir, fallback ES)')}")

        if dry_run:
            events_to_touch = session.run(f"""
                MATCH (e:Event) WHERE {NEEDS_BACKFILL_WHERE} RETURN count(e) AS n
            """).single()["n"]
            print(f"\n  🔎 --dry-run: {events_to_touch} eventos serían actualizados. Nada escrito.")
            return

        print("\n💾 Escribiendo eventArtTagsFr en Neo4j...")
        n = apply_backfill(session, mapping)
        print(f"  ✅ {n} eventos actualizados.")


if __name__ == "__main__":
    app()
