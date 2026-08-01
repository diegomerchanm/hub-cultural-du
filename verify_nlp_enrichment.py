"""
Verificación manual de 4_enrich_nodes_nlp.py — NO forma parte del pipeline.
Muestra ejemplos reales (bio/caption -> idioma -> entidades -> keywords)
para revisión humana, y señala casos frontera dignos de mirar.

Uso:
    python verify_nlp_enrichment.py
"""
import os
import random

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
)

SAMPLE_PER_LANG = 3


def show_bio_examples():
    print("=" * 70)
    print("MUESTRA DE BIOS ENRIQUECIDAS (por idioma detectado)")
    print("=" * 70)
    with driver.session() as session:
        for lang in ["es", "fr", "en", "unknown"]:
            rows = session.run(
                """
                MATCH (a:Account)
                WHERE a.bioLanguage = $lang
                RETURN a.username AS u, a.biography AS bio,
                       a.bioEntities AS ents, a.bioKeywords AS kw
                ORDER BY rand() LIMIT $n
                """,
                lang=lang, n=SAMPLE_PER_LANG,
            ).data()
            print(f"\n--- idioma = {lang} ---")
            for r in rows:
                bio_short = (r["bio"] or "")[:120].replace("\n", " ")
                print(f"  @{r['u']}")
                print(f"    bio: {bio_short}")
                print(f"    entidades: {r['ents']}")
                print(f"    keywords:  {r['kw']}")
                print()


def show_known_accounts():
    """Cuentas donde ya sabemos (por contexto del proyecto) qué idioma
    esperaríamos, para chequear consistencia."""
    print("=" * 70)
    print("CUENTAS CONOCIDAS — chequeo de consistencia")
    print("=" * 70)
    checks = [
        ("consuladocolparis", "es (consulado colombiano)"),
        ("francediplo_es", "es o fr (diplomacia francesa en español)"),
        ("institutocervantesparis", "es (instituto español, sede en Francia)"),
        ("maisondelameriquelatineparis", "fr (institución francesa)"),
        ("alianzafrancesademedellin", "es (sede en Medellín, bio sobre francés)"),
    ]
    with driver.session() as session:
        for username, expected in checks:
            r = session.run(
                """
                MATCH (a:Account {username: $u})
                RETURN a.biography AS bio, a.bioLanguage AS lang,
                       a.bioEntities AS ents
                """, u=username,
            ).single()
            if not r:
                print(f"  @{username}: NO ENCONTRADA")
                continue
            print(f"  @{username}  (esperado: {expected})")
            print(f"    detectado: {r['lang']}")
            print(f"    bio: {(r['bio'] or '')[:100]}")
            print(f"    entidades: {r['ents']}")
            print()


def show_unknown_reasons():
    """Para cada 'unknown', muestra la bio cruda -- para confirmar si es
    porque es muy corta (<10 chars, comportamiento esperado) o algo raro."""
    print("=" * 70)
    print("CASOS 'unknown' — ¿son bios cortas (esperado) o un problema real?")
    print("=" * 70)
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (a:Account)
            WHERE a.bioLanguage = 'unknown'
            RETURN a.username AS u, a.biography AS bio
            """
        ).data()
    short = [r for r in rows if len(r["bio"] or "") < 10]
    long_ = [r for r in rows if len(r["bio"] or "") >= 10]
    print(f"  Total unknown: {len(rows)}")
    print(f"  Por bio <10 caracteres (esperado): {len(short)}")
    print(f"  Por fallo real de detección (bio >=10 chars, revisar): {len(long_)}")
    if long_:
        print("\n  Casos a revisar manualmente:")
        for r in long_[:10]:
            print(f"    @{r['u']}: \"{r['bio']}\"")


def show_caption_examples():
    print("\n" + "=" * 70)
    print("MUESTRA DE CAPTIONS ENRIQUECIDOS")
    print("=" * 70)
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (p:Post)
            WHERE p.captionLanguage IS NOT NULL AND p.captionLanguage <> 'unknown'
            RETURN p.caption AS cap, p.captionLanguage AS lang,
                   p.captionEntities AS ents, p.captionKeywords AS kw
            ORDER BY rand() LIMIT 5
            """
        ).data()
    for r in rows:
        cap_short = (r["cap"] or "")[:120].replace("\n", " ")
        print(f"  [{r['lang']}] {cap_short}")
        print(f"    entidades: {r['ents']}")
        print(f"    keywords:  {r['kw']}")
        print()


if __name__ == "__main__":
    driver.verify_connectivity()
    print("Conexión Neo4j OK\n")
    show_bio_examples()
    show_known_accounts()
    show_unknown_reasons()
    show_caption_examples()
    driver.close()
