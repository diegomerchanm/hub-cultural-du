# Hub Cultural DU

🚧 Work in progress — active research project. The pipeline runs end-to-end and a live dashboard is deployed; scope, scoring and documentation are still evolving.

Mapping and analysis of cultural actor networks on social media. The project scrapes public profiles, builds a graph of the cultural ecosystem in a Neo4j database, and combines graph analysis, NLP and hand-curated categorization to measure cultural relevance and detect communities. Originally focused on the Colombian diaspora in France, the scope has broadened to general cultural accounts in France.

Built to answer questions like: who are the structuring actors of a cultural scene, how do they connect, and which communities and events emerge from their interactions?

## What it does

A data pipeline in six stages:

1. **Extraction** — scrape public Instagram profiles and posts via Apify → raw JSON. Target accounts come from curated seed files (`config/seeds_*.json`) or an incremental, Neo4j-driven scan of accounts not yet scraped.
2. **Ingestion** — load the data into a Neo4j graph (accounts, posts, hashtags, mentions, locations…), with per-node ingestion timestamps (`firstSeenAt` / `lastUpdatedAt`) so old and new batches stay distinguishable.
3. **Graph analysis** — local network metrics (igraph / leidenalg: PageRank, betweenness, Leiden community detection, k-core, participation coefficient) computed offline, no Neo4j GDS server required.
4. **Manual categorization** — a hand-curated spreadsheet (art type, institution type, cultural identity, geographic zone, pricing/free-events info, verified follower counts) uploaded onto accounts, complementing the automated signal rather than replacing it.
5. **Event detection** — zero-shot classification of captions into `:Event` nodes, with inline and standalone de-duplication passes.
6. **Geo** — geocoding of locations (Nominatim) and a Location → Arrondissement → City → Country hierarchy.

A companion Dash dashboard (multi-page, deployed on Render) surfaces the results: an events agenda, a network map, and a filterable account directory built from the manual categorization.

## Key ideas

- **Layered relevance signal** — automated network centrality (`3_analyze_network.py`) and hand-curated categorization (`load_manual_account_categorization.py`) are kept as distinct, non-overlapping property sets on the same `:Account` nodes, so either can be re-run without clobbering the other. An earlier Neo4j GDS-based scoring path (`old/run_gds_algorithms.py`) is archived — it depends on the GDS plugin, which standard AuraDB doesn't include.
- **Multiplex network** — the social layer (mentions / tags / co-authorship) is kept separate from the algorithmic layer, so structural and interaction dynamics can be read independently.
- **Ingestion provenance** — every node touched by the pipeline is timestamped, and a one-time sealing script (`seal_legacy_batch.py`) tags pre-existing, undated data as a distinguishable legacy batch — a prerequisite for ever cleaning up old or irrelevant accounts safely.
- **Manual curation at scale** — for accounts where automated scraping falls short (missing bios, ambiguous categorization), verification is done directly against Instagram (via a browser session) rather than left blank.

## Stack

Python · Apify · Neo4j (Aura) · igraph / leidenalg · spaCy (multilingual) · sentence-transformers · Nominatim · Dash / Plotly / Cytoscape · Render

## Getting started

```bash
pip install -r requirements.txt
```

Create a `.env` file (never committed):

```
APIFY_TOKEN=...
NEO4J_URI=neo4j+s://<host>.databases.neo4j.io
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
```

Run the pipeline (in order):

```bash
python extract_profiles.py                                      # 1. scrape pending profiles (cost-aware)
python 1_harvest_ig_profiles.py --seeds config/seeds_idf.json    #    or: scrape a curated seeds file
python 1_harvest_ig_posts.py --seeds config/seeds_idf.json       #    posts for the same seeds file
python 2_build_graph.py                                          # 2. ingest JSON into Neo4j
python 3_analyze_network.py run-all                              # 3. local graph analysis (igraph/leidenalg)
python load_manual_account_categorization.py                     # 4. upload curated spreadsheet
python 4_enrich_events_extract.py                                # 5a. detect events
python 4_enrich_events_resolve.py                                # 5b. de-duplicate events
python 4_enrich_locations.py                                     # 6. geocode locations
python 5_export_dashboard_data.py                                 # 7. export to site/data.json
```

Then push `site/` to `master` — Cloudflare Workers Builds redeploys automatically. See `CLAUDE.md` for the full command reference, options, and architecture notes.

## Project status

- ✅ Extraction, Neo4j ingestion, local graph analysis, community detection.
- ✅ Manual categorization pipeline (art type, institution, cultural identity, geo zone).
- ✅ Event extraction/de-duplication and geocoding.
- ✅ Static discovery site (`site/`) deployed on Cloudflare Workers Builds, replacing the old Dash dashboard (see DD-044/DD-045 in `docs/decisions_es.md`).
- 🔩 In progress: broader geographic coverage, scoring refinements, French/Spanish documentation parity, clickable map, account image bank.

## Project layout

- Numbered scripts at the repo root (`1_`–`6_`-ish) are the active pipeline, run in order — see `CLAUDE.md`.
- `5_export_dashboard_data.py` + `site/` (HTML/CSS/JS, no build step) — the live discovery site, deployed on Cloudflare.
- `config/` — seed lists and account tiers. `docs/` — project notes.
- `old/` — superseded or environment-incompatible scripts, kept for reference, not run. This includes the retired Dash dashboard (`5_visualize_dashboard.py`, `dash_common.py`, `pages/`), replaced by `site/`.
- `testing/` — verification/diagnostic scripts, useful for QA but outside the main pipeline flow.

## Data & ethics

Uses public profile data for research on cultural ecosystems. Credentials and raw data live in gitignored files (`.env`, `data_raw/`). Political accounts are explicitly separated from the cultural signal.

## Author

Diego Merchán — [github.com/diegomerchanm](https://github.com/diegomerchanm)
