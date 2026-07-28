# Hub Cultural — Architecture Decision Records

> Living document recording the technical and methodological decisions
> of the project, their rationale, and the alternatives considered.
> Foundation for the methodology chapter of the mémoire.

---

## DD-001 — Neo4j as primary database

**Date:** June 2026
**Decision:** Use Neo4j AuraDB (cloud) as the project's primary storage.
**Rationale:** Instagram data is naturally relational — accounts that mention accounts, posts that tag users, hashtags shared across publications. A graph captures this structure natively. Network queries (shortest paths, neighborhoods, centrality) are 10–100x more efficient in Cypher than in SQL.
**Alternative considered:** PostgreSQL with relationship tables or document-oriented MongoDB.
**Why rejected:** Network analysis queries are complex and slow in SQL. MongoDB has no native support for graph algorithms.

---

## DD-002 — Apify as harvesting platform

**Date:** June 2026
**Decision:** Use Apify Cloud with `instagram-profile-scraper` and `instagram-post-scraper` actors.
**Rationale:** Instagram actively blocks direct scraping. Apify maintains specialized actors that bypass these protections, with cloud infrastructure that avoids IP blocks. The pay-per-use model (FinOps) allows cost control per query.
**Alternative considered:** Custom Selenium/Playwright, Instaloader.
**Why rejected:** High maintenance burden given Instagram changes, IP block risk, no cloud infrastructure.

---

## DD-003 — Separate scripts by responsibility

**Date:** June 2026
**Decision:** Separate scripts for profiles (`1_harvest_ig_profiles.py`) and posts (`1_harvest_ig_posts.py`) instead of a single harvester.
**Rationale:** Profiles and posts have completely different JSON structures, different Apify actors, and different update frequencies. Keeping them separate allows independent execution and granular cost control.
**Alternative considered:** A single script that extracts everything.
**Why rejected:** Excessive coupling, hard to maintain and to control costs.

---

## DD-004 — Graph model: nodes and relationships

**Date:** June 2026
**Decision:** Model with 7 node types (Account, Post, IgtvVideo, Hashtag, Location, Track, Comment) and 10 relationship types (PUBLISHED, HAS_HASHTAG, MENTIONS, TAGS_USER, COAUTHORED_BY, TAGGED_AT, USES_MUSIC, WROTE, ON, RELATED_TO).
**Rationale:** Captures both content (posts, hashtags) and social relationships (mentions, tags) and cultural context (music, location). Enables multidimensional network analysis.
**Alternative considered:** Simplified model with only Account and Post.
**Why rejected:** Would lose valuable information about events (Location), cultural trends (Hashtag, Track), and collaborations (COAUTHORED_BY).

---

## DD-005 — :Public/:Private labels on Account nodes

**Date:** June 2026
**Decision:** Use additional Neo4j labels (`:Public`, `:Private`) instead of just a boolean property.
**Rationale:** Labels in Neo4j allow more efficient filtering in Cypher (`MATCH (a:Account:Public)`) and are visually distinguishable in Neo4j Browser with different colors.
**Alternative considered:** Property `a.isPrivate = true/false`.
**Why rejected:** Boolean properties do not allow indexed filtering as efficiently as labels in Neo4j.

---

## DD-006 — Network expansion strategy: BFS from seed

**Date:** June 2026
**Decision:** Use `@consuladocolparis` as the seed account and expand the network via mentions, tags, and relatedProfiles instead of random harvesting.
**Rationale:** The consulate is the most central institutional node of the Colombian diaspora in Paris — all relevant accounts are 1–3 degrees away. BFS expansion guarantees that every newly discovered account has a real connection to the community.
**Alternative considered:** Manually curated list of Colombian accounts in Paris.
**Why rejected:** Researcher bias, not scalable, misses unexpected connections.

---

## DD-007 — Semantic embeddings over keywords for event detection

**Date:** July 2026
**Decision:** Use `paraphrase-multilingual-MiniLM-L12-v2` as Layer 1 for event detection instead of hardcoded keywords.
**Rationale:** Instagram posts mix Spanish, French, and English with emojis and slang. Keywords fail with spelling variations, multilingualism, and indirect forms of announcing events. Embeddings capture the concept of "future event at a physical place" regardless of language or exact wording. As concluded during development: "classifying by keywords in the AI era makes no sense."
**Alternative considered:** Regex + hardcoded keywords, spaCy EntityRuler.
**Why rejected:** Fragile against variations, requires constant maintenance, not multilingual by design.

---

## DD-008 — Multilingual mDeBERTa over monolingual NLI

**Date:** July 2026
**Decision:** Use `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` for zero-shot event type classification.
**Rationale:** The previous model (`cross-encoder/nli-MiniLM2-L6-H768`) was trained on MNLI in English — when fed Spanish and French captions the result was near chance. The multilingual mDeBERTa was trained on XNLI across 15 languages including Spanish and French.
**Alternative considered:** `cross-encoder/nli-MiniLM2-L6-H768` (English monolingual).
**Why rejected:** Detection rate of 0.7% — unacceptable. Bug identified by external review (Fable).

---

## DD-009 — 2-layer architecture for event detection

**Date:** July 2026
**Decision:** Two-layer pipeline: Layer 1 (semantic similarity with embeddings) filters candidates, Layer 2 (zero-shot NLI) classifies the event type only on candidates.
**Rationale:** Running the NLI model on all posts takes 5–7 hours on an i5 CPU. Using embeddings as a pre-filter reduces candidates to ~40% of the corpus, bringing total time down to ~18 minutes. Layer 1 is 100x faster and precise enough for initial filtering.
**Alternative considered:** Zero-shot only over all posts, fine-tuned SetFit.
**Why rejected:** Zero-shot alone: too slow. SetFit: requires ~200 manually labeled examples and a GPU — out of scope for v1.

