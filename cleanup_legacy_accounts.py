"""
cleanup_legacy_accounts.py — Hub Cultural DU

Por qué existe: las primeras cargas de este proyecto seleccionaban cuentas
con un criterio automático (scoring en old/1_harvest_account_classifier.py /
data_processed/account_scores.csv), y en la práctica muy pocas resultaron
ser cuentas culturalmente valiosas. Desde que existe la categorización
manual (load_manual_account_categorization.py, ~126 cuentas revisadas a
mano), esa es la señal de confianza real del proyecto — no el criterio
automático viejo.

Nota sobre legacyBatch: seal_legacy_batch.py reportó 0 candidatos la última
vez que se corrió, porque para ese momento todo nodo ya tenía alguna fecha
propia (firstSeenAt, manualDataCuratedAt, etc.) — es decir, la propiedad
legacyBatch=true nunca llegó a marcar ningún nodo real. Por eso este script
NO usa legacyBatch como criterio: usa manualDataCuratedAt IS NULL, que es
lo que en la práctica distingue "cuenta que yo revisé" de "cuenta que
seleccionó el script viejo".

Excepción — candidatos vía RELATED_TO: 2_build_graph.py crea un :Account
por cada `relatedProfiles` que Instagram sugiere al scrapear un perfil
(ver esa sección del script). Eso significa que algunas cuentas "sin
categorización manual" no son basura vieja: fueron sugeridas por una
cuenta que SÍ curaste a mano, potencialmente en el harvest más reciente.
Antes de borrar nada, este script las TAGEA (no las borra) con
discoveredViaCuratedAccount=true y candidateReviewStatus='pending', para
que queden como lista de "candidatas a revisar" en vez de perderse.
Idempotente: si ya tienen candidateReviewStatus, no lo pisa (así una
revisión manual posterior —'approved'/'rejected'— no se resetea si el
script se vuelve a correr).

Qué borra (en este orden, porque el orden importa — ver nota abajo), UNA
VEZ excluidas las cuentas curadas Y las candidatas recién tageadas:
    1. :Comment escritos por esas cuentas (vía WROTE)
    2. :Post / :IgtvVideo publicados por esas cuentas (vía PUBLISHED)
    3. las cuentas :Account mismas
    4. barrido de huérfanos: :Hashtag, :Location, :Track, :Event,
       :Arrondissement, :City, :Country, :Comment que queden con 0
       relaciones tras los pasos 1-3 (se repite varias veces porque un
       huérfano puede dejar huérfano a su padre, p. ej. Location ->
       Arrondissement -> City)

Por qué este orden: DETACH DELETE de una cuenta borra sus relaciones
PUBLISHED/WROTE — si borráramos las cuentas primero, perderíamos la única
forma de identificar cuáles Posts/Comments les pertenecían. Por eso
Comments y Posts se borran ANTES que las Accounts que los originaron.

Seguridad: todo (tageo de candidatos incluido) corre dentro de UNA
transacción explícita. Se calculan los números REALES primero; recién
después, si no es --dry-run, se pide confirmación escrita; solo entonces
se hace COMMIT. En --dry-run o si cancelas la confirmación, ROLLBACK — no
se guarda ni el tageo de candidatos.

Uso:
    python cleanup_legacy_accounts.py --dry-run   # cuenta exacto, no borra ni tagea
    python cleanup_legacy_accounts.py              # borra de verdad (pide confirmación)
"""

import os

import typer
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

app = typer.Typer()

# Marca como "candidata a revisar" (no la borra) cualquier cuenta sin
# categorización manual que fue sugerida (RELATED_TO) por una cuenta que sí
# está curada a mano — sin importar cuándo se descubrió la relación.
TAG_CANDIDATES = """
    MATCH (curated:Account)-[:RELATED_TO]->(c:Account)
    WHERE curated.manualDataCuratedAt IS NOT NULL
      AND c.manualDataCuratedAt IS NULL
    WITH DISTINCT c
    SET c.discoveredViaCuratedAccount = true,
        c.candidateReviewStatus = coalesce(c.candidateReviewStatus, 'pending')
    RETURN count(c) AS n
"""

# "Legacy" = sin categorización manual Y sin haber sido tageada como
# candidata (el paso anterior corre siempre antes que esta condición se
# evalúe, dentro de la misma transacción).
LEGACY_CONDITION = """
    a.manualDataCuratedAt IS NULL
    AND a.discoveredViaCuratedAccount IS NULL
"""

