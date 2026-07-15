# Data Flow — Hub Cultural DU

Diagrama del flujo completo de datos, desde las fuentes externas hasta Neo4j.
Cada bloque indica el script responsable y los nodos/relaciones que crea o enriquece.

> **Cómo actualizar:** cada fase es una `subgraph` independiente. Añadir un nuevo
> script = añadir un nuevo `subgraph` y conectarlo con `-->` al storage correspondiente.

---

## Flujo completo

```mermaid
flowchart TD

    %% ─── FUENTES EXTERNAS ───────────────────────────────────────────────────
    IG[(Instagram\nAPI pública)]
    APIFY[Apify Cloud\napify/instagram-profile-scraper\napify/instagram-post-scraper]
    NOM[Nominatim\nOpenStreetMap]

    IG --> APIFY

    %% ─── FASE 1 — EXTRACCIÓN ─────────────────────────────────────────────────
    subgraph FASE1["Fase 1 · Extracción  (extract_profiles.py + extract_ig_*.py)"]
        direction TB
        E1["① Consulta Neo4j\nAccounts sin followersCount"]
        E2["② Estima costo\n(calibrado desde .apify_cost_log.json)"]
        E3["③ Scrape por username\n— profile metadata\n— latestPosts\n— latestIgtvVideos\n— relatedProfiles"]
        E4[/"data_raw/\nprofile_‹user›.json\nposts_‹user›.json"/]
        E1 --> E2 --> E3 --> E4
    end

    APIFY --> E3

    %% ─── FASE 2 — INGESTA ────────────────────────────────────────────────────
    subgraph FASE2["Fase 2 · Ingesta Neo4j  (2_build_graph.py)"]
        direction TB
        L1["load_profile()\nMERGE :Account\nSET :Public | :Private\nMERGE :Location  ──LOCATED_AT──▶ Account"]
        L2["load_posts()\nMERGE :Post  ──PUBLISHED──▶ Account\nMERGE :Hashtag  ──HAS_HASHTAG\nMERGE :Account  ──MENTIONS / TAGS_USER / COAUTHORED_BY\nMERGE :Location ──TAGGED_AT\nMERGE :Track    ──USES_MUSIC\nMERGE :Comment  ──WROTE / ON"]
        L3["load_igtv()\nMERGE :IgtvVideo ──PUBLISHED──▶ Account\nMERGE :Hashtag   ──HAS_HASHTAG\nMERGE :Account   ──MENTIONS"]
        L4["relatedProfiles\nMERGE :Account ──RELATED_TO──▶ Account"]
        L1 --> L2 --> L3 --> L4
    end

    E4 --> L1

    %% ─── NEO4J — CAPA BASE ───────────────────────────────────────────────────
    subgraph NEO4J["Neo4j Aura · Nodos base"]
        direction LR
        N_ACC(":Account\n:Public | :Private\n:Political")
        N_POST(":Post")
        N_IGTV(":IgtvVideo")
        N_HASH(":Hashtag")
        N_LOC(":Location")
        N_TRACK(":Track")
        N_COMM(":Comment")
    end

    L4 --> NEO4J

    %% ─── FASE 3 — ALGORITMOS GDS ─────────────────────────────────────────────
    subgraph FASE3["Fase 3 · Graph Algorithms  (run_gds_algorithms.py)"]
        direction TB
        G1["mark_political_accounts()\nSET :Political, politicalWeight=0.1"]
        G2["detect_political_by_hashtags()\nSET politicalScore"]
        G3["project_graph()\nIN-MEMORY: 'red-cultural'\nAccount + MENTIONS/TAGS_USER/RELATED_TO/COAUTHORED_BY"]
        G4["gds.degree.write()     → degreeCentrality\ngds.pageRank.write()   → pageRankScore\ngds.leiden.write()     → communityId\ngds.betweenness.write()→ betweennessScore"]
        G5["compute_cultural_relevance()\n→ culturalRelevanceScore\n= 0.35·PR + 0.25·Deg + 0.20·Bet + 0.20·log(fol)\n  × politicalPenalty"]
        G1 --> G2 --> G3 --> G4 --> G5
    end

    NEO4J --> G1
    G5 -. "SET en :Account" .-> N_ACC

    %% ─── FASE 4-A — ENRIQUECIMIENTO NLP ──────────────────────────────────────
    subgraph FASE4A["Fase 4-A · NLP Enrichment  (4_enrich_nodes_nlp.py)"]
        direction TB
        A1["detect_lang()  [langdetect]\n→ bioLanguage / captionLanguage"]
        A2["extract_features()  [spaCy ES/EN/FR]\n→ bioEntities / captionEntities\n→ bioKeywords / captionKeywords"]
        A3["[opt] SentenceTransformer\nparaphrase-multilingual-MiniLM-L12-v2\n→ bioEmbedding / captionEmbedding"]
        A1 --> A2 --> A3
    end

    NEO4J --> A1
    A3 -. "SET en :Account\n    en :Post" .-> N_ACC
    A3 -. "SET en :Account\n    en :Post" .-> N_POST

    %% ─── FASE 4-B — EXTRACCIÓN DE EVENTOS ────────────────────────────────────
    subgraph FASE4B["Fase 4-B · Event Extraction  (4_enrich_events_extract.py)"]
        direction TB
        B1["zero-shot  [cross-encoder/nli-MiniLM2-L6-H768]\n12 etiquetas culturales → tipo de evento + confianza"]
        B2["extract_ner()  [spaCy]\n→ DATE, LOC/GPE/FAC, ORG"]
        B3["compute_hotness()\n= 0.4·log(likes) + 0.3·log(comments) + 0.3·recency"]
        B4["find_similar_event()  [inline resolver]\ncoseno > 0.82 + location match + fecha ±3d\n→ ENRICH existente | CREATE nuevo"]
        B5["upsert_event()\nMERGE :Event\n──MENTIONS_EVENT──▶ Post\n──ORGANIZED──▶ Account\n──PARTICIPATED_IN──▶ Account\n──SUPPORTED──▶ Account\n──LOCATED_AT──▶ Location\n──HAS_HASHTAG──▶ Hashtag"]
        B1 --> B2 --> B3 --> B4 --> B5
    end

    NEO4J --> B1
    B5 --> N_EV

    subgraph NEO4J_EV["Neo4j Aura · Nodos evento"]
        N_EV(":Event\ntitle, type, eventDate\nhotnessScore, confidence\nbioEmbedding")
    end

    %% ─── FASE 4-C — DEDUPLICACIÓN ────────────────────────────────────────────
    subgraph FASE4C["Fase 4-C · Event Resolver  (4_enrich_events_resolve.py)"]
        direction TB
        C1["load_all_events()\ncargar :Event con embedding"]
        C2["Agrupar por locationName"]
        C3["Comparar pares\ncoseno > threshold\n+ fecha ±date_window días"]
        C4["merge_events()\ncanónico = mayor hotnessScore\nredirigir relaciones explícitamente\nDETACH DELETE duplicado"]
        C1 --> C2 --> C3 --> C4
    end

    NEO4J_EV --> C1
    C4 -. "elimina duplicados\nenriquece canónico" .-> NEO4J_EV

    %% ─── FASE 5 — GEOCODIFICACIÓN ────────────────────────────────────────────
    subgraph FASE5["Fase 5 · Geo Enrichment  (4_enrich_locations.py)"]
        direction TB
        GEO1["geocode_location()  [Nominatim 1.1s/req]\nhasta 3 queries por Location\n→ lat, lon, city, country, arrondissement"]
        GEO2["write_location_geo()\nSET lat/lon/city/country/quartier/arrondissement"]
        GEO3["write_hierarchy()\nMERGE :Arrondissement (Paris 750XX)\nMERGE :City\nMERGE :Country\n──LOCATED_IN──▶ jerarquía"]
        GEO4[/.geocoding_log.json\nFinOps log/]
        GEO1 --> GEO2 --> GEO3
        GEO3 --> GEO4
    end

    NOM --> GEO1
    N_LOC --> GEO1

    subgraph NEO4J_GEO["Neo4j Aura · Jerarquía geo"]
        direction LR
        G_LOC(":Location\n+lat/lon/city/arrondissement")
        G_ARR(":Arrondissement")
        G_CI(":City")
        G_CO(":Country")
        G_LOC -- "LOCATED_IN" --> G_ARR
        G_ARR -- "LOCATED_IN" --> G_CI
        G_CI  -- "LOCATED_IN" --> G_CO
    end

    GEO3 --> NEO4J_GEO

    %% ─── ESTILOS ─────────────────────────────────────────────────────────────
    classDef source    fill:#fde68a,stroke:#d97706,color:#000
    classDef storage   fill:#dbeafe,stroke:#3b82f6,color:#000
    classDef phase     fill:#f0fdf4,stroke:#16a34a,color:#000
    classDef node_neo  fill:#e0e7ff,stroke:#6366f1,color:#000

    class IG,APIFY,NOM source
    class E4,GEO4 storage
    class N_ACC,N_POST,N_IGTV,N_HASH,N_LOC,N_TRACK,N_COMM,N_EV node_neo
    class G_LOC,G_ARR,G_CI,G_CO node_neo
```

