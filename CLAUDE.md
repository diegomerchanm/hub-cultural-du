# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Hub Cultural DU** is a data pipeline for mapping and analyzing Colombian cultural networks on Instagram. It scrapes Instagram profiles via Apify, ingests data into a Neo4j graph database, and runs graph algorithms to score cultural influence and detect communities.

**Pipeline Phases:**
1. **Extraction** — `extract_ig_profiles.py` / `extract_ig_posts.py` / `extract_ig_network.py` / `extract_profiles.py` → scrape Instagram via Apify → `data_raw/*.json`
2. **Ingestion** — `load_to_neo4j.py` → loads JSON files into Neo4j Aura
3. **Analysis** — `run_gds_algorithms.py` → runs GDS algorithms and writes scores back to nodes

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run pipeline phases in order
python extract_profiles.py        # Phase 1: scrape pending profiles (cost-aware)
python load_to_neo4j.py           # Phase 2: ingest data_raw/*.json into Neo4j
python run_gds_algorithms.py      # Phase 3: run graph algorithms and score nodes

# Individual scrapers (lower-level)
python extract_ig_profiles.py     # profile metadata
python extract_ig_posts.py        # post timelines
python extract_ig_network.py      # follower networks
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

**Nodes:** `:Account` (`:Public` | `:Private`, optionally `:Political`), `:Post`, `:Hashtag`, `:Location`, `:Track`, `:Comment`, `:IgtvVideo`

**Relationships:** `PUBLISHED`, `HAS_HASHTAG`, `MENTIONS`, `TAGS_USER`, `COAUTHORED_BY`, `TAGGED_AT`, `USES_MUSIC`, `WROTE`, `ON`, `LOCATED_AT`, `RELATED_TO`

## Key Architecture Notes

- **Incremental processing:** `extract_profiles.py` queries Neo4j for accounts not yet scraped, making re-runs idempotent.
- **Cost control:** Apify usage is tracked in `.apify_cost_log.json` (gitignored). Always check estimated cost before running large extractions.
- **Political filtering:** 13 political accounts are hardcoded in `run_gds_algorithms.py` and down-weighted in the Cultural Relevance Score. Changes to this list require deliberate review.
- **Cultural Relevance Score formula:** 35% PageRank + 25% Degree Centrality + 20% Betweenness + 20% log(followers) − political penalty.
- **GDS graph projection:** Named `"red-cultural"` — must be dropped before re-projecting (`gds.graph.drop("red-cultural")`).
- **raw data:** `data_raw/` is gitignored; JSON files are named `profile_<username>.json`.
- **Multilingual NLP:** spaCy models for ES, EN, FR are listed in `requirements.txt` but NLP is not yet wired into the main pipeline.