---

## DD-010 — 100 reference phrases for Layer 1

**Date:** July 2026
**Decision:** Use 100 reference phrases covering 8 event types in 3 languages and 6 announcement forms (call for entries, opening, agenda, date+location, registration, reminder).
**Rationale:** With few references (<15), the semantic comparison space is too narrow — posts with indirect announcement forms are not detected. 100 phrases sufficiently cover the semantic space without being redundant.
**Alternative considered:** 15 generic phrases.
**Why rejected:** Insufficient detection rate — the most interesting posts (small community events) use announcement forms not covered by few references.

---

## DD-011 — nlp_event_resolver for semantic deduplication

**Date:** July 2026
**Decision:** Separate script that merges duplicate events using a triple criterion: same normalized Location + date ±3 days + cosine similarity > 0.75.
**Rationale:** Multiple accounts post about the same event (DichaFest appeared in 6–7 posts from different accounts). Without deduplication, the same event is created multiple times with fragmented data. The triple signal prevents incorrect merges between distinct events that share only one criterion.
**Alternative considered:** Semantic similarity alone, DBSCAN clustering.
**Why rejected:** Similarity only: merges distinct events at the same venue. DBSCAN: requires defining epsilon without calibration data.

---

## DD-012 — Political filter: penalty vs. total exclusion

**Date:** July 2026
**Decision:** Political accounts (`@gustavopetrourrego`, `@registraduria`, etc.) receive a penalty in the `culturalRelevanceScore` (weight = 0.1) rather than being excluded from the graph.
**Rationale:** Excluding them would remove real connections — the consulate interacts with these accounts and that interaction is valid data. The penalty keeps them in the graph for network analysis but makes them irrelevant for seed recommendations and the cultural dashboard.
**Alternative considered:** Total exclusion from the graph, exclusion only from the dashboard.
**Why rejected:** Total exclusion distorts centrality algorithms by removing nodes with many real connections.

---

## DD-013 — Local NetworkX/igraph over Neo4j GDS

**Date:** July 2026
**Decision:** Run network analysis algorithms (PageRank, Betweenness, Leiden) locally with igraph (C) instead of using Neo4j Graph Data Science.
**Rationale:**
1. Neo4j Aura port 7687 is blocked on the corporate network — GDS requires a persistent connection.
2. igraph in C is faster than GDS for graphs of this size (~7,000 nodes).
3. CSV results are git-versionable and reproducible without a connection.
4. GDS on Aura Free has undocumented memory limits.
**Alternative considered:** Neo4j GDS via Bolt, Google Colab with GDS.
**Why rejected:** Dependency on network and cloud credentials for every execution.

---

## DD-014 — Full graph for algorithms, tier filter only in reporting

**Date:** July 2026
**Decision:** Centrality algorithms (PageRank, Betweenness, Leiden) run on the full graph (4,637 nodes). The tier filter (primary/secondary/excluded) is applied only to the final report and seed selection.
**Rationale:** `@dichaparis`'s PageRank depends on who mentions it — including `unknown` accounts. If the graph is filtered before running algorithms, that context is lost and rankings are less representative of each account's real importance in the network.
**Alternative considered:** Filter nodes and edges before building the igraph graph.
**Why rejected:** With ~36 classified nodes out of 4,637 total, the filtered graph is too small for algorithms to be statistically meaningful.

---

## DD-015 — Instagram businessCategoryName as actorType source

**Date:** July 2026
**Decision:** Use the `businessCategoryName` field from the Instagram API (via Apify) as the primary account type classifier, complemented by `manual_overrides` in `config/account_tiers.json`.
**Rationale:** Instagram already classifies its business accounts with precise categories (Artist, Restaurant, NGO, Politician, etc.). This is more reliable than keyword heuristics on the username or bio. `manual_overrides` allow correcting incorrect classifications without touching code.
**Alternative considered:** Zero-shot classification on the bio, keyword heuristics on username.
**Why rejected:** Keywords: fragile and requires maintenance. Zero-shot: adds latency and unnecessary computational cost when Instagram already has the classification.

---

## DD-016 — Tier system for account prioritization

**Date:** July 2026
**Decision:** Three account tiers based on `businessCategoryName`:
- **Primary:** Artist, Restaurant, Community, Local business, Podcast, Art Gallery, Journalist — core of cultural analysis.
- **Secondary:** Language School, NGO, Education, University, Digital creator, Public figure, Entrepreneur — relevant context but not priority.
- **Excluded:** Financial service, Politician, Real Estate, Tour Agency, Government — noise or out of cultural scope (exception: Bloc A seed accounts from DD-022 remain active as structural anchors despite this tier — see DD-026).
**Rationale:** Not all accounts have the same value for the project's objective. Financial and political institutions distort cultural rankings. Tiers enable granular analysis without losing data.
**Update (July 2026):** Digital creator, Public figure, and Entrepreneur moved from primary to secondary. Justification: ambiguous categories that do not guarantee primary cultural content — they may be relevant but not priority. A "Digital creator" can be a fashion or fitness influencer with no Colombian cultural connection; a "Public figure" can be a politician or athlete.
**Alternative considered:** Include everything without a filter, manually exclude case by case.
**Why rejected:** No filter: rankings dominated by accounts with millions of followers and no cultural relevance. Manual: not scalable.