COUNT_LEGACY_ACCOUNTS = f"""
    MATCH (a:Account) WHERE {LEGACY_CONDITION}
    RETURN count(a) AS n
"""

DELETE_COMMENTS = f"""
    MATCH (a:Account)-[:WROTE]->(cm:Comment) WHERE {LEGACY_CONDITION}
    WITH DISTINCT cm
    DETACH DELETE cm
    RETURN count(cm) AS n
"""

DELETE_POSTS = f"""
    MATCH (a:Account)-[:PUBLISHED]->(p) WHERE {LEGACY_CONDITION}
    WITH DISTINCT p
    DETACH DELETE p
    RETURN count(p) AS n
"""

DELETE_ACCOUNTS = f"""
    MATCH (a:Account) WHERE {LEGACY_CONDITION}
    WITH DISTINCT a
    DETACH DELETE a
    RETURN count(a) AS n
"""

# Nodos que solo tienen sentido si algo los referencia. Se repite hasta que
# una pasada no borre nada, para capturar cadenas (p. ej. una Location
# huérfana puede dejar huérfano a su Arrondissement).
DELETE_ORPHANS = """
    MATCH (n)
    WHERE (n:Hashtag OR n:Location OR n:Track OR n:Event
           OR n:Arrondissement OR n:City OR n:Country OR n:Comment)
      AND NOT (n)--()
    WITH DISTINCT n
    DETACH DELETE n
    RETURN count(n) AS n
"""

MAX_ORPHAN_PASSES = 6


@app.command()
def main(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Corre todo dentro de una transacción y hace ROLLBACK — conteos exactos, no borra ni tagea nada"
    ),
):
    if not all([NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD]):
        raise ValueError("Error: credenciales Neo4j ausentes en .env")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    driver.verify_connectivity()

    with driver.session() as session:
        tx = session.begin_transaction()
        try:
            candidates_tagged = tx.run(TAG_CANDIDATES).single()["n"]
            legacy_count = tx.run(COUNT_LEGACY_ACCOUNTS).single()["n"]

            print(f"🔎 {candidates_tagged} cuentas tageadas como candidatas a revisar (RELATED_TO desde cuenta curada)")
            print(f"📦 {legacy_count} cuentas realmente legacy (sin categorización manual, sin ser candidatas)")

            if legacy_count == 0:
                print("\n✅ No hay cuentas para borrar con el criterio actual.")
                if candidates_tagged > 0 and not dry_run:
                    tx.commit()
                    print(f"   (se guardó el tageo de {candidates_tagged} candidatas)")
                else:
                    tx.rollback()
                driver.close()
                return

            comments_deleted = tx.run(DELETE_COMMENTS).single()["n"]
            posts_deleted = tx.run(DELETE_POSTS).single()["n"]
            accounts_deleted = tx.run(DELETE_ACCOUNTS).single()["n"]

            orphans_deleted = 0
            for _ in range(MAX_ORPHAN_PASSES):
                n = tx.run(DELETE_ORPHANS).single()["n"]
                orphans_deleted += n
                if n == 0:
                    break

            print("\nResultado:")
            print(f"   · Candidatas tageadas (preservadas): {candidates_tagged}")
            print(f"   · Comments borrados:        {comments_deleted}")
            print(f"   · Posts/IgtvVideos borrados: {posts_deleted}")
            print(f"   · Accounts borradas:         {accounts_deleted}")
            print(f"   · Huérfanos barridos:        {orphans_deleted}  (Hashtag/Location/Track/Event/Arrondissement/City/Country/Comment)")

            if dry_run:
                tx.rollback()
                print("\n[dry-run] ROLLBACK — nada se guardó (ni el borrado ni el tageo de candidatas). Corre sin --dry-run para aplicar de verdad.")
            else:
                confirm = input(
                    f"\n⚠️  Esto va a borrar {accounts_deleted} cuentas y todo lo que dependa "
                    f"exclusivamente de ellas, DE FORMA PERMANENTE (y guardar el tageo de "
                    f"{candidates_tagged} candidatas).\nEscribe BORRAR para confirmar: "
                )
                if confirm.strip() == "BORRAR":
                    tx.commit()
                    print("\n✅ Cambios guardados permanentemente.")
                else:
                    tx.rollback()
                    print("\nCancelado — ROLLBACK, no se guardó nada.")
        except Exception:
            tx.rollback()
            raise

    driver.close()


if __name__ == "__main__":
    app()
