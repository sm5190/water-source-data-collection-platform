# Milestone 3.1 — Relationship graph checkpoint

Date: 2026-11-03. Offline on stored runs only (no searches, fetches or model calls). Geography
uses the DRAFT identity (`CITY_IDENTITY_ALLOW_DRAFT=1`, recorded as `DRAFT_EVALUATION` on
every graph). Every relationship label is a draft by me (UNCONFIRMED), so all precision
figures are provisional, not validated.

Numbering: you called this Phase 3. In the approved plan (rev 2) the relationship graph was
Phase 5. Municipal affiliation (DEPARTMENT_OF) and feature location are still to come;
DEPARTMENT_OF is in the schema but never produced.

## Commits

| Commit | Content |
|---|---|
| 798a43b | Schema (`schemas/relationships.py`) + deterministic builder (`services/supply_relationships.py`, relationship-1) + 18 tests |
| ccc46c5 | Migration 0020 `run_supply_relationships`; graph built and stored at finalize (soft); `GET /research/runs/{id}/relationships`; records trace lists a source/utility value's relationships |
| 1076510 | Offline builder `scripts/supply_relationships.py`; label tooling `scripts/relationship_labels.py`; 37 drafted labels; precision fixes from the stored runs (below) + tests |

Full suite: 1158 passed, 37 skipped (live/Postgres-only). Migration 0020 is applied locally;
`alembic check` reports only the LangGraph checkpoint tables, which are created outside
Alembic as before.

## 1. Relationships by kind and status (14 stored runs: 7 M3 + 7 earlier, same cities)

| Kind | VERIFIED | SUPPORTED | CANDIDATE | HISTORICAL | CONTRADICTED |
|---|---|---|---|---|---|
| UTILITY_SERVES_CITY | 5 | 6 | 18 | 0 | 0 |
| SUPPLIES_WHOLESALE_TO | 0 | 2 | 0 | 0 | 0 |
| SOURCE_SUPPLIES_CITY | 3 | 0 | 0 | 0 | 0 |
| SOURCE_SUPPLIES_UTILITY | 1 | 0 | 0 | 0 | 0 |
| PREDECESSOR_OF | 0 | 0 | 2 | 0 | 0 |
| SOURCE_PART_OF_SYSTEM, ALIAS_OF, DEPARTMENT_OF | 0 | 0 | 0 | 0 | 0 |

Plus 15 rejected statements (kept for audit). No HISTORICAL or CONTRADICTED relationship
occurs in the stored evidence; both are exercised by the tests only.

## 2. Verified and supported relationships (evidence in `graphs.json`)

- **Source → city / utility (all VERIFIED, from government documents):**
  - Lake Houston → Houston (usgs.gov: "the primary drinking-water source for Houston, Texas");
  - Río Maipo → Aguas Andinas (mma.gob.cl: "el agua que toma del río Maipo");
  - Lake Ontario → Toronto (toronto.ca);
  - Limestone Alps of Lower Austria and Styria → Vienna (wien.gv.at). This entity is a region,
    kept with its kind, not standardized as a source.
- **Utility → city VERIFIED:** Municipal Department 31 – Vienna Water and Vienna Water
  (wien.gv.at; two runs); Nairobi City Water & Sewerage Company and its short name (county
  assembly).