---

## DD-017 — Historical storage of analysis runs

**Date:** July 2026
**Decision:** Each execution of `3_analyze_network.py analyze` saves results in `data_processed/runs/YYYYMMDD_HHMMSS_{label}/` in addition to updating the root files.
**Rationale:** Network analysis is recalculated every time data is added to the graph. Saving historical runs allows comparing how rankings evolve as the corpus grows — evidence of the iterative process for the mémoire.
**Alternative considered:** Always overwrite the same files.
**Why rejected:** Loses traceability of analysis evolution — impossible to compare V1 with V2.

---

## DD-018 — Nominatim (OpenStreetMap) for geocoding

**Date:** July 2026
**Decision:** Use Nominatim (OpenStreetMap free API) to geocode Location nodes extracted by NER.
**Rationale:** Free, no API key required, covers Paris and Colombia with good accuracy. The 1 req/s rate limit is manageable for the project volume (~215 locations).
**Alternative considered:** Google Maps Geocoding API, HERE Maps.
**Why rejected:** Google Maps: paid with insufficient free request limit. HERE: requires registration and API key.

---

## DD-019 — harvest/build/analyze/enrich/visualize naming convention

**Date:** July 2026
**Decision:** Rename all scripts with a descriptive prefix + stage number (`1_harvest_`, `2_build_`, `3_analyze_`, `4_enrich_`, `5_visualize_`).
**Rationale:** The original naming (`extract_profiles.py`, `nlp_extract_events.py`) did not communicate execution order or each script's role. The new scheme is self-descriptive — any collaborator understands the pipeline without reading documentation.
**Alternative considered:** Keep original names, use numbers only.
**Why rejected:** Numbers only: not descriptive. Original names: do not communicate order or responsibility.

---

## DD-020 — Low graph density as methodological limitation

**Date:** July 2026
**Decision:** Explicitly document the low graph density (2,047 nodes / 2,300 edges, ratio ~1.1 edges/node) as a v1 methodological limitation rather than trying to hide it.
**Rationale:** The limitation is real and affects the discriminative power of centrality algorithms. Documenting it honestly and proposing V2 BFS as the solution is more academically rigorous than presenting results without context.
**Implication for V2:** Harvesting 50 posts for each of the 170 current accounts (instead of 12) would triple graph density without needing to expand the number of accounts.
**Reference:** Low density can also be interpreted as a sociological finding — a very sparse network may indicate fragmentation of the Colombian diaspora in France, consistent with literature on diasporas in host countries with high individualization (Vertovec, 2009).

---

---

## DD-022 (update) — V2 seeds: consulates + cultural institutions

**Date:** 2026-07-07

Context: consulates know their communities — their relatedProfiles and mentions are a map of the real diaspora. The seed criterion is expanded to include French cultural institutions related to Latin America.

Decision: the importance/tier classification of each account is NOT defined manually here. It will be determined afterward through follower/relatedProfile harvesting (`1_harvest_ig_profiles.py`) and NLP classifier scoring (`1_harvest_account_classifier.py` — geography_score + cultural_score + anti-embeddings, DD-023). This block only fixes the initial seed set, not each account's final tier.

### Bloc A — Latin American consulates and embassies in France

| Country | IG Handle | Type | Confidence |
|---|---|---|---|
| Colombia | @consuladocolparis | Consulate | High |
| Colombia | @embajadacolfra | Embassy | High |
| Argentina | @arg_enfrancia | Embassy | Medium-high |
| Brazil | @cg_brasil_paris | Consulate | Medium (verify vs @cgparisoficial) |
| Brazil | @bresilenfrance | Embassy | High |
| Chile | @embachilefrancia | Embassy | Medium |
| Mexico | @embajadademexicoenfrancia | Embassy | Medium-high |
| Peru | @consuladodelperuenparis | Consulate | High |
| Venezuela | @embfrancia_ve | Embassy | Low (verify vs @embavefrancia) |
| Ecuador | @eecufrancia | Embassy | Low (verify vs @embajadaecufrancia) |
| Uruguay | @uruguayfrancia | Embassy | High |
| Bolivia | — | Not found on IG | — |
| Costa Rica | @costaricafrance | Embassy | Medium-high |
| Guatemala | @embaguafr | Embassy | High |
| Dominican Republic | @rdenfrancia | Embassy | High |
| Dominican Republic | @rdenparis | Consulate | High |
| Panama | @embpanamafra | Embassy | High |
| Cuba | @embacubafrancia | Embassy | High |
| El Salvador | — | Not found on IG | — |
| Honduras | @embajadadehondurasenfrancia | Embassy | Medium-high |
| Nicaragua | — | Not found on IG | — |
| Paraguay | — | Not found on IG | — |

### Bloc B — French cultural/academic institutions related to Latin America

| Account | IG Handle | Type |
|---|---|---|
| Maison de l'Amérique Latine | @maisondelameriquelatineparis | Cultural institution (1946) |
| Instituto Cervantes París | @institutocervantesparis | Spanish cultural institute |
| France Diplomatie (ES) | @francediplo_es | French institutional (Spanish) |
| IHEAL & CREDA | @iheal_creda | Academic center (Sorbonne Nouvelle) |
| Festival CLaP | @festivalclap | Latin American film festival in Paris |
| GRULAC UNESCO | @grulacunesco | LatAm/Caribbean diplomatic group at UNESCO |
| El Café Latino | @elcafelatino | Bilingual media outlet on Latin America in Europe |

