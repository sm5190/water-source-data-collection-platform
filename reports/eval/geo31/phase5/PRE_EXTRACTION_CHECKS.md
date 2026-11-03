# Milestone 3.1 — checks before geographic-aware extraction

Date: 2026-11-03. Offline, read only (plus a DNS lookup of 7 official hosts). Geography uses
the DRAFT identities; all labels are drafts. No research behaviour was changed by these checks.
The only code change is a guard test (8743808).

## 1. Relationship labels

`RELATIONSHIP_LABEL_REVIEW.md` lists all 47 rows, Claude-drafted and UNCONFIRMED, in priority
order:

| Priority | Rows |
|---|---|
| Drafted INCORRECT | 6 |
| Drafted UNCERTAIN | 3 |
| Historical / outdated | 1 (EMOS) |
| Candidate only | 18 |
| The rest | the remainder |

Confirming means editing `relationship_labels.csv`. Until then, no figure is validated.

## 2. Official city site as GOVERNMENT: field-specific counterfactual (7 Toronto claims)

All 7 cite toronto.ca/city-government/…/toronto-water, the city's official site (Wikidata
P856), which is classed UTILITY there.

| Run | Field | Value | Today | Would GOVERNMENT change it? | Correct? | Risk |
|---|---|---|---|---|---|---|
| 0618af70 | city_population_millions | 3.6 | review (designated source missing, measurement year, geographic scope, no official source) | **No**: the designated-dataset and scope checks still fail | **Wrong**: 3.6 M is Toronto Water's service area incl. York and Peel; the city is ~2.8 M | Authority must never bypass the designated-dataset rule; it doesn't |
| 0618af70 | utility_reported_city_population_millions | 3.6 | review (measurement year, scope warnings) | **No** | Plausible as a service figure, but includes York/Peel | A scope warning is the right outcome |
| d26a016b | utility_reported_city_population_millions | 3.6 | review (no official source, utility service not established, scope) | **No**: the utility-serves-city check comes from the registry | same | — |
| 0618af70 | utility_name | Toronto Water | **AUTO** | No (already AUTO) | Correct | — |
| d26a016b | utility_name | Toronto Water | review (attribution unknown, service not established) | **No**: attribution and the registry check remain | Correct value | — |
| 0618af70 | infrastructure_notes | Toronto Water manages … | review (review_always) | No | Descriptive | — |
| 0618af70 | water_quality_notes | Toronto Water conducts laboratory testing … | review (review_always) | No | Descriptive | — |

**Result: 0 of 7 decisions would change.** Official-site authority only removes
`NO_OFFICIAL_SOURCE`; each claim stays blocked by its own field's semantic checks.

**Risk if enabled later:** a city site can host pages about other bodies (a regional
authority's figures, news). Authority should apply only to statements whose passage is about the
city, with field rules unchanged. Not enabled; your call after this report.

## 3. Verified municipal departments

- In the graph, a verified department's own pages weigh like a government document only for
  that organisation's relationships.
- No field value uses them, and affiliation never creates a service relationship: a
  DEPARTMENT_OF row is separate from UTILITY_SERVES_CITY, which still needs its own placed
  statement.
- The counterfactual is 0 values. The only verified department (Vienna Water / MA 31) has no
  pages outside wien.gv.at.

## 4. City identities

`IDENTITY_REVIEW.md` now has an approval addendum with official websites and domain checks.
Approval is still pending.

## 5. Geographic inclusions and exclusions: evaluator correction vs production change

Same frozen pool: 74 documents, 43 passages.

| Code (as production ran it) | Evaluator | Relevant excluded | Old measure | Not-relevant included |
|---|---|---|---|---|
| Phase 0 (6deb746) | its own (host rule = its production) | 8 | 17/34 | 7 |
| Phase 2 (32e858d) | old (host rule, **not** its production) | 5 | 13/23 | 14 |
| Phase 2 (32e858d) | corrected (search_scope = its production) | **0** | 1/8 | 17 |
| now (8743808) | corrected | **0** | 1/8 | 17 |

- **Production improvement:** 8 → 0 false exclusions. It happened in **Phase 2**, when ranking
  and acquisition moved to the title-based search scope.
- **Evaluator correction:** the old evaluator under-reported Phase 2 by 5. Checkpoint B's "5" was
  that artefact.
- **Feature location (Phases 4/E):** 0 changes on this pool.
- The Checkpoint D/E report said "most of the gain comes from fixing the evaluation tool"; that
  was wrong, and the report is corrected.
- Relevant documents or passages lost at any step: 0.

## 6. The ten newly included off-topic documents

| Docs | What they are | Effect |
|---|---|---|
| 4 search-only (G006 "Vienna Woods" vent field, G022 USDA food report, G048 Kenya rural regulation, G053 Colombian groundwater page) | never fetched | No passage can reach extraction; only ranking (now ×1 instead of ×0.5) |
| 6 fetched (Borealis, WaterEquity, Amazon water portfolio, Eni, IWA congress, World Bank VDKC) | right city, off-topic | Scored alone, each would send 12–37 passages to the model across 6–16 fields |

In their stored runs, 3 of the 6 fetched documents produced **6 claims, none accepted**,
including a spurious water-source candidate, "Sai Reddy Lake" (Amazon's Hyderabad irrigation
portfolio).

**Cause:** the relevance gate is purely geographic (city nearby, document/section names the
city…). Topic only influences ranking, so an off-topic document with no competition fills a
bundle.

**Fix** (proposed for the extraction phase, not done): a separate topical requirement per field
before a model call, not stricter geography. Foreign publishers and distant sources stay as they
are.

## 7. Registry, graph and Nairobi

- **Nairobi names.** The stored evidence has "Nairobi City Water & Sewerage Company (NCWSC) Ltd"
  (county assembly) and "Nairobi Water Company" (nairobiwater.co.ke). It has no sentence stating
  they are the same organisation; the assembly document's "Nairobi Water" is a heading. They stay
  separate (EQUIVALENCE_UNRESOLVED), and no alias is assumed.
- **Isolation.** No module that validates, resolves, accepts, retrieves or exports values imports
  the graph or affiliation code. Only finalize (to store it), discovery (the bounded affiliation
  check), the evidence store and the API do. The new guard `tests/test_graph_isolation.py` keeps it
  that way, so a CANDIDATE (or wrong) relationship can't grant authority or change acceptance.
- **Status differences** stay evidence-based and labelled (`difference` per relationship).
