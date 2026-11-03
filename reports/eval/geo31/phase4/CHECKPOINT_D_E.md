# Milestone 3.1 — Checkpoints D and E: affiliation, feature location, graph consistency

Date: 2026-11-03. Offline on 14 stored runs, plus a no-model revalidation of 15 runs (8 M1 and 7 M3).
- No searches, fetches or model calls; links were re-read from the stored raw HTML in MinIO.
- Geography uses the DRAFT identities (`DRAFT_EVALUATION`).
- All labels and utility expectations are UNCONFIRMED drafts, so every quality figure is
  provisional.
- Nothing here shows a live-research improvement.

## Commits

| Commit | Content |
|---|---|
| 4789ae3 | Official city websites in the identity (Wikidata P856; 51 of 56 entries, still DRAFT); `GeoMatcher.official_host` |
| 4014dd4 | Document links (migration 0021): anchor, context, heading, footer flag; copied with run evidence |
| 44505bb | Municipal affiliation (MUNICIPAL_CANDIDATE / MUNICIPAL_VERIFIED → DEPARTMENT_OF); bounded `site:` check; `--reparse` |
| 1318253 | Registry: plant-name fragments are not organisations (utility-4). **Kept**: 0 decisions changed |
| 3dcf7ea | Feature location (normalize-8, retrieval 12, utility-5) |
| 25185ea, 0533a72 | Graph v2 (relationship-2): typing, reporting period, reconciliation, SOURCE_LOCATED_IN |
| 887fef1 | Evaluation fix: geo labels judged search results by the publisher host, a rule production dropped in Phase 2 |

Full suite: 1185 passed, 37 skipped.

## 1. Acceptance (no-LLM revalidation, 15 runs)

| Comparison | Automatic decisions changed |
|---|---|
| Fragment rule alone vs Phase 3 | **0** |
| Everything in this phase vs Phase 3 | **0** |

- No new AUTO, no correct value moved to review, no correct value lost. Checkpoint A's 15 AUTO
  values (all correct) are unchanged.
- Every revalidation run stored its relationship-2 graph at finalize.

**Counterfactuals, not applied (your decision):**

| If this counted for acceptance | Values affected |
|---|---|
| Verified municipal departments' pages as GOVERNMENT | **0**. The only verified departments (Vienna Water / MA 31) have no pages of their own outside wien.gv.at, which is already government. |
| Statements on the city's official website as GOVERNMENT (the graph does this) | **7 Toronto claims** citing a toronto.ca page classed UTILITY: utility_name ×2, utility-reported population ×3, city population, and the infrastructure and water-quality notes. |

## 2. Municipal affiliation (DEPARTMENT_OF)

| Organisation | Status | Evidence |
|---|---|---|
| Municipal Department 31 – Vienna Water / Vienna Water | VERIFIED | smartcity.wien.gv.at (official site, P856): "operated by Municipal Department 31 – Vienna Water", "Vienna Water (MA 31)" |
| Houston Public Works | CANDIDATE | Hostname hint (houstonpublicworks.org), plus houstontx.gov links whose anchors are "Drinking Water Operations", "Wastewater Operations". The anchors don't name the department, so they don't verify. |
| Toronto Water | Not needed | Its statements are on toronto.ca itself, the city's official site, so they count as government directly. |

- No statement or departments list on an official host names Houston Public Works.
- The bounded `site:houstontx.gov "Houston Public Works"` check would be planned in a live
  round. It isn't run offline.
- Plain links from other pages are no longer a signal. On the first pass they made CWA, USGS
  and the TWDB "candidates".

## 3. Registry / graph reconciliation (the four utilities)

| Utility (run) | Registry | Graph | Difference | Justified? |
|---|---|---|---|---|
| Coastal Water Authority (b13e0d1d) | VERIFIED (wholesale) | SUPPORTED | SELF_DESCRIPTION_ONLY | Yes. Only its own site states it; other documents call it Lake Houston's operator or a partner. |
| Nairobi Water Company (f622ab6a) | VERIFIED | SUPPORTED | SELF_DESCRIPTION_ONLY | Yes. The government statement names "Nairobi City Water & Sewerage Company", a separate, unmerged entity (same organisation: an alias not established by the evidence). |
| Toronto Water (0618af70) | VERIFIED | **VERIFIED** | none | Fixed. toronto.ca is the city's official site; the registry had bound it as Toronto Water's own host. That was a **logic error**, now resolved by `official_host`. |
| Houston Water (993b3415) | VERIFIED | CANDIDATE | TITLE_PLACEMENT_ONLY | Yes. Its statements are on houstonpublicworks.org and name the city only in titles, and that host's affiliation is CANDIDATE. Verified affiliation would make it government. |

The graph is now stronger than the registry in two cases (GRAPH_STRONGER): Nairobi City Water
(21b46e42) and Toronto Water (d26a016b), both from official-site statements. No
`LOGIC_ERROR` remains, and no registry-verified utility is missing from the graph.

## 4. "East Water" and EMOS

- **"East Water"** was a mined fragment of "the City's East Water Purification Plant". The
  registry rule now rejects names followed by purification/filtration/pumping/booster/
  desalination; it measured at 0 decisions changed and is kept. The relationship is gone; the
  original evidence is untouched.