Explicitly excluded: Alliances Françaises outside France, commercial accounts (e.g. Maison de l'Amérique Latine restaurant).

Pending manual verification by Diego:
- Bolivia, El Salvador, Nicaragua, Paraguay — no confirmed IG handle.
- Brazil, Venezuela, Ecuador — two candidates, choose the active account before running the harvester.

---

---

## DD-025 — Empty accounts as organic prospect list

**Date:** July 2026
**Decision:** Treat the ~4,467 empty accounts in Neo4j as a priority prospect list for V2, before looking for external new seeds.
**Rationale:** These accounts were discovered organically because the Colombian diaspora already mentioned, tagged, commented on, or related to them. Their origin confirms relevance:
- 1,530 via comments on consulate posts
- 1,001 via relatedProfiles of 36 harvested profiles
- 906 via mentions in posts
- 740 via tags in posts
- 290 unknown origin (multiple sources)

relatedProfiles are especially valuable — 36 profiles potentially generate ~1,300 unique new accounts without needing to harvest followers. Estimated cost to harvest all their profiles: ~$0.65 USD.

**Implication for V2:** Run `1_harvest_ig_profiles.py` on empty accounts filtered by the NLP classifier before looking for external seeds — they are candidates with organically confirmed relevance.
**Alternative considered:** Look for new external seeds (Latin American consulates) as the first V2 action.
**Why both complement each other:** External seeds expand scope to all of Latin America; empty accounts deepen the existing Colombian corpus.

---

## DD-026 — Consulates as structural anchors vs. classifiable cultural targets

**Date:** 2026-07-09
**Decision:** Bloc A V2 seed accounts (Latin American consulates and embassies in France, DD-022) receive `role = "seed_source"` and `keep = False` unconditionally in `1_harvest_account_classifier.py`, regardless of their `final_score`. This is implemented in the `_finalize()` function (lines ~531–540): if the account is a seed and belongs to Bloc A, `keep` is overwritten to `False` regardless of the calculated score.

**Rationale — the contradiction it resolves:** DD-016 classifies "Government organization" in the Excluded tier, since government institutions distort cultural relevance rankings. But DD-006 and DD-022 use precisely government institutions (consulates/embassies) as the structural seed of all network discovery — without them there is no BFS, no `relatedProfiles`, no expansion. Applying DD-016 literally would exclude from the graph the very account that makes the graph possible.

The resolution separates two distinct questions that were previously resolved with a single criterion:
1. "Is this account a valid cultural target for relevance analysis?" → For consulates/embassies: NO (they behave exactly as DD-016 predicts — they are government, not culture).
2. "Should this account remain active as a node/anchor to discover the network?" → YES, always — its function is not to be evaluated, but to generate the connections that will be evaluated.

The high score these accounts obtain (e.g. @consuladocolparis: geo=1.00, cult=0.88, final=0.94) is correct and is not discarded — it confirms the model detects geography+culture well — but it does not translate into `keep=True` because `keep` answers question 1, not question 2.

**Distinction from DD-012:** DD-012 penalizes (does not exclude) individual political accounts to preserve real edges in the graph. DD-026 is a different mechanism: not a score penalty, but a role separation (seed_source vs. target) that applies only to Bloc A institutional seeds, not to political accounts discovered organically.

**Alternative considered:** Apply DD-016 without exception (also exclude consulates from the active graph); create an additional "institutional-seed" tier with its own scoring rules.
**Why rejected:** Excluding consulates from the active graph breaks the BFS discovery chain (DD-006) — there would be no way to find diaspora accounts without the account that connects them. A new tier would add unnecessary scoring complexity when the real problem is about role (source vs. target), not score.

---

## DD-027 — Data completeness metric as diagnostic (does not affect scoring yet)

**Date:** 2026-07-09
**Context:** After the first harvest of 25 V2 seeds, the graph grew from 4,637 to 5,433 :Account nodes (+796). Of those, 2,665 new accounts have `fullName` (arrived via relatedProfiles/taggedUsers/coauthorProducers) and 2,575 have no fields beyond the username (arrived via mentions or comments). `1_harvest_account_classifier.py` did not distinguish this — it treated all accounts without a harvested profile with the same fixed confidence factor (USERNAME_CONF=0.60), regardless of how much real evidence was available. Additionally, the export to nodes.csv was losing the `verified`, `private`, and `profilePicUrl` properties that do arrive in Neo4j from 2_build_graph.py.

**Decision:** Add `data_completeness` (0–1, count of non-null fields out of 5: fullName, followers, public, verified, profilePicUrl) as a diagnostic column in account_scores.csv and in the `--diagnose` output. For now it does NOT modulate the `final_score` calculation or the keep threshold — it is purely diagnostic so that visual threshold analysis (by eye, as agreed) takes into account the quality of evidence behind each score, not just the score itself.
**Rationale:** Comparing a final_score=0.45 from an account with a real bio+posts against a final_score=0.45 from a username-only account is not comparing the same thing — the validity of the measurement differs. Documenting the metric before deciding how to use it avoids prematurely committing to a weighting formula without having seen the real distribution.
**Alternative considered:** Automatically modulate USERNAME_CONF by completeness from the start.
**Why deferred:** Diego wants to review the real distribution of data_completeness crossed with final_score before deciding if and how it should weigh — avoids tuning a formula with data not yet seen.

---

## DD-028 — Recent posts over historical density

**Date:** 2026-07-12
**Decision:** `1_harvest_ig_posts.py` filters by `onlyPostsNewerThan` (default 10 days) instead of just a quantity cap (RESULTS_LIMIT=50 without temporal filter, as in V1/RUN-003). Account list generalized from account_scores.csv (keep=True), with manual exclusion of `williamsanchezinmobiliaria` (classifier false positive — business category not captured by the tier).
**Rationale:** DD-020 (V1) sought to maximize graph density by harvesting more posts per account, under the logic that more social edges = better discrimination of centrality algorithms (GDS/igraph). In V2, the cultural suitability of an account no longer depends on those algorithms — it is resolved directly by the NLP classifier on bio+posts+username (DD-023, DD-027). This frees the posts phase from the responsibility of generating density, and allows prioritizing the real objective of the event pipeline (4_enrich_events_extract.py): capturing current event announcements, not reconstructing history.
**Alternative considered:** Keep RESULTS_LIMIT=50 without temporal filter (as V1).
**Why rejected:** Brings posts from months/years ago that do not contribute to detecting upcoming events, and dilutes the corpus with outdated content that no longer represents the current cultural activity of the diaspora.
**Accepted risk:** Institutional accounts with low publication frequency may end up with 0 posts in the 10-day window — diagnosed in the run (point 3) and reviewed case by case if the volume of "empties" is high.

---

## DD-029 — Dynamic harvest window per account + sliding cap of 50 posts

**Date:** 2026-07-13
**Decision:** `1_harvest_ig_posts.py` replaces the current binary active/expired check (DD-028) with an `onlyPostsNewerThan` window calculated dynamically per account: min(days since last known post, --days cap). Accounts with gap <1 day are skipped; the rest are re-checked with exactly the window they need, not a fixed global value. Results are merged with existing posts (dedupe by id) and trimmed to the 50 most recent (sliding window), instead of overwriting the full file.
**Rationale:** The binary check of DD-028 had a blind spot: an account with a post from 2 days ago would be marked "active" under any window ≥2 days and skipped entirely, missing posts published in the interval between that known post and "now." The dynamic window closes exactly that gap per account, without over-spending on accounts already nearly up to date or falling short on accounts with recent activity just outside the binary check's radar.
**Alternative considered:** Keep fixed global window (DD-028 as-is) and accept the blind spot.
**Why rejected:** The marginal cost of querying with a small window is nearly zero (empirically confirmed: $0.00–0.02 per account in previous runs), so there is no strong reason to tolerate the blind spot just for call savings.
**Technical note:** The 50-post cap (RESULTS_LIMIT) now functions as a cumulative sliding window, not a single-run limit — the corpus per account converges to "the 50 most recent known posts to date," updated incrementally on each run.

---

## DD-030 — Explicit detection of Apify "no_items" error placeholder

**Date:** 2026-07-15
**Decision:** Add `_is_error_placeholder(items)` in `1_harvest_ig_posts.py` to detect the case where `apify/instagram-post-scraper` returns a 1-element list with key `"error"` and no `"id"`, before passing to the merge branch. The check in `scrape_posts()` changes from `if not dataset_items` to `if not dataset_items or _is_error_placeholder(dataset_items)`.
**Rationale:** When the actor finds no content (private account, no posts in the window, or restricted profile), it returns:
`[{"url": ..., "inputUrl": ..., "requestErrorMessages": [], "error": "no_items", "errorDescription": "Empty or private data for provided input"}]`.
This 1-element list is **truthy** in Python — `if not dataset_items` never entered the diagnostic branch. The result was: (a) `window_empty`/`no_content`/`unknown` counters were not incremented, (b) the log recorded 1 false "post," and (c) in versions prior to DD-029 (without merge_and_cap), the existing file was overwritten with empty data or the placeholder itself. Confirmed as the cause of data loss in RUN-013 for elcafetal.paris, educulturaco, and ivan_argote.
**Detection criterion:** `len(items) == 1 and "error" in items[0] and "id" not in items[0]` — conservative: does not filter real posts with a single item, since real posts always have `"id"`.
**Alternative considered:** Check only `items[0].get("error") == "no_items"` (hardcoded string).
**Why rejected:** The composite condition (len==1 + "error" in item + no "id") is more robust to changes in the exact error field string, and harder to accidentally trigger against a real post.

---

## DD-031 — Geographic bounding box to penalize accounts outside France

**Date:** 2026-07-15
**Decision:** In `geo_hard_signals()`, before flattening `businessAddress` to a string, preserve the original dict to read `latitude`/`longitude`. If the coordinates fall outside the metropolitan France bounding box (`lat: 41.0–51.5, lon: -5.5–9.7`), apply `penalty = 0.90` to the `geography_score` (`geography = max(0.0, geography - 0.90)`). The previous fallback (searching for LatAm cities in bio) is reduced to `penalty = 0.35` and only activates when there is no lat/lon in businessAddress AND no positive France signals (empty signals list) — to avoid penalizing legitimate diaspora patterns like "from Bogotá to Paris."
**Finding that motivated the change:** Manual verification of raw JSONs confirmed that `businessAddress` has real `latitude`/`longitude` in almost all populated cases, but does NOT have `countryCode`. The original bug (DD-030 predecessor) was thought to affect only Colombian Alianzas Françaises, but verifying `businessAddress` in real profiles revealed four additional affected accounts: `williamsanchezinmobiliaria` (Spain), `unadunioneuropea` (Madrid), `embcolghana` (Ghana), `remaxmariavillasmil02` (Venezuela) — all with lat/lon clearly outside France but without LatAm city text in their bio, which made the bug invisible to the previous fallback.
**Why bbox instead of city list:**
- Generalizes to any country without list maintenance.
- Detects coordinates from Madrid, Accra, Caracas, Bogotá, etc. with the same rule, without needing to add each new case.
- City lists have false positives ("Cartagena" in Spain, "Valencia" in Venezuela), the bbox does not.
**Accepted limitation:** The bbox excludes French DOM-TOMs (Guadeloupe, Martinique, Réunion, etc., lat < 41.0 or lon outside the range). Deliberate decision: the project focuses on the Latin American diaspora in metropolitan France/Île-de-France. If DOM-TOMs are to be covered in the future, revise `FRANCE_BBOX` or add sub-bboxes by region.
**Alternative considered:** List of valid `countryCode` values for France.
**Why rejected:** `businessAddress` from the Instagram API does not include `countryCode` — field absent in verified real data.

---

## DD-032 — Scoped rule for Alliance Française branches outside France

**Date:** 2026-07-15
**Decision:** Add to `geo_hard_signals()` a check independent of the bio-city fallback (DD-031): if the profile username matches `AF_SATELLITE_PATTERN` (`alian[cz]a.{0,4}frances|alliance.{0,4}fran[cç]aise`) AND contains a token from `NON_FRANCE_CITY_MARKERS`, apply `penalty = max(penalty, 0.90)`. This check is not gated by "no positive France signals" — unlike the bio fallback — because the username+LatAm city pattern is structurally unambiguous: an Alliance Française branch with a LatAm city name in the handle is by definition a branch outside France.
**Residual that motivated the change:** `alianzafrancesademedellin` (Medellín, Colombia) passed the bbox fix (DD-031) because its `businessAddress` has no `latitude`/`longitude`. Its bio mentions "Francia" as a topic ("¡Aprende francés! ..."), not as a location → triggers `sem_geo:1.00` and `bio:FR`, which blocks the bio-city fallback by design. It was the only incorrect `keep=True` after DD-031.
**Why scoped rule to the AF pattern, not a general username rule:**
A general rule "username with LatAm city → penalize" would break legitimate diaspora accounts that use their city of origin in the handle but do reside in France: `medellin_en_paris`, `paisas_en_paris`, `bogotanos_en_paris`, etc. The AF pattern is semantically distinct: "alianza francesa" + Colombian city unambiguously identifies an institution based in that Colombian city, not in France.
**Alternative considered 1:** General rule for username with any LatAm city in `NON_FRANCE_CITY_MARKERS`.
**Why rejected:** High risk of false positives in diaspora accounts with city of origin in the username.
**Alternative considered 2:** Manual override in `config/account_tiers.json` (`"alianzafrancesademedellin": "excluded"`).
**Why rejected:** Does not generalize to future Alliance Française branches discovered later (BFS may find more); less defensible as systematic methodology in the mémoire than a declarative rule.

---

## DD-033 — Groq LLM as Layer 3 of event extraction

**Date:** 2026-07-24
**Decision:** Add a fourth layer to `4_enrich_events_extract.py`, running only on the candidates that already passed Layers 1+2 (~30-50 per run, never the full corpus). Uses Groq's free-hosted `llama-3.3-70b-versatile` (confirmed current on console.groq.com/docs/models at the time of writing — not assumed) via its OpenAI-compatible endpoint with `response_format: json_object`, called from `llm_enrich_event()`. It returns `is_public_invitation`, `is_upcoming`, `clean_location`, `clean_date`, `clean_description`, and `reasoning`. `clean_location`/`clean_date` override the spaCy NER / dateparser output when non-null (fallback to the existing values otherwise); `eventScore` gets an additional `llm_penalty` multiplier: `0.15` when `NOT (is_public_invitation AND is_upcoming)`, `1.0` otherwise — same soft-penalty pattern as the political-account penalty (DD-012): the event stays in the graph for traceability but becomes irrelevant in rankings, rather than being dropped outright. A Groq call failure (network error or invalid JSON) returns null values and is retried once; on a second failure `llm_penalty` stays at `1.0` (no penalty) so one bad caption never crashes the run or unfairly penalizes an event the LLM simply failed to score. `sourcePostUrl`/`sourceAuthor`/`sourcePostDate` are written on the `:Event` node only at creation time (not when an existing event is enriched by a later post) since they identify the original source post — `postCount` and the `MENTIONS_EVENT` relationships already cover full traceability across all corroborating posts.
**Rationale:** Manual review of dry-run output showed two confirmed bug classes that Layers 1-2 cannot fix: (1) spaCy/dateparser extract dates with a "prefer future" bias baked in, producing wrong dates when the caption is actually about a past event; (2) institutional news items — a meeting with Hermès's CEO, an official visit by Macron, routine administrative consular notices — get classified as cultural "events" because they mention a date/place/org, even though there is no real open invitation to the public. No `:Event` node has been created in Neo4j yet (every run so far was `--dry-run`), so this design applies from the very first real event onward.
**Why Layer 3 only on filtered candidates, not the full corpus:** Layers 1+2 already narrow ~thousands of captions down to ~30-50 per run. Running an LLM call over the full corpus would be unnecessary cost/latency for zero marginal benefit — captions rejected by Layers 1-2 were never going to become events.
**Alternative considered 1:** Local LLM via Ollama.
**Why rejected:** The user's machine has a RAM constraint that makes running a 70B-class (or even a smaller quantized) local model impractical alongside the rest of the pipeline (spaCy, sentence-transformers, transformers models already loaded).
**Alternative considered 2:** Train a small classifier (e.g. SetFit) on `is_public_invitation`/`is_upcoming`.
**Why rejected:** Not enough labeled data exists yet to train a reliable classifier for this mémoire's timeline; a hosted general-purpose LLM gets usable output today without a labeling phase.

---

## DD-033 (update) — Groq failure logging, rate-limit backoff, and revised fallback penalty

**Date:** 2026-07-24
**Problem found:** A real `--dry-run` (50 posts, 37 events) showed 7/37 `llm_enrich_event()` calls returning `None` with no logged cause. Because the original fallback was "failure → `llm_penalty = 1.0`" (no penalty), an institutional news item — Ecuador's ambassador assuming new duties, pure news with no public invitation — ranked **#1** of the whole run (`eventScore = 0.768`) specifically because its Groq call failed and therefore went unpenalized. The failure mode itself was invisible: no exception type, no status code, nothing to distinguish rate-limiting from a timeout or a JSON-parse error.
**Decision:**
1. `_groq_request()` now logs `type(e).__name__: e` on every failed attempt, and detects HTTP 429 explicitly (before `raise_for_status()`) to log it as a rate-limit event distinctly from other exceptions.
2. Added `_groq_rate_limit_wait()`: a minimum-interval throttle (`GROQ_MAX_RPM = 25`, under the free tier's 30 req/min) applied before every attempt, plus 429-specific backoff that honors the `Retry-After` header when present (falls back to `2**attempt` seconds). Retries per call raised to `GROQ_MAX_ATTEMPTS = 3`. This replaces the previous single immediate retry, which was neither spaced out nor 429-aware — 37 back-to-back calls against a 30/min limit was guaranteed to trip it.
3. Fallback penalty on unresolved verdict (`is_public_invitation`/`is_upcoming` still `None` after all retries) changed from `1.0` (blind trust) to `LLM_UNKNOWN_PENALTY = 0.5` — distinct from `LLM_REJECT_PENALTY = 0.15` used when Groq *does* respond and rejects the event. Three options were weighed explicitly before picking this one:
   - **Keep 1.0 (no penalty):** simplest, but is exactly the bug above — an unresolved verdict is silently treated as a pass.
   - **Intermediate penalty (chosen, 0.5):** bounds the damage in both directions — a real event isn't crushed by a transient Groq outage, but an unresolved institutional-news case can no longer reach the top of the ranking. Requires an arbitrary calibration constant.
   - **Fail-closed (0.15, same as a confirmed reject):** safest against false positives at the top of the ranking, but punishes infrastructure flakiness (rate-limit, timeout) as if it were a content problem, demoting genuinely good events whenever Groq is merely unavailable.
   User chose the intermediate option.
**Verification:** Re-run `--dry-run --max-posts 50` after the fix; confirm failures are now logged with an identifiable cause and the failure rate drops substantially versus the original 7/37.

---

## DD-033 (update 2) — Token-per-minute throttle for Groq

**Date:** 2026-07-24
**Problem found:** A live `--dry-run --max-posts 150` still hit 429s with escalating `Retry-After` (2s → 13s → 560s) despite the RPM throttle from the first update. Groq's free tier limits **requests** per minute (30) *and* **tokens** per minute (assumed 6,000 at the time — later found to actually be 12,000, see update 3) independently; several corpus captions run 300-500+ words, sometimes duplicated in Spanish and French in the same post, so the token bucket emptied before the request-count bucket did.
**Decision:**
1. `_estimate_tokens()` — a `len(text)//4` heuristic — estimates the prompt's token cost before sending, plus a fixed `GROQ_RESPONSE_TOKENS_EST = 150` for the (short, fixed-shape) JSON output.
2. `_groq_token_window` — a sliding list of `(timestamp, estimated_tokens)` pruned to the trailing 60s — tracks usage; `_groq_tpm_wait()` blocks *before* sending if `used + estimated > GROQ_TPM_SAFETY_MARGIN`, sleeping until the oldest window entry ages out, instead of firing and eating the 429. The existing 429 handling is kept as a safety net only.
3. Caption truncation for the LLM prompt tightened from 1500 to 900 characters (`LLM_CAPTION_CHARS`) — checked with a synthetic long caption that this alone drops the per-call estimate from ~1000 to ~225 tokens, a meaningful reduction on the dominant cost driver. 900 characters is enough for the LLM to judge `is_public_invitation`/`is_upcoming`/`clean_location`, which are almost always decidable from a caption's first paragraph, not from trailing tagged-account lists or repeated-language blocks.
**Verified with a fake clock** (no real waiting) that the token-window block/release logic is correct in isolation before running it live.

---

## DD-033 (update 3) — Migrate Layer 3 to local Ollama; Groq kept as opt-in fallback

**Date:** 2026-07-24
**Problem found:** Querying the pending backlog returned **724** posts still needing extraction, of which ~74% (≈536) require an LLM call. While diagnosing the update-2 fix live against Groq, its response headers revealed the *real* free-tier limits: **12,000 TPM** (not 6,000 as assumed) but also, per Groq's own rate-limit docs, a **100,000 tokens/day (TPD)** cap that isn't exposed in any per-request header — so neither this project's throttle nor a header-driven one can see it coming, only hit it. At ~650-700 tokens/call, that TPD cap allows only **~148 Groq calls/day** on the free tier — far short of the ~536 needed. The escalating multi-hundred-second `Retry-After` values seen in the 150-post test (and originally reported) are consistent with this daily bucket, not the per-minute one: math checks out closely (100,000/day ÷ 86,400s ≈ 1.16 tok/s trickle-refill; ~650 tokens/1.16 ≈ 560s, matching the largest observed `Retry-After`).
**Decision:** Add `llm_enrich_event_ollama()` — later folded into a provider-dispatching `llm_enrich_event()` — that sends the *exact same* prompt/JSON schema (`_build_llm_prompt()`, shared between both transports) to a local Ollama instance (`qwen2.5:7b`, `http://localhost:11434/api/chat`, `format: "json"`) instead of Groq. A new `LLM_PROVIDER` env var (default `"ollama"`) selects the transport; Groq's code (`_groq_request`, its RPM/TPM throttling from update 2) is left intact and reachable via `LLM_PROVIDER=groq`, preserving the option to switch back or run a hybrid later without rewriting anything. Ollama has no quota to pace against — calls are sequential with no retry/backoff loop (that machinery was specific to Groq's quota, not applicable locally); a `ConnectionError` is caught and reported with an actionable message ("¿está corriendo `ollama serve` y el modelo qwen2.5:7b descargado?") instead of a raw traceback. The existing fallback policy is unchanged: any Ollama failure returns null verdicts, and the caller applies `LLM_UNKNOWN_PENALTY = 0.5` exactly as it already did for Groq failures.
**Trade-off accepted:** local CPU inference on a 7B model is slower per call than a hosted 70B model, and quality may differ somewhat from `llama-3.3-70b-versatile`. Accepted because the daily quota ceiling makes Groq's free tier structurally unable to process the current backlog in reasonable time regardless of how well request pacing is tuned, whereas Ollama's only constraint is wall-clock compute time on the user's machine.
**Not yet verified against a real Ollama call** — a real test run (`qwen2.5:7b` actually generating a response) is pending until the model finishes downloading. However, checking the local environment during implementation found Ollama's daemon already running (`v0.32.3`) with no models pulled yet (`ollama pull qwen2.5:7b` needed) — `_ollama_request()` was extended to detect this specific case (HTTP 404 + `"not found"` in the body) and print `Ollama: modelo qwen2.5:7b no está descargado — correr \`ollama pull qwen2.5:7b\`` distinctly from the "daemon unreachable" `ConnectionError` case, instead of a generic HTTP-error message.

## DD-033 (update 4) — Local LLM (Ollama) and NLI invitation filter both rejected; full commitment to Groq

**Date:** 2026-07-25
**Problem investigated:** Two non-Groq alternatives were tested to avoid the
Groq free-tier daily cap (148 calls/day, see update 3), motivated by the
user's stated preference for full independence from third-party services.
**Alternative 1 — Local Ollama, qwen2.5:14b:** Tested as a higher-capacity
replacement for qwen2.5:7b (which had shown reasoning errors on
`is_upcoming` for institutional-news captions in manual review). Direct
`ollama run` succeeded, but invocation through the pipeline's HTTP API
crashed with HTTP 500 when run alongside the rest of the pipeline's loaded
models (sentence-transformers, NLI classifier, spaCy). Root cause confirmed
via direct system measurement: the machine has 15.69GB total RAM, with as
little as 0.56GB free during a real run. An isolated API call (no other
models loaded) succeeded but took 36.7s for a trivial prompt, with
`prompt_eval_duration` alone at 21.8s — consistent with memory-pressure
swapping, not just slow CPU inference. At ~536 pending LLM calls, this is
both unreliable (crashes under real pipeline conditions) and impractically
slow (36s+/call). **Rejected for hardware reasons, not quality** — whether
14B would have actually fixed the `is_upcoming` reasoning gap was never
established.
**Alternative 2 — NLI-based invitation filter (Capa 2c):** A new binary NLI
layer (same lightweight model as Capa 2a, `MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli`,
new hypothesis: "Esta publicación invita al público a asistir a un evento
futuro") was added as a diagnostic-only signal (not gating scoring) and
compared against the Groq LLM's `is_public_invitation` verdict on a 17-post
dry-run. Agreement was 5/17 (29%) — worse than the trivial baseline of
always predicting "not an invitation" (13/17, 76%). The model systematically
scored institutional news items (ambassador meetings, official visits,
commemorations) at 0.73-0.98, i.e. confidently *wrong* in the exact case
this filter was meant to catch. Diagnosed as the lightweight NLI model
conflating topical similarity ("this is about an event/meeting") with the
actual speech act ("this invites the public") — the same class of failure
anticipated for embeddings/cosine-similarity in an earlier discussion, just
manifesting through a different technique. **Rejected outright, not tuned**
— removed from the codebase rather than kept as a partial/adjusted signal.
**Decision:** Revert `LLM_PROVIDER` default to `"groq"`. Accept the ~148
calls/day free-tier cap as an operating constraint on the full corpus
(~536 pending calls, ≈4 days at cap), relying on the pipeline's existing
idempotent design (`eventExtracted` sentinel) to resume across days without
manual intervention. Local/embedding/NLI alternatives remain open for future
work (e.g. a heavier NLI model for the invitation axis, or a grammatical
tense-detection rule layer for `is_upcoming`) but are out of scope for the
current corpus run.
**Known limitation for the mémoire:** the project cannot currently run
Layer 3 (LLM semantic verification of institutional vs. genuine public
invitations) independently of a third-party hosted LLM (Groq) — full
reproducibility by a third party depends on that party also having a Groq
API key subject to the same free-tier daily limit, or supplying their own
paid LLM access.

---

*Last updated: 2026-07-25*
*Next decisions to document: DD-023 (NLP account classifier), SetFit for v2, TikTok integration, human-in-the-loop for event review.*
