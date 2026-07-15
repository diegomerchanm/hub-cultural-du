# Hub Cultural — Execution Log

> Chronological record of each significant pipeline run.
> Complements docs/decisions.md (the why) with what happened and what results were obtained.
> Foundation for the results chapter of the mémoire.

---

## RUN-001 — June 2026 — Initial seed
**Scripts:** 1_harvest_ig_profiles.py, 2_build_graph.py
**Input:** @consuladocolparis (single seed)
**Output:**
- 1 profile harvested with complete data
- ~80 empty Account nodes created via relatedProfiles and mentions
- Cost: ~$0.0005 USD
**Result:** Initial graph with single seed operational
**Lessons:** The seed automatically generates empty nodes
for related accounts at no additional harvesting cost

---

## RUN-002 — June 2026 — BFS expansion layer 1
**Scripts:** 1_harvest_ig_profiles.py, 2_build_graph.py
**Input:** ~80 empty accounts automatically detected by Neo4j
**Output:**
- 169 additional profiles harvested
- 170 total profiles in data_raw/
- 4,637 Account nodes in Neo4j
- 107 :Public accounts, 63 :Private accounts
- Cost: $0.0026 USD cumulative total
**Result:** Initial corpus of 170 profiles operational
**Lessons:**
- Extremely low cost ($0.0005/profile)
- The remaining 4,467 empty accounts are an organic prospect list
- Account origins: 1,530 comments, 1,001 relatedProfiles,
  906 mentions, 740 tags, 290 unknown

---

## RUN-003 — July 2026 — Prioritized cultural posts
**Scripts:** 1_harvest_ig_posts.py, 2_build_graph.py
**Input:** 7 manually selected cultural accounts:
  dichaparis, el_man_de_los_chorizos, elcafetal.paris,
  ivan_argote, calisabor_salsa_calena,
  alianzafrancesademedellin, educulturaco
**Output:**
- 333 posts harvested (50 per account, 33 for calisabor)
- 1,190 total posts in Neo4j
- Cost: $0.82 USD
**Result:** Deep posts available for NLP pipeline
**Lessons:** Post harvesting cost (~$0.12/account) is 240x
higher than profiles (~$0.0005) — requires prior NLP filter

---

## RUN-004 — July 2026 — NLP node enrichment
**Scripts:** 4_enrich_nodes_nlp.py
**Input:** 123 Account bios + 1,317 Post captions
**Output:**
- 123 bios enriched with language, NER entities, keywords
- 1,317 captions enriched
- Bio language distribution: es=82, unknown=18, en=14, fr=9
**Result:** Nodes enriched with NLP semantics
**Lessons:**
- spaCy does not extract DATE from bios (expected — bios are static)
- Post-NER filters needed: URLs, line breaks, >60 chars
- spaCy MISC very noisy in Instagram text — removed

---

## RUN-005 — July 2026 — Event detection v1 (failed)
**Scripts:** 4_enrich_events_extract.py (original version)
**Input:** 1,085 posts
**Output:**
- Detection rate: ~0.7% (7 events from 1,085 posts)
- Time: ~5 hours on i5 CPU
**Result:** FAILED — pipeline unusable
**Lessons:**
- Critical bug 1: monolingual English NLI model
  (cross-encoder/nli-MiniLM2-L6-H768) on ES/FR texts
- Critical bug 2: spaCy ES/FR has no DATE label
- Critical bug 3: make_event_id() with non-normalized date
- All identified by Fable review

---

## RUN-006 — July 2026 — Event detection v2 (corrected)
**Scripts:** 4_enrich_events_extract.py (post-Fable fixes)
**Input:** 1,289 posts (after sentinel reset)
**Output:**
- Detection rate: 74% (756 events from 1,289 posts)
- Time: 18 minutes (vs. 5 hours before)
- 494 events created, 262 enriched
- Distribution: 447 gastronomic, 108 institutional,
  57 visual, 41 training, 32 community, 24 festival,
  22 musical, 3 performing arts, 3 audiovisual, 1 political
**Result:** NLP pipeline operational
**Lessons:**
- Fix 1: mDeBERTa-v3-base-xnli-multilingual-nli-2mil7
- Fix 2: dateparser for date extraction
- Fix 3: maximum similarity vs. average in Layer 1
- Fix 4: lightweight MiniLMv2 as default in Layer 2b
- 10x speedup via batch inference
- 259 suspicious gastronomic → manual review →
  many are menu posts, not real events

---

## RUN-007 — July 2026 — Duplicate resolution
**Scripts:** 4_enrich_events_resolve.py
**Input:** 504 events (after manual cleanup)
**Output:**
- 14 duplicate pairs merged
- 220 relationships redirected
- 490 final events
- Average hotness: 2.900
- Max postCount: 11
**Result:** Deduplicated events in Neo4j
**Lessons:**
- Location normalization critical: París/Paris/paris
  must be the same group (fix with unidecode)
- Threshold 0.75 correct for semantic similarity
- Triple criterion necessary: location + date ±3d + similarity