- **Wholesale:** Coastal Water Authority → Houston, SUPPORTED as a self-description ("CWA
  provides untreated surface water to the cities of Houston, Baytown, and Deer Park"). It is
  never a retail operator. The legitimate raw-water relationship is present; it is SUPPORTED,
  not VERIFIED, because no other host states it.
- **Other SUPPORTED:** Aguas Andinas (riotimesonline), EMOS (old World Bank document), Nairobi
  Water Company (own site), Luanda Provincial Water Company / Provincial Water Company (EPAL,
  kept as two entities, not merged), Toronto Water (own site).

## 3. False or unsupported relationships

On the draft labels, 7 relationships are INCORRECT. All but one sit at CANDIDATE:
- **"East Water" → Houston wholesale (SUPPORTED).** A fragment of the "East Water
  Purification Plant", which the registry mines as a name. This is the only incorrect
  SUPPORTED/VERIFIED relationship. The fix belongs in the registry (an infrastructure noun
  after the name); it was not changed, because the registry feeds acceptance.
- CANDIDATE, all from registry names:
  - NFBWA Internal Distribution Water (Houston);
  - Community Drinking Water (Hyderabad; rural India plants);
  - Development Workshop (an NGO in Luanda);
  - Scottish Water (Toronto congress list);
  - Water Treatment Plant (a glossary entry; ×2).

The first pass also produced these, each fixed generically with a regression test:

| False positive (first pass) | Cause | Fix |
|---|---|---|
| Scottish Water and "Water Treatment Plant" → Toronto, SUPPORTED | The registry places a sentence by the page title | The graph needs the sentence or its section to name the city; a title counts only on a government document |
| "Second Vienna Spring Water Main" part of the First | A "comprises" list ran past "while" | The list ends at the clause; only sources are parts |
| EPAL "bulk supply" → Luanda, SUPPORTED | "no plans to … bulk supply" wasn't a negation | Added "no plans to" as a negation; the statement is now rejected |

Rejected statements (15): contractor or non-serving roles, e.g. Almar Water Solutions
(designs/operates infrastructure), SWOT (a satellite mission), Saudi Water Authority (a
reference list in an engineering company's brochure), and "EPAL constructed …". One was rejected
as only negated (EPAL "no plans"). Spot-check: the rejection is right in every case except two
borderline ones (EMOS described as an "efficient utility" in a financing sentence; "Vienna
water service area houses constructed prior to 1986"). In both, a correct entity's statement
is rejected because the registry reads "constructed / designed" as a contractor role. Nothing
is lost by it: both entities keep other relationships.

## 4. Precision against the labels (provisional; 37 drafted rows, 0 confirmed)

| Kind | VERIFIED | SUPPORTED | CANDIDATE |
|---|---|---|---|
| Utility → city | 5/5 | 5/6 (1 outdated: EMOS) | 11/17 (1 uncertain) |
| Source → city | 3/3 | — | — |
| Source → utility | 1/1 | — | — |
| Wholesale | — | 1/2 ("East Water") | — |
| All | **9/9** | 6/8 | 12/18 |

The CANDIDATE level is where the registry's weak names sit, which is its intended meaning: not
reviewed or used. The sample is small: source relationships are 4 rows in total.

## 5. Needing review

- 20 CANDIDATE relationships (mostly claim-only or title-placed utilities).
- 2 PREDECESSOR_OF: Empresa de Agua Potable de Santiago → EMOS is correct; EMOS → "EMOS" (a
  change of legal form) is uncertain.
- 0 CONTRADICTED.
- Possible equivalences, preserved and not merged:
  - Vienna Water / Wiener Wasser / MA 31 – Vienna Water;
  - Nairobi City Water / NCWSC / Nairobi Water Company;
  - EPAL / Provincial Water Company / Luanda Provincial Water Company.

## 6. Acceptance changes (no-LLM revalidation)

- The 8 M1 and 7 M3 runs were revalidated with this code and compared with the Phase 2
  revalidation runs: **0 automatic decisions changed in 15 runs**
  (`decisions_phase2_vs_phase3.json`).
- So there are no new auto-acceptances and no correct value moved to review. Checkpoint A's 15
  AUTO values (all correct) are unchanged.
- No acceptance rule reads the graph.
- Each revalidation run stored its graph at finalize: 38 relationships in total, all
  relationship-1.

## 7. Evidence lost

None. The change is additive: no claim, resolution, review decision, registry row or document
is altered, and the parents' rows are untouched.

## 8. Performance and storage

| Measure | Value |
|---|---|
| Build time, all 14 runs | 74.5 s (0.7 to 16 s per run; the longest run has 1,280 chunks) |
| Stored payload, all runs | 61 KB (1 to 8 KB per run) |
| Rows per run | 1 to 10 (relationships + rejected) |

Most of the time goes to rebuilding the registry and the document scopes: the graph is
recomputed from evidence on purpose, for reproducibility. The build runs once per run, at
finalize, off the critical path.

## 9. Registry vs graph disagreements (your rule: a self-description alone is SUPPORTED)

4 utilities are VERIFIED in the registry but not in the graph, and all are real utilities:
- **Self-descriptions (graph SUPPORTED):** Coastal Water Authority, Nairobi Water Company, and
  Toronto Water. Toronto Water's host, toronto.ca, is the city's own government domain; it
  counts as a self-description because municipal affiliation (DEPARTMENT_OF) isn't built yet.
- **Placed only by the page title (graph CANDIDATE):** Houston Water.

If the registry adopted the graph's rule, these 4 would lose official-source authority, and
acceptance would need to be measured first.

## Known limits

- Predicates are a fixed list in five languages. Phrasings outside it, table rows, and
  anaphora across sentences are missed by design.
- With no feature-location dimension yet, a sentence that names only the source's distant
  region and no target place may still read as AMBIGUOUS. Location phrases are removed before
  the city check, which mitigates this.
- Source kinds come from claims or from wording ("Lake Houston" reads as a lake although it is
  a reservoir). The standardization rule is unchanged; the graph does not decide source fields.

## Needed from you

1. Review the 37 relationship labels (`tests/evaluation/geo31/relationship_labels.csv`).
2. Decide whether the registry should drop infrastructure fragments like "East Water"
   (acceptance would need measuring).
3. Order of the next work: municipal affiliation (would let toronto.ca-type self-descriptions
   count as government) and feature location, or the extraction/validation phase.
