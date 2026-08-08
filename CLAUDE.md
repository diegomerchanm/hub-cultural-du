# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Hub Cultural DU** is a data pipeline for mapping and analyzing Colombian cultural networks on Instagram. It scrapes Instagram profiles via Apify, ingests data into a Neo4j graph database, and runs graph algorithms to score cultural influence and detect communities.

**Pipeline Phases:**
1. **Extraction** — `1_harvest_ig_profiles.py` / `1_harvest_ig_posts.py` / `1_harvest_ig_network.py` / `extract_profiles.py` → scrape Instagram via Apify → `data_raw/*.json`
2. **Ingestion** — `2_build_graph.py` → loads JSON files into Neo4j Aura
3. **Analysis** — `run_gds_algorithms.py` → runs GDS algorithms and writes scores back to nodes
4. **NLP** (run in order):
   - `4_enrich_nodes_nlp.py` — language detection, NER, keywords, optional embeddings on Account.biography and Post.caption
   - `4_enrich_events_extract.py` — zero-shot classification → `:Event` nodes with inline deduplication
   - `4_enrich_events_resolve.py` — standalone deduplication pass for existing `:Event` nodes
5. **Geo** — `4_enrich_locations.py` → Nominatim geocoding + `:LOCATED_IN` hierarchy

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Full pipeline (phases in order)
python extract_profiles.py                  # Phase 1: scrape pending profiles (cost-aware)
python 2_build_graph.py                     # Phase 2: ingest data_raw/*.json into Neo4j
python run_gds_algorithms.py                # Phase 3: graph algorithms + cultural relevance score
python 3_analyze_network.py run-all         # Phase 3-alt: local analysis (igraph/leidenalg) — no GDS or port 7687
python 3_analyze_network.py analyze         #   metrics only, 100% offline from data_processed/*.csv
python 4_enrich_nodes_nlp.py                # Phase 4-A: bio + caption → lang/NER/keywords
python 4_enrich_events_extract.py           # Phase 4-B: detect events → :Event nodes
python 4_enrich_events_resolve.py           # Phase 4-C: dedup existing :Event nodes
python 4_enrich_locations.py                # Phase 5: geocode :Location + hierarchy

# Key options
python 4_enrich_nodes_nlp.py --only bio --embeddings          # bios + bioEmbedding
python 4_enrich_nodes_nlp.py --only posts --embeddings --emb-target posts
python 4_enrich_events_extract.py --threshold 0.6 --max-posts 500 --dry-run
python 4_enrich_events_resolve.py --threshold 0.85 --dry-run
python 4_enrich_locations.py --city-hint "Paris, France" --dry-run

# Individual scrapers (lower-level)
python 1_harvest_ig_profiles.py     # profile metadata
python 1_harvest_ig_posts.py        # post timelines
python 1_harvest_ig_network.py      # follower networks
```

## Environment

Requires a `.env` file (never commit it):
```
APIFY_TOKEN=...
NEO4J_URI=neo4j+s://<host>.databases.neo4j.io
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
```

## Graph Data Model

**Nodes:** `:Account` (`:Public` | `:Private`, optionally `:Political`), `:Post`, `:Hashtag`, `:Location`, `:Track`, `:Comment`, `:IgtvVideo`, `:Event`, `:City`, `:Country`, `:Arrondissement`

**Relationships (original):** `PUBLISHED`, `HAS_HASHTAG`, `MENTIONS`, `TAGS_USER`, `COAUTHORED_BY`, `TAGGED_AT`, `USES_MUSIC`, `WROTE`, `ON`, `LOCATED_AT`, `RELATED_TO`

**Relationships (NLP/Geo):** `MENTIONS_EVENT`, `ORGANIZED`, `PARTICIPATED_IN`, `SUPPORTED`, `LOCATED_IN`

## Key Architecture Notes

- **Incremental processing:** `extract_profiles.py` queries Neo4j for accounts not yet scraped, making re-runs idempotent.
- **Cost control:** Apify usage is tracked in `.apify_cost_log.json` (gitignored). Always check estimated cost before running large extractions.
- **Political filtering:** 13 political accounts are hardcoded in `run_gds_algorithms.py` and down-weighted in the Cultural Relevance Score. Changes to this list require deliberate review.
- **Cultural Relevance Score formula:** components normalized by **percentile rank** (0–1] before weighting: 43.75% PageRank + 31.25% Degree + 25% Betweenness (original proportions 35:25:20), × political penalty. `log(followers)` was **excluded from the score** and is reported as a separate dimension (`popularityScore`); `pageRankPct`/`degreePct`/`betweennessPct` are also persisted.
- **Local analysis (3_analyze_network.py):** offline alternative to GDS (port 7687 is often blocked and standard AuraDB does not include GDS). Exports to `data_processed/*.csv`, runs igraph/leidenalg (exact PageRank and betweenness, Leiden γ=0.5/1.0/1.5 with seed=42, WCC, k-core, participation coefficient, E-I index by actor type) and writes back with `UNWIND` in batches. **Multiplex** network: social layer (MENTIONS/TAGS_USER/COAUTHORED_BY projected author→post→target, since in the raw graph they originate from the Post) separate from the algorithmic layer (RELATED_TO, `Algo` suffix). Actor typology can be curated in `data_processed/actor_types.csv`.
- **GDS graph projection:** Named `"red-cultural"` — must be dropped before re-projecting (`gds.graph.drop("red-cultural")`).
- **raw data:** `data_raw/` is gitignored; JSON files are named `profile_<username>.json`.
- **NLP scripts are all idempotent:** each checks for a `NULL` sentinel property before processing (`bioLanguage`, `captionLanguage`, `eventExtracted`, `lat`).
- **Ingestion provenance:** `2_build_graph.py` sets `firstSeenAt` (`ON CREATE`) and `lastUpdatedAt` (always) on every node it touches (`Account`, `Post`, `Hashtag`, `Location`, `Track`, `Comment`, `IgtvVideo`). Nodes created before this was added have neither property — run `seal_legacy_batch.py` once (before re-running the harvest) to tag that pre-existing state with `legacyBatch = true` / `legacyBatchDate`, so old and new ingestions stay distinguishable and old/irrelevant accounts can be cleaned up later (e.g. `MATCH (a:Account) WHERE a.legacyBatch = true AND a.culturalRelevanceScore < X DETACH DELETE a`).
- **NLP models:** spaCy lazy-loads per language (`es_core_news_lg` / `en_core_web_sm` / `fr_core_news_lg`). Entities stored as `"TYPE:texto"` strings (e.g. `"ORG:Ministerio de Cultura"`). Embeddings via `paraphrase-multilingual-MiniLM-L12-v2` stored as float lists, compatible with Neo4j vector indexes.
- **Event extraction flow:** zero-shot ZS (`cross-encoder/nli-MiniLM2-L6-H768`) classifies caption → NER extracts DATE/LOC/ORG → hotness score → inline resolver checks cosine similarity > 0.82 + location match + date ±3 days before creating `:Event` node. Event IDs are MD5 hashes of `type|raw_date|location`.
- **Event resolver (standalone):** groups `:Event` nodes by `locationName`, compares pairs by date window then cosine similarity, merges duplicates by redirecting all relationships explicitly (no APOC). Canonical = higher `hotnessScore`.
- **Geocoding:** `4_enrich_locations.py` uses Nominatim (1.1 s/req rate limit). Paris arrondissements detected from postal code 750XX. Hierarchy: `Location -[:LOCATED_IN]-> Arrondissement -[:LOCATED_IN]-> City -[:LOCATED_IN]-> Country`. Sessions logged in `.geocoding_log.json` (gitignored).