---

## RUN-008 — July 2026 — Location geocoding
**Scripts:** 4_enrich_locations.py
**Input:** 550 Location nodes in Neo4j
**Output:**
- 550/550 locations geocoded (100%)
- Geographic distribution:
  France: 245, Colombia: 93, Spain: 44,
  Brazil: 19, other: 149
- Nominatim rate limit: 1 req/s → ~10 minutes total
**Result:** Transnational network across 8+ countries confirmed
**Lessons:**
- The network is not only Paris — it is transnational
- Consistent with transnational simultaneity theory
  (Vertovec, 2009)
- Some false positives: "Festival De" → Germany,
  "Este Domingo" → Dominican Republic

---

## RUN-009 — July 2026 — Network analysis v1
**Scripts:** 3_analyze_network.py export/analyze/writeback
**Input:** 4,637 nodes, 2,939 social edges,
          1,300 algorithmic edges
**Output:**
- 61 Leiden communities (modularity 0.88)
- Global social E-I Index: -0.2149
  · individual: -0.2020 (localist)
  · institucional_cultural: +0.8027 (bridge)
  · comercial: +0.9059 (strong bridge)
  · medio: +1.0000 (pure connector)
- Betweenness @consuladocolparis: 13,108
- Graph density: 2,047 nodes / 2,300 edges (ratio 1.1)
**Result:** Complete v1 network analysis with significant
sociological findings
**Lessons:**
- Density too low for discriminative rankings
- Bug identified: previous GDS projection only saw
  RELATED_TO, not MENTIONS/TAGS_USER — corrected by Fable
- Latin businesses are the real bridges of the diaspora
  (commercial E-I = +0.9059) — citable finding

---

## RUN-010 — July 2026 — Network analysis with tiers
**Scripts:** 3_analyze_network.py export/analyze/writeback
**Input:** 4,637 nodes with assigned tier
  (27 primary, 9 secondary, 17 excluded, 4,584 unknown)
**Output:**
- Same algorithms on full graph
- Tier filter only in final report
- Top primary by PageRank available for V2 seeds
**Result:** Clean rankings without political/commercial noise
**Lessons:**
- 99% of the graph is unknown — low density confirmed
- Needs more BFS cycles to be discriminative
- Tier system correct but insufficient corpus

---

## RUN-011 — July 2026 — First V2 seed harvest
**Scripts:** 1_harvest_ig_profiles.py --seeds config/seeds_v2.json,
2_build_graph.py
**Input:** 25 V2 seeds (18 Bloc A consulates/embassies + 7 Bloc B
cultural institutions, DD-022). 2 already had a local profile
(consuladocolparis, embajadacolfra).
**Output:**
- 23 new profiles harvested, 0 failures
- 193 total profiles in data_raw/ (170 from V1 + 23 new)
- 5,433 :Account nodes in Neo4j (+796 vs. 4,637 from RUN-002/010)
- Of those +796: 23 are complete profiles (the seeds), ~773 are
  new nodes discovered via relatedProfiles/taggedUsers/coauthorProducers/
  mentions/comments from the 23 institutional accounts
- Actual cost: $0.06 USD (prior calibration had estimated $1.34–$3.36
  — corrected overestimate)
- Transient Neo4j connection error during 2_build_graph.py, automatically
  resolved by retry — did not affect the final result
**Result:** V2 expansion operational, ready for NLP classifier
**Lessons:**
- The BFS pattern from RUN-001/002 scales well: 23 institutional seeds
  generated ~33 new nodes each on average (fewer than the ~80 generated
  by @consuladocolparis alone in RUN-001 — institutions appear to have
  more limited relatedProfiles/engagement than an active community account)
- Data heterogeneity finding: 2,665 of the new nodes have fullName
  (relatedProfiles/taggedUsers), 2,575 are username-only
  (mentions/comments) — see DD-027

---

## RUN-012 — July 2026 — Second harvest directed by NLP classifier
**Scripts:** 1_harvest_account_classifier.py --diagnose,
1_harvest_ig_profiles.py --from-classifier, 2_build_graph.py
**Input:** account_scores.csv after RUN-011 expansion (5,433 accounts
evaluated, threshold unchanged: THRESHOLD_ORG=0.60, THRESHOLD_PERSON=0.75)
**Output:**
- keep=True: 70 accounts (1.3%) · Roles: context=5345, target=63, seed_source=25
- data_completeness (DD-027): average=0.52, bimodal — 2565 in band 0–0.33,
  11 in band 0.34–0.66, 2857 in band 0.67–1.0
- Of the 63 target, 57 already had a complete profile (V1) — the incremental
  check skipped them automatically
- 6 genuinely new profiles harvested: festivaldautomne,
  domingo_pal_bailador_paris, parisglobefestival, ruedadecumbia.paris,
  cinema.lemelies.montreuil, parislete — all discovered via
  relatedProfiles/taggedUsers of V2 institutional seeds
  (data_completeness=0.8 before harvesting)
