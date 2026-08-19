"""
classify_candidate_accounts.py — Hub Cultural DU

Por qué existe: la expansión de red (sesión aparte, ver handoff pegado por
Diego 2026-08-19) encontró ~7897 cuentas candidatas entre dos fuentes
(following-scraping vía HikerAPI/scraper manual de consola, y
relatedProfiles ya presentes en los profile_*.json que Apify trae por
cada perfil scrapeado, ~35 por cuenta en promedio). Ninguna de esas
candidatas tiene biografía todavía — hace falta scrapear el perfil
completo (`1_harvest_ig_profiles.py --seeds <archivo>`) antes de poder
clasificar nada.

Qué hace: clasifica cuentas candidatas (una vez que YA tienen
`data_raw/profile_<username>.json` con `biography`) contra el criterio
de selección de Diego (2026-08-19), sin gastar tokens de LLM para el
grueso del trabajo — Capa 1 (embeddings, gratis, corre local) hace casi
todo el trabajo; Capa 2 (reglas duras, gratis) solo para lo inequívoco.
No hay Capa 3 (LLM) todavía a propósito — la idea es medir en el piloto
si hace falta antes de gastar nada ahí (ver docs/decisions_es.md).

Criterio (parafraseado de Diego, 2026-08-19): cuentas comunitarias — no
excluir centros de eventos culturales; sí excluir fiestas/vida nocturna;
no excluir nada científico (física, geología, arqueología); incluir
literatura y teatro; nueva categoría "terceros lugares" — espacios donde
se puede conocer gente (cursos, charlas, mesas redondas); excluir
personas individuales.

Capa 1 — sentence-transformers (mismo modelo que ya usa
4_enrich_events_extract.py, `paraphrase-multilingual-MiniLM-L12-v2`,
cero dependencia nueva): embeddea la bio + fullName + businessCategoryName
de cada cuenta candidata, y la compara por similitud coseno MÁXIMA contra
dos bancos de frases de referencia (POSITIVE_ANCHORS / NEGATIVE_ANCHORS).
`score = max_sim(positivos) - max_sim(negativos)`.

Capa 2 — reglas duras, solo para lo inequívoco por keyword en la bio
(fiesta/discoteca/vida nocturna) — ver NIGHTLIFE_KEYWORDS. Deliberadamente
NO hay una regla dura para "persona individual": el businessCategoryName
de Instagram no distingue eso de forma confiable (una cuenta de "Artista"
puede ser un colectivo o una sola persona), así que esa decisión queda en
manos del embedding (ver NEGATIVE_ANCHORS) — a afinar con el piloto.

Salida: un CSV ordenado por score descendente (no dos documentos
separados) para que Diego filtre/ordene él mismo en el mismo espíritu
que ya usa en `cuentas_instagram_completo_v4.xlsx` — con una columna
`bucket` (fijo / posible / descartar) calculada por umbral, pero el
score crudo queda visible por si los umbrales hay que correrlos.

Uso (correr en el venv de Diego — sentence-transformers no está
disponible en el sandbox de este agente):
    python classify_candidate_accounts.py --usernames-file config/seeds_pilot_account_classification.json
    python classify_candidate_accounts.py --usernames-file <lo que sea> --out data_processed/pilot_classification.csv
"""

import json
import os
import re

import typer

app = typer.Typer(add_completion=False)

DATA_RAW = "data_raw"
ST_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"  # mismo modelo que 4_enrich_events_extract.py

# ── Capa 2: reglas duras (gratis, antes de gastar nada en embeddings) ────────
NIGHTLIFE_KEYWORDS = [
    "discoteca", "boîte de nuit", "boite de nuit", "night club", "nightclub",
    "club privé", "club prive", "vida nocturna", "afterwork party", "clubbing",
    "rave", "fiesta electrónica", "fiesta electronica", "soirée clubbing",
]


def hard_exclude_reason(bio: str) -> str:
    low = (bio or "").lower()
    for kw in NIGHTLIFE_KEYWORDS:
        if kw in low:
            return f"regla dura: menciona '{kw}' (vida nocturna/fiesta)"
    return ""


# ── Criterio de selección (Diego, 2026-08-19) — banco de frases ancla ───────
# Multilingüe a propósito (ES/FR/EN) porque las bios vienen en cualquiera de
# los tres. "Terceros lugares" = concepto de Ray Oldenburg (ni casa ni
# trabajo, espacio de encuentro) — es la categoría nueva que pidió Diego.
POSITIVE_ANCHORS = [
    "centro comunitario cultural sin ánimo de lucro",
    "espacio de encuentro comunitario abierto al público",
    "association culturelle à but non lucratif",
    "centro cultural con talleres y eventos regulares",
    "institución educativa con programación cultural abierta",
    "museo o galería de arte abierta al público",
    "teatro o sala de espectáculos con programación regular",
    "librería con eventos literarios y club de lectura",
    "tertulia o club de lectura literaria",
    "charla o conferencia abierta al público en general",
    "curso o taller donde se conoce gente nueva",
    "mesa redonda o encuentro de discusión abierto",
    "espacio de divulgación científica, física, geología o arqueología",
    "festival cultural comunitario",
    "casa de la cultura latinoamericana en Francia",
    "grupo o colectivo cultural comunitario",
    "tercer lugar de encuentro social, ni casa ni trabajo",
    "association d'accueil et d'échange culturel",
    "lieu de rencontre et d'ateliers ouverts au public",
]
NEGATIVE_ANCHORS = [
    "cuenta personal de un individuo o influencer",
    "compte personnel d'un influenceur",
    "fiesta, discoteca o vida nocturna",
    "club privado de fiesta electrónica",
    "marca comercial de ropa o producto sin relación cultural",
    "artista solista promocionando únicamente su propia carrera",
    "medio de comunicación o cadena de noticias generalista",
    "embajada, consulado o entidad de gobierno sin programación cultural propia",
    "partido político o campaña electoral",
    "cuenta de comercio o restaurante sin programación cultural",
]