---

## Propiedades escritas por fase

| Fase | Nodo | Propiedades añadidas |
|------|------|----------------------|
| 2 — Ingesta | `:Account` | `id`, `fullName`, `biography`, `followersCount`, `followsCount`, `verified`, `private`, `businessCategory`, `postsCount`, `profilePicUrl` |
| 2 — Ingesta | `:Post` | `type`, `shortCode`, `url`, `caption`, `timestamp`, `likesCount`, `commentsCount`, `videoViewCount`, `displayUrl` |
| 2 — Ingesta | `:Location` | `name`, `latitude`, `longitude`, `streetAddress`, `zipCode` |
| 3 — GDS | `:Account` | `degreeCentrality`, `pageRankScore`, `communityId`, `betweennessScore`, `culturalRelevanceScore`, `politicalWeight`, `politicalScore` |
| 4-A — NLP | `:Account` | `bioLanguage`, `bioEntities`, `bioKeywords`, `bioEmbedding`\* |
| 4-A — NLP | `:Post` | `captionLanguage`, `captionEntities`, `captionKeywords`, `captionEmbedding`\* |
| 4-B — Eventos | `:Event` | `id`, `title`, `type`, `rawDate`, `eventDate`, `locationName`, `hotnessScore`, `confidence`, `postCount`, `embedding`, `createdAt` |
| 4-B — Eventos | `:Post` | `eventExtracted` (sentinel idempotencia) |
| 5 — Geo | `:Location` | `lat`, `lon`, `city`, `country`, `countryCode`, `quartier`, `arrondissement`, `displayName`, `geocodedAt` |