- **EMOS** → Santiago is now **HISTORICAL** (REPORTING_PERIOD): "its statements report for
  1995 or earlier; Aguas Andinas is reported for 2026".
  - The year comes from the World Bank report's first page (1995). The PDF's metadata date
    (2026-06-02) is not trusted.
  - No EMOS-specific rule: any operator reported only by documents at least 5 years older than
    another, unrelated operator's statements.
  - The stored evidence still has no "EMOS became Aguas Andinas" statement.

## 5. Entity typing

| Entity | Typed as | Basis |
|---|---|---|
| Lake Houston (b13e0d1d) | reservoir (infrastructure, not natural) | a reservoir statement in the run's evidence |
| Lake Houston (993b3415) | lake_unspecified (natural unknown) | no such statement in that run |
| Lake Ontario, Lake Livingston and similar | lake_unspecified | never natural without explicit wording |
| Limestone Alps of Lower Austria and Styria | **region**, with the note "not a standardized water source" | — |
| Treatment/purification plants | infrastructure | — |
| Supply mains | systems | — |

No entities are merged.

## 6. Feature location

- Locating phrases ("springs in Styria", "a dam in Lesotho", "Lesotho's dams", "Katse Dam
  (Lesotho)", "Lake Livingston in Polk County") are the source's location. They are masked from
  city and document-subject decisions and never mask the target itself.
- SOURCE_LOCATED_IN is kept only for sources the run is about and places that are areas.
- The first pass produced 163 relationships, mostly unrelated dams and dates. After the filter
  there are 8, 7 of them correct on the labels, e.g. Lake Houston in the San Jacinto River
  Basin, Lake Livingston in the Trinity River System, and Ndakaini Dam in the Thika-Chania
  sub-watershed. The uncertain one is "Río Maipo en El Manzano", a gauging locality.

**Geographic false exclusions** (74-document label sheet, Phase 0 code → now):

| | Phase 0 | now |
|---|---|---|
| Relevant documents excluded (new measure) | 8 | **0** |
| Old measure (MIXED = relevant), excluded about-target / decided | 17/34 | **1/8** |
| Relevant documents or passages lost | — | 0 |
| Not-relevant labels included | 7 | 17 |

Of the 10 newly included not-relevant documents:
- 6 name the right city off-topic (company HQs, a congress venue);
- 2 are uncertain: "Vienna Woods" (a hydrothermal field) and a Colombian groundwater page
  that doesn't name Santiago;
- 2 are country-level reports with no city.

No homonym document is included (Vienna VA, Houston MS, Santiago de Cali stay excluded).

**Correction (2026-11-03, see `../phase5/PRE_EXTRACTION_CHECKS.md` §5):** an earlier version
said most of this gain came from fixing the evaluation tool. That was wrong. The 8 → 0 is a
production change made in Phase 2 (title-based search scope); the old evaluator had
under-reported it at Checkpoint B. The feature-location change itself moves 0 of the 74
labelled documents.

## 7. Relationship precision and recall (provisional)

**Precision** on 47 drafted labels: 44 decided, 3 uncertain.

| Graph status | Right / decided |
|---|---|
| VERIFIED | **19/19** |
| SUPPORTED | 6/6 |
| HISTORICAL | 1/1 (EMOS, labelled OUTDATED) |
| CANDIDATE | 12/18 |

The incorrect ones are all CANDIDATE: weak registry names such as Scottish Water, the glossary
"Water Treatment Plant", Community Drinking Water and Development Workshop. By kind:

| Kind | Right / decided |
|---|---|
| Utility → city | 22/28 |
| Source → city | 3/3 |
| Source → utility | 1/1 |
| Wholesale | 1/1 |
| DEPARTMENT_OF | 3/3 |
| SOURCE_LOCATED_IN | 7/7 |

**Utility → city recall**, against the UNCONFIRMED evaluation expectations (5 cities, 18
expected utility-run pairs):
- 10/18 found at any status, 7/18 at SUPPORTED or VERIFIED.
- Misses:
  - Santiago's group subsidiaries and SMAPA (never named in the stored evidence: a discovery
    limit);
  - CWA in run 993b3415 (never retrieved);
  - EPAL in run 707df87b (named "Luanda Public Water Utility (EPAL"; the expectation has no
    such alias, a matching limit).
- Source relationship recall can't be measured: there are no independent source-relationship
  expectations yet.

## 8. Performance and storage

| Measure | Value |
|---|---|
| Graph build, 14 runs (link re-parse excluded) | 78 s |
| Stored payload | 98 KB |
| Links per run (re-read) | 131 to 1,540 |
| Links stored per document | at most 300 |

## Known limits

- Affiliation needs the official host to name the organisation. Houston's site links to the
  department under function names, so it stays a candidate until a page states it.
- Reporting years come from a document's first page, so a footer year on a web page could
  date it. It only matters when another unrelated operator is dated at least 5 years later.
- Identities, labels and expectations are drafts; the 7 identities still await your approval.

## Needed from you

1. Review the 47 relationship labels and the 11 new rows (`relationship_labels.csv`).
2. Decide on the two acceptance counterfactuals: official-site statements as government (7
   Toronto claims), and verified departments (0 now).
3. Approve the identities (official websites are now part of each entry), so production runs
   use this geography.
4. Then the next step: geographic-aware extraction and validation, followed by the controlled
   end-to-end evaluation (needs the VT VPN).
