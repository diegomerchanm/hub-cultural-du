# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Hub Cultural DU** is a data pipeline for mapping and analyzing cultural networks (originally Colombian diaspora, now general cultural accounts in France too) on Instagram. It scrapes Instagram profiles via Apify, ingests data into a Neo4j graph database, and combines automated graph analysis with hand-curated categorization to score cultural relevance and detect communities.

**Pipeline Phases:**
1. **Extraction** — `1_harvest_ig_profiles.py` / `1_harvest_ig_posts.py` / `extract_profiles.py` → scrape Instagram via Apify → `data_raw/*.json`. Target usernames come either from `config/seeds_*.json` (`--seeds`, recommended) or, for `1_harvest_ig_posts.py` only, from `data_processed/account_scores.csv` as a legacy fallback (see `old/1_harvest_account_classifier.py`).
2. **Ingestion** — `2_build_graph.py` → loads JSON files into Neo4j Aura
3. **Analysis** — `3_analyze_network.py` → local graph metrics (igraph/leidenalg — no GDS plugin needed, works on standard AuraDB). `old/run_gds_algorithms.py` is archived: it depends on the Neo4j GDS plugin, which standard AuraDB doesn't include, so it never runs successfully in this project's setup.
4. **Manual categorization** (complementary to automated analysis, not a replacement) — `load_manual_account_categorization.py` → uploads hand-curated fields (art type, institution type, cultural identity, geographic zone, pricing/free-events info, verified follower counts) from a curated spreadsheet onto `:Account` nodes by `username`.
5. **Events** — `4_enrich_events_extract.py` (zero-shot classification → `:Event` nodes with inline dedup) → `4_enrich_events_resolve.py` (standalone dedup pass for existing `:Event` nodes)
6. **Geo** — `4_enrich_locations.py` → Nominatim geocoding + `:LOCATED_IN` hierarchy

**Folder conventions:** `old/` holds scripts that are superseded or don't work in this project's actual environment (kept for reference, not run). `testing/` holds verification/diagnostic scripts (`verify_events_extraction.py`, `check_geotag_coverage.py`, `test.py`, `plot_eventscore_boxplot.py`) that are still useful for QA but aren't part of the main pipeline flow.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Full pipeline (phases in order)
python extract_profiles.py                  # Phase 1: scrape pending profiles (cost-aware, Neo4j-driven)
python 1_harvest_ig_profiles.py --seeds config/seeds_idf.json   # Phase 1 alt: scrape a curated seeds file
python 1_harvest_ig_posts.py --seeds config/seeds_idf.json      # Phase 1: posts for the same seeds file
python 2_build_graph.py                     # Phase 2: ingest data_raw/*.json into Neo4j
python 3_analyze_network.py run-all         # Phase 3: local analysis (igraph/leidenalg) — no GDS or port 7687
python 3_analyze_network.py analyze         #   metrics only, 100% offline from data_processed/*.csv
python load_manual_account_categorization.py --dry-run   # Phase 4: preview curated-spreadsheet upload
python load_manual_account_categorization.py             #   write it to Neo4j (idempotent, safe to re-run)
python seal_legacy_batch.py --dry-run       # One-time: preview which nodes have no ingestion timestamp yet
python seal_legacy_batch.py                 #   tag them legacyBatch=true before the next harvest run
python 4_enrich_events_extract.py           # Phase 5-A: detect events → :Event nodes
python 4_enrich_events_resolve.py           # Phase 5-B: dedup existing :Event nodes
python 4_enrich_locations.py                # Phase 6: geocode :Location + hierarchy