\* Solo con `--embeddings`

---

## Relaciones por fase

| Fase | Relación | Origen → Destino |
|------|----------|-----------------|
| 2 | `PUBLISHED` | `:Account` → `:Post` / `:IgtvVideo` |
| 2 | `HAS_HASHTAG` | `:Post` / `:IgtvVideo` → `:Hashtag` |
| 2 | `MENTIONS` | `:Post` / `:IgtvVideo` → `:Account` |
| 2 | `TAGS_USER` | `:Post` → `:Account` |
| 2 | `COAUTHORED_BY` | `:Post` → `:Account` |
| 2 | `TAGGED_AT` | `:Post` → `:Location` |
| 2 | `USES_MUSIC` | `:Post` → `:Track` |
| 2 | `WROTE` | `:Account` → `:Comment` |
| 2 | `ON` | `:Comment` → `:Post` |
| 2 | `LOCATED_AT` | `:Account` → `:Location` |
| 2 | `RELATED_TO` | `:Account` → `:Account` |
| 4-B | `MENTIONS_EVENT` | `:Post` → `:Event` |
| 4-B | `ORGANIZED` | `:Account` → `:Event` |
| 4-B | `PARTICIPATED_IN` | `:Account` → `:Event` |
| 4-B | `SUPPORTED` | `:Account` → `:Event` |
| 4-B | `LOCATED_AT` | `:Event` → `:Location` |
| 4-B | `HAS_HASHTAG` | `:Event` → `:Hashtag` |
| 5 | `LOCATED_IN` | `:Location` → `:Arrondissement` → `:City` → `:Country` |
