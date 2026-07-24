# V2 Network Analysis — Draft for the Mémoire Results Chapter

> Draft analytical summary of RUN-015 (`3_analyze_network.py run-all`, 2026-07-24),
> the first full network analysis on the V2-expanded graph. Written as raw material
> for the results chapter — facts and figures are sourced directly from the run
> output; interpretive framing is marked explicitly so it can be revised or
> challenged before going into the final mémoire text.

---

## 1. Context

This is the first structural analysis of the graph after the V2 expansion
(seed-driven BFS discovery + NLP classifier filtering, DD-022 through DD-032).
The V1 analysis (RUN-010) covered ~170 profiles; this run covers the full
Neo4j graph as of 2026-07-24: **7,269 Account nodes**, exported and analyzed
locally with igraph/leidenalg per the offline-analysis design (DD-013).

The graph is treated as a **multiplex network** — two structurally distinct
layers analyzed separately rather than merged, per the project's data model:

- **Social layer**: organic Instagram interactions (mentions, tagged users,
  co-authorship), projected author→post→target since these relationships
  originate at the Post node in the raw graph.
- **Algorithmic layer**: Instagram's own `relatedProfiles` similarity
  suggestions (`RELATED_TO` edges) — a platform-generated signal, not a
  human interaction.

Keeping them separate matters methodologically: the social layer reflects
*who actually engages with whom*, while the algorithmic layer reflects
*what Instagram's recommender considers similar*. Conflating them would
mix a behavioral signal with a platform-inference signal.

---

## 2. Structural overview

| Metric | Social layer | Algorithmic layer |
|---|---|---|
| Nodes | 2,973 | 1,706 |
| Edges | 3,348 | 1,956 |
| Weakly connected components | 50 | 6 |
| Giant component size | 2,547 (85.7%) | 1,537 (90.1%) |
| Leiden modularity (γ=0.5 / 1.0 / 1.5) | 0.902 / 0.905 / 0.905 | 0.860 / 0.869 / 0.868 |
| Communities found (γ=0.5 / 1.0 / 1.5) | 73 / 79 / 83 | 29 / 33 / 38 |

**Fact:** Both layers are dominated by a single giant component (85–90% of
nodes), with modularity consistently above 0.85 across all three resolution
levels tested.

**Interpretive note (flag for discussion):** modularity this high can mean
genuinely well-separated cultural/thematic sub-communities — but it can also
be partly an artifact of low-degree node fragmentation (many peripheral
accounts with 1–2 edges get trivially assigned to small communities, which
mechanically inflates modularity). Before treating "73–83 communities" as a
substantive finding for the thesis, it's worth checking the community *size
distribution* — how many of those communities have, say, fewer than 5
members. That check hasn't been run yet.

---

## 3. Mixing patterns (E-I Index)

The E-I Index measures whether a group's connections are mostly internal
(negative, toward -1) or mostly external (positive, toward +1).

**By tier** (social layer, global = +0.4056):

| Tier | E-I Index | n |
|---|---|---|
| unknown | +0.432 | 2,915 |
| excluded | +0.852 | 19 |
| primary | +0.892 | 25 |
| secondary | +0.970 | 14 |

**By tier** (algorithmic layer, global = +0.2301):

| Tier | E-I Index | n |
|---|---|---|
| unknown | +0.234 | 1,664 |
| secondary | +0.916 | 9 |
| excluded | +0.971 | 21 |
| primary | +0.994 | 12 |

**By Leiden community** (γ=1.0): -0.9273 (social) / -0.8436 (algorithmic).