# Key options
python 4_enrich_events_extract.py --threshold 0.6 --max-posts 500 --dry-run
python 4_enrich_events_resolve.py --threshold 0.85 --dry-run
python 4_enrich_locations.py --city-hint "Paris, France" --dry-run
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
- **Political filtering (archived):** 13 political accounts were hardcoded in `old/run_gds_algorithms.py` and down-weighted in the Cultural Relevance Score. Since that script is archived (see below), the `:Political` label is no longer being freshly applied to new accounts — this filtering is currently dormant, not something any active script maintains.
- **Cultural Relevance Score (archived):** lived entirely in `old/run_gds_algorithms.py` — percentile-rank blend of PageRank/Degree/Betweenness × political penalty, `followersCount` reported separately as `popularityScore`. Archived because it depends on the Neo4j GDS plugin, which standard AuraDB doesn't include (it never ran successfully in this project's actual environment). `3_analyze_network.py` is the live alternative for relevance/centrality signal — check its actual output property names in `data_processed/*.csv` before assuming they match the old GDS-era names.
- **Local analysis (3_analyze_network.py):** offline alternative to GDS (port 7687 is often blocked and standard AuraDB does not include GDS) — this is the live, working phase-3 script. Exports to `data_processed/*.csv`, runs igraph/leidenalg (exact PageRank and betweenness, Leiden γ=0.5/1.0/1.5 with seed=42, WCC, k-core, participation coefficient, E-I index by actor type) and writes back with `UNWIND` in batches. **Multiplex** network: social layer (MENTIONS/TAGS_USER/COAUTHORED_BY projected author→post→target, since in the raw graph they originate from the Post) separate from the algorithmic layer (RELATED_TO, `Algo` suffix). Actor typology can be curated in `data_processed/actor_types.csv`.
- **Manual categorization:** `load_manual_account_categorization.py` uploads a hand-curated spreadsheet (art type, institution type, event frequency, parent institution, content type, verified follower count, free-events/pricing info, cultural identity, geographic zone — see the script's `COLUMN_MAP`) onto `:Account` nodes via `MERGE` by `username`. Property names (`artType`, `culturalIdentity`, `geoZone`, `manualFollowersCount`, etc.) are deliberately distinct from what `2_build_graph.py` writes, so re-running either script never clobbers the other's data. Idempotent, safe to re-run whenever the source spreadsheet changes.
- **raw data:** `data_raw/` is gitignored; JSON files are named `profile_<username>.json`.
- **NLP scripts are idempotent:** each checks for a `NULL` sentinel property before processing (`eventExtracted`, `lat`). The bio/caption language+NER+keywords enrichment (`old/4_enrich_nodes_nlp.py`) is archived — nothing downstream in the active pipeline reads `bioLanguage`/`captionLanguage`/`bioEntities`/`captionEntities`/`bioKeywords`/`captionKeywords`/`bioEmbedding`/`captionEmbedding`, so it was cut rather than kept as dead weight. Revive from `old/` if a future feature (language-based filtering, topic clustering) actually needs it.
- **Ingestion provenance:** `2_build_graph.py` sets `firstSeenAt` (`ON CREATE`) and `lastUpdatedAt` (always) on every node it touches (`Account`, `Post`, `Hashtag`, `Location`, `Track`, `Comment`, `IgtvVideo`). Nodes created before this was added have neither property — run `seal_legacy_batch.py` once (before re-running the harvest) to tag that pre-existing state with `legacyBatch = true` / `legacyBatchDate`, so old and new ingestions stay distinguishable and old/irrelevant accounts can be cleaned up later (e.g. `MATCH (a:Account) WHERE a.legacyBatch = true AND coalesce(a.manualFollowersCount, a.followersCount, 0) < X DETACH DELETE a` — `culturalRelevanceScore` is no longer a reliable relevance signal here since the script that computed it is archived, see below).
- **NLP models:** spaCy lazy-loads per language (`es_core_news_lg` / `en_core_web_sm` / `fr_core_news_lg`). Entities stored as `"TYPE:texto"` strings (e.g. `"ORG:Ministerio de Cultura"`). Embeddings via `paraphrase-multilingual-MiniLM-L12-v2` stored as float lists, compatible with Neo4j vector indexes.
- **Event extraction flow:** zero-shot ZS (`cross-encoder/nli-MiniLM2-L6-H768`) classifies caption → NER extracts DATE/LOC/ORG → hotness score → inline resolver checks cosine similarity > 0.82 + location match + date ±3 days before creating `:Event` node. Event IDs are MD5 hashes of `type|raw_date|location`.
- **Event resolver (standalone):** groups `:Event` nodes by `locationName`, compares pairs by date window then cosine similarity, merges duplicates by redirecting all relationships explicitly (no APOC). Canonical = higher `hotnessScore`.
- **Geocoding:** `4_enrich_locations.py` uses Nominatim (1.1 s/req rate limit). Paris arrondissements detected from postal code 750XX. Hierarchy: `Location -[:LOCATED_IN]-> Arrondissement -[:LOCATED_IN]-> City -[:LOCATED_IN]-> Country`. Sessions logged in `.geocoding_log.json` (gitignored).