- 199 total profiles in data_raw/ (136 public, 63 private)
- Cost: $0.00 USD (below the rounding shown by the script)
- None of the 6 new accounts brought latestPosts — pending evaluation of
  whether 1_harvest_ig_posts.py is worthwhile for them
**Result:** Complete two-phase cycle (BFS + NLP filter + directed harvest)
operational and validated end-to-end on V2 seeds
**Lessons:**
- The two-pass strategy proved highly efficient: of 796 new nodes generated
  in RUN-011, only 6 required real harvesting investment — the rest were
  either already known (57) or did not pass the NLP filter (733)
- Original classifier threshold (0.60/0.75) maintained without adjustment —
  validated as functional by Diego after reviewing --diagnose
- data_completeness (DD-027) remains diagnostic only; its bimodal nature
  suggests a categorical treatment (full/partial/bare) would be as informative
  as a continuous one, given the scarcity of intermediate cases

---

## RUN-013 — July 2026 — Data loss due to stale code version (incident)
**Scripts:** 1_harvest_ig_posts.py
**Input:** 4 accounts with RUN-003 history (dichaparis, elcafetal.paris,
educulturaco, ivan_argote) — executed at 09:16–09:25 UTC on
2026-07-13, 14–23 minutes BEFORE commit e2ae5fc (DD-029, merge
+ dynamic window) was applied.
**Output:**
- The code that ran was from 8f58d50 (DD-028) — direct overwrite,
  without merge_and_cap.
- dichaparis: full overwrite, only 2 new posts remained (lost ~48
  historical ones from RUN-003).
- elcafetal.paris, educulturaco, ivan_argote: overwritten with the
  Apify "no_items" error placeholder (additional bug — the check
  "if not dataset_items" did not detect that case, see DD-030), losing
  100% of their RUN-003 history.
- Corrupted state ingested into Neo4j via 2_build_graph.py before
  detection.
- Recovery attempted via Apify API: impossible — RUN-003 datasets
  (2026-07-01) had already expired. The API only retains the last ~83
  runs (available range: 2026-07-12 to 2026-07-13). Permanent loss confirmed.
**Result:** INCIDENT — partial data loss, root cause identified and corrected.
**Lessons:**
- Actual root cause: timing gap between script execution and the fix commit
  that was believed already applied — not a bug in merge_and_cap or
  days_to_fetch (both work correctly, confirmed via Apify API comparing
  commit timestamps vs. run timestamps).
- Real and confirmed secondary bug: the error placeholder
  "{'error': 'no_items', ...}" from apify/instagram-post-scraper is a
  1-element list — truthy in Python — so "if not dataset_items" never
  detected it, in any version of the script until this fix (DD-030).
- data_raw/ is gitignored — no automatic backup of raw data. Losing a
  local file means losing the data, unless Apify still retains the original
  dataset (verified: not in this case).
- Future protocol: confirm a fix commit is applied BEFORE running the script
  that depends on it — do not assume "it's already committed" without
  verifying with git status/git log.

---

## RUN-014 — July 2026 — Validation of geo_hard_signals fix (DD-031/DD-032)
**Scripts:** 1_harvest_account_classifier.py --diagnose
**Input:** data_processed/account_scores.csv (5,433 accounts) after
commits 9180ad4 (lat/lon bbox, DD-031) and fe65863 (AF_SATELLITE, DD-032)
**Output:**
- 7/7 target accounts moved to keep=False:
  alianzafrancesademedellin (username:AF_satellite:medellin),
  alianzafrancesacali (addr:OUTSIDE_FR:3.44,-76.52 + AF_satellite:cali),
  alianza_francesa_de_pereira (addr:OUTSIDE_FR:4.81,-75.70 + AF_satellite:pereira),
  unadunioneuropea (addr:OUTSIDE_FR:40.43,-3.67),
  williamsanchezinmobiliaria (addr:OUTSIDE_FR:39.99,-0.05),
  embcolghana (addr:OUTSIDE_FR:5.61,-0.18),
  remaxmariavillasmil02 (addr:OUTSIDE_FR:7.77,-72.21)
- keep=True: 62 → 61 (exactly the expected account, no more)
- No collateral damage: calisabor_salsa_calena and francy_barahona_calisabor
  (both with "cali" in the username, both based in Paris) verified
  intact at keep=True with geo≥0.99
- No network or Neo4j needed — complete offline validation on CSV
**Result:** Fix validated end-to-end
**Lessons:**
- businessAddress with lat/lon is the most robust geographic signal
  available; generalizes to any country without list maintenance
- The post-bbox residual (alianzafrancesademedellin, no lat/lon, bio
  about "Francia" as a topic) required a rule scoped to the name pattern
  (AF_SATELLITE), not a general LatAm-city username rule
- _tokens_in() uses word boundary — does not work for tokens embedded in
  usernames without separators; use substring (_norm(tok) in _norm(username))
  in contexts already gated by another strong pattern

---

*Last updated: 2026-07-15*
*Next run: RUN-015 — 3_analyze_network.py on expanded V2 graph (blocked by connectivity)*