**Interpretive note:** the tier-level pattern is expected, almost
mechanical — `primary`/`secondary`/`excluded` are small, hand-classified
groups embedded in a much larger `unknown` mass, so by sheer numbers most
of their edges necessarily point outward. The more meaningful number here is
the **community-level E-I index being strongly negative** (-0.93 / -0.84):
this confirms the Leiden communities found above are genuinely cohesive —
most edges stay inside the community rather than crossing between them —
which supports (but doesn't fully resolve) the modularity question raised
in §2.

---

## 4. Central and bridging actors

Restricted to `tier=primary` accounts (the 26 hand-curated core cultural
institutions/accounts).

**Top bridging actors (participation coefficient — connect across
communities), social layer:**

`francediplo_es` (P=0.420) · `latitud4podcast` (0.329) · `educulturaco`
(0.284) · `clmbiasays` (0.261) · `ruedadecumbia.paris` (0.252) ·
`elcafetal.paris` (0.167) · `ivan_argote` (0.106) · `morcharpentier`
(0.105) · `dichaparis` (0.094) · `parislete` (0.064)

**Top by PageRank, social layer:**

`dichaparis` · `calisabor_salsa_calena` · `clmbiasays` ·
`francy_barahona_calisabor` · `morcharpentier` · `francediplo_es` ·
`domingo_pal_bailador_paris` · `ivan_argote` · `latitud4podcast` ·
`mariaca_castro`

**Notable finding:** `elcafetal.paris` and `educulturaco` rank among the top
bridging actors and top PageRank accounts in both layers, despite having
**0–1 posts loaded** from this pipeline (their Apify scrapes returned empty
— see RUN-013/RUN-015 notes on persistent `no_items` responses for these
two accounts). Their network centrality comes entirely from being
**mentioned/tagged by other accounts**, not from their own publishing
activity.

**Interpretive note — this is a useful methodological validation for the
thesis:** it empirically supports DD-028's design decision to prioritize
post *recency* over post *density* when scraping, and to determine cultural
relevance via the NLP classifier (bio/caption semantics) rather than via
graph density metrics. A content-poor account can still be structurally
central to the diaspora network — centrality and content volume are
measuring different things, and conflating them would have biased account
selection toward "prolific posters" rather than "actually important nodes
in the network."

---

## 5. Actor composition

| Actor type | Count | % of 7,269 |
|---|---|---|
| individual | 6,725 | 92.5% |
| institutional_cultural | 182 | 2.5% |
| institutional_estatal (government) | 163 | 2.2% |
| comercial | 151 | 2.1% |
| medio (media) | 48 | 0.7% |

**Interpretive note:** the network is overwhelmingly composed of individual
accounts, not institutions. This is consistent with a grassroots,
organically-formed diaspora network rather than one structured top-down by
institutional actors — cultural institutions and government/consular
accounts are present and structurally important (see §4) but numerically
marginal. This framing — "small institutional core, large organic
periphery" — is likely a useful thesis narrative, but should be
cross-checked against the tier distribution (only 62 of 7,269 nodes have an
explicit tier — the actor-type classification and the tier classification
are two separate, only partially overlapping labeling systems, and that
distinction should be made explicit if used in the same argument).

---

## 6. Known limitations of this run (for methodological transparency)

- Tier coverage is sparse: only 62/7,269 nodes (0.85%) have an explicit
  `tier` label (`primary`+`secondary`+`excluded`); the remaining 99.15% are
  `unknown`. Most of the tier-based analysis in §3–4 is therefore describing
  a small, hand-curated subset, not the graph as a whole.
- The `:Political` label and `politicalScore` property are not populated in
  this pipeline path — they belong to `run_gds_algorithms.py` (the
  Neo4j-GDS-based script abandoned per DD-013 due to port 7687 being
  blocked). Political filtering in this project is instead applied earlier,
  during NLP classification (DD-012), so this is a naming leftover, not a
  missing analysis.
- Community-size distribution (see §2 caveat) has not yet been examined —
  recommended before citing "73–83 communities" as a headline number.

---

*Draft generated 2026-07-24 from RUN-015 output. To be reviewed, corrected,
and integrated into the mémoire results chapter — not final text.*