# Umbrales de primera pasada — a afinar con el piloto de 50 (ver docstring).
THRESHOLD_FIJO = 0.15     # score >= esto -> "fijo"
THRESHOLD_POSIBLE = 0.00  # score >= esto (y < THRESHOLD_FIJO) -> "posible"
# score < THRESHOLD_POSIBLE -> "descartar"


def load_profile(username: str) -> dict:
    path = os.path.join(DATA_RAW, f"profile_{username}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = data[0] if data else {}
    return data or {}


def candidate_text(profile: dict) -> str:
    """Texto que se embeddea: bio + nombre + categoría de negocio, todo lo
    que Instagram da gratis sin tener que leer posts."""
    parts = [
        profile.get("biography") or "",
        profile.get("fullName") or "",
        profile.get("businessCategoryName") or "",
    ]
    return " · ".join(p.strip() for p in parts if p and p.strip())


def load_usernames(usernames_file: str) -> list:
    with open(usernames_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "seeds" in data:
        return [s["handle"].strip().lower() for s in data["seeds"] if s.get("handle")]
    if isinstance(data, list):
        return [str(u).strip().lower() for u in data]
    raise typer.BadParameter(
        "Formato no reconocido — se espera {'seeds': [{'handle': ...}, ...]} o una lista simple de usernames."
    )


@app.command()
def main(
    usernames_file: str = typer.Option(
        ..., "--usernames-file",
        help="JSON con las cuentas a clasificar (formato de --seeds de 1_harvest_ig_profiles.py, o lista simple)."
    ),
    out: str = typer.Option(
        "data_processed/account_classification.csv", "--out",
        help="Ruta del CSV de salida, ordenado por score descendente."
    ),
):
    from sentence_transformers import SentenceTransformer  # import tardío — pesado, ver docstring

    usernames = load_usernames(usernames_file)
    print(f"📋 {len(usernames)} cuentas a clasificar, leídas de {usernames_file}")

    rows = []
    missing = []
    for u in usernames:
        profile = load_profile(u)
        if not profile:
            missing.append(u)
            continue
        rows.append({
            "username": u,
            "fullName": profile.get("fullName") or "",
            "businessCategoryName": profile.get("businessCategoryName") or "",
            "biography": (profile.get("biography") or "").replace("\n", " "),
            "followersCount": profile.get("followersCount"),
            "text": candidate_text(profile),
        })

    if missing:
        print(f"  ⚠️  {len(missing)} sin profile_<username>.json todavía — faltan por scrapear: "
              f"{', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}")

    if not rows:
        print("  ❌ Ninguna cuenta tiene profile_<username>.json — corré 1_harvest_ig_profiles.py primero.")
        raise typer.Exit(1)

    print(f"  📦 Cargando sentence-transformers: {ST_MODEL}")
    model = SentenceTransformer(ST_MODEL)

    print("  🧮 Embeddeando anclas de criterio…")
    pos_emb = model.encode(POSITIVE_ANCHORS, normalize_embeddings=True)
    neg_emb = model.encode(NEGATIVE_ANCHORS, normalize_embeddings=True)

    texts = [r["text"] or r["username"] for r in rows]
    print(f"  🧮 Embeddeando {len(texts)} cuentas candidatas…")
    cand_emb = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    import numpy as np

    for i, r in enumerate(rows):
        sims_pos = cand_emb[i] @ pos_emb.T
        sims_neg = cand_emb[i] @ neg_emb.T
        best_pos_idx = int(np.argmax(sims_pos))
        best_neg_idx = int(np.argmax(sims_neg))
        pos_score = float(sims_pos[best_pos_idx])
        neg_score = float(sims_neg[best_neg_idx])
        score = pos_score - neg_score

        hard_reason = hard_exclude_reason(r["biography"])
        if hard_reason:
            bucket = "descartar"
        elif score >= THRESHOLD_FIJO:
            bucket = "fijo"
        elif score >= THRESHOLD_POSIBLE:
            bucket = "posible"
        else:
            bucket = "descartar"

        r.update({
            "score": round(score, 4),
            "pos_score": round(pos_score, 4),
            "neg_score": round(neg_score, 4),
            "top_positive_match": POSITIVE_ANCHORS[best_pos_idx],
            "top_negative_match": NEGATIVE_ANCHORS[best_neg_idx],
            "hard_exclude_reason": hard_reason,
            "bucket": bucket,
        })

    rows.sort(key=lambda r: -r["score"])

    import csv
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    fieldnames = [
        "username", "bucket", "score", "pos_score", "neg_score",
        "top_positive_match", "top_negative_match", "hard_exclude_reason",
        "fullName", "businessCategoryName", "followersCount", "biography",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    n_fijo = sum(1 for r in rows if r["bucket"] == "fijo")
    n_posible = sum(1 for r in rows if r["bucket"] == "posible")
    n_descartar = sum(1 for r in rows if r["bucket"] == "descartar")
    print(f"\n✅ {len(rows)} cuentas clasificadas -> {out}")
    print(f"   fijo: {n_fijo}  ·  posible: {n_posible}  ·  descartar: {n_descartar}")
    print("   Revisá el CSV ordenado por score — los umbrales (THRESHOLD_FIJO/THRESHOLD_POSIBLE) "
          "son de primera pasada, se afinan mirando dónde caen los ejemplos que vos ya conocés.")


if __name__ == "__main__":
    app()
