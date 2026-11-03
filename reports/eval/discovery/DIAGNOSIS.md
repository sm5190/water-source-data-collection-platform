# Milestone 3 — why Santiago and Houston verified no serving utility; scholarly fetches

Date: 2026-11-02. Read-only diagnosis: stored fresh discovery-only runs
(`reports/eval/discovery/fresh/`, 8 runs, with search snapshots for the improved arm), the 7
new full runs in Postgres, and the logs. No research data was changed. The proposed fixes were
tried **in memory only** (scratchpad what-if scripts, monkeypatched rules on the stored
evidence); nothing is committed.

"Expected" utilities come from `tests/evaluation/discovery/utility_expectations.yaml`, which is
UNCONFIRMED. Everything below is **measured on stored data** unless marked *provisional*.

## 1. Houston: the utility's own pages were found but never fetched

| Step | What happened | Evidence |
|---|---|---|
| Search | **Found.** `houstonpublicworks.org` pages were returned **29 times** in the full run (b13e0d1d), at rank 1 in 7 queries; 10 and 18 times in the two improved fresh runs. | `discovery_results` |
| Acquisition | **Never fetched** (0 of 18 slots in the full run, 0 of 36 in the improved fresh runs). Classification v2 calls those pages OTHER (`/drinking-water-operations`) or TECHNICAL (the PDF report). Acquisition orders by tier (UTILITY > GOVERNMENT > … > OTHER), so the slots went to UTILITY-classed pages of other organisations (precisionutility.biz, an American Water Sam Houston report, nfbwa.com, westutx.gov) and to `.gov` pages. | `source_documents`; `classify()` on the URL |
| Access | The page is fetchable (HTTP 200, 165 kB, checked today). | live check |
| Relationship evidence | The page states the service in the first person and in the third person ("Our number one priority is protecting public health by supplying citizens with high quality drinking water"; "The City of Houston is the regional water provider for Harris County…"). | page text |
| Verification rule | **Even if fetched, it would not verify today.** US pages rarely name the country. `document_scope` decides the country from country names only, so a page naming "Texas" but not "United States" has country UNKNOWN. An own-site statement then counts only as SUPPORTING, and one supporting host is not enough. | what-if: 0 verified (current) → Houston Public Works VERIFIED (with the region fix) |

The Coastal Water Authority (the expected wholesaler) has the same scope problem: "CWA
provides untreated surface water to the cities of Houston, Baytown, and Deer Park" sits on its
own site, but the page names Texas, not the country.

There is also a **role bug**. If only the scope were fixed, the Coastal Water Authority would
verify as a *drinking-water operator*, because "untreated surface water" is not recognised as
wholesale. The two fixes must go together; with both applied it verifies as WHOLESALE_SUPPLIER.

**Cause:** acquisition ordering, from the classification of a city department's host (main);
plus geography that is too strict for regions that imply the country. Not search coverage, and
not access.

## 2. Santiago: inaccessible sites, a rule bug, and verification searches that never ran

| Step | What happened | Evidence |
|---|---|---|
| Search | Topic queries found the utility's sites: aguasandinas.cl ×2 and the investor site aguasandinasinversionistas.cl ×5 in the full run. **No query ever named Aguas Andinas.** Its name was extracted as a utility_name claim in round 1, so the utility field left the search plan (only fields needing evidence are planned). Verification queries hang off the utility field, so they never ran (0 in rounds 2–3). | `field_claims` (extraction_round 1); `discovery_results` (round 2–3 fields) |
| Search (form) | Every query starts "Santiago Santiago Metropolitan Region Chile …". The region is added for disambiguation, but its name already contains the city. Spanish queries carry the English region name. | `discovery_results` |
| Access | **aguasandinas.cl is behind a bot check** (Incapsula: a 212-byte page, or a 1,054-byte "Request unsuccessful" page stored as OK). **siss.gob.cl, the sanitation regulator that lists each commune's concessionaire, times out from this network** (DNS resolves, TCP doesn't answer). It is logged as `ROBOTS_DISALLOWED` because an unreachable robots.txt is treated as a refusal, which is a misleading label. | stored chunk text; `curl` today |
| Relationship evidence | **Missing in what was fetched.** Government PDFs (Ministry of Environment, planesynormas.mma.gob.cl) name Aguas Andinas 28 times, but only as plant operator or study client ("Aguas Andinas potabiliza el agua que toma del río Maipo"; "Informe preparado … para Aguas Andinas"). None says it serves Santiago. The investor-site pages are history pages. One World Bank PDF says "Aguas Andinas, the company managing water and sanitation for the Santiago metropolitan region", but the PDF's line break splits the sentence, and a metro-area name supports at most. | chunks |
| Verification rule | **One bug, otherwise correctly strict.** The contractor pattern matches the Spanish *noun* "diseño" (design): "Aguas Andinas encargó a la empresa Hidrosan el diseño…" made Aguas Andinas NON_SERVING in the full run. That status also stopped verification queries, which only run for CANDIDATE or ambiguous candidates. Without the bug it is CANDIDATE, which is right for the evidence held. | what-if: NON_SERVING → CANDIDATE |

**Cause:** search planning (verification queries never ran) plus access (the utility and regulator
sites are unreachable for this crawler), plus one rule bug. The verification rule did what it
should with the evidence it had.

**A government source instead of the utility site:** SISS is the right one, and it is
unreachable here. Untested alternatives that are reachable: the regulator's commune and
concession pages mirrored on other gob.cl hosts, the Biblioteca del Congreso (bcn.cl) concession
decrees, and Ministry of Public Works documents. None was fetched, because no query asked for
the concessionaire. A query on the candidate's name plus "concesionaria" (or the local
"regulator" topic, which ran only once) is the way to reach them. Untested: whether any
fetched page states it.

## 3. Other precision problems found on the same data (what-if, current code)

- **Nairobi:** "Non-Revenue Water" is still VERIFIED in run 21b46e42. It is mined in its
  "Full Name (ABBR)" form, "Non-Revenue Water (NRW)", and names with an acronym skip the
  specificity filter. Commit 37fedfd fixed only the plain form.
- **Houston:** on the Houston Public Works page, the heading "Drinking Water Operations" would
  verify as a utility of its own once the region fix applies (same cause: a name built only
  from descriptors).
- **PDF line breaks:** joining continuation lines recovers the World Bank sentence, but in the
  what-if it also verified "Borehole Water" (Nairobi) and added junk non-serving names
  (Toronto). **Not proposed now:** it doesn't change Santiago or Houston, and its precision
  cost is real.

## 4. Scholarly fetches

| Runs | Fetch attempts | Scholarly | Scholarly OK | OK and relevant (provisional labels) | Cited by a claim |
|---|---|---|---|---|---|
| 8 fresh discovery-only | 144 | 61 (42%) | 11 | 2 | — |
| 7 new full runs | 126 | 18 (14%) | 0 | 0 | 0 |

Where the failures come from:
- `semanticscholar.org/paper/...`: **25 of 25 came back EMPTY.** The page is a JavaScript
  shell; the adapter uses the paper's page URL before its open-access PDF.
- `doi.org` (the OpenAlex landing page): 23 distinct DOIs. **None of the 7 non-open-access
  works produced text**, and 3 of the 16 open-access ones did. The other 13 open-access works
  failed on publisher bot walls (MDPI, Wiley, Taylor & Francis, Elsevier, Springer). The
  open-access PDF links on those publishers also return 403 to our crawler (4 of 4 tested).
- What worked: open repositories and publishers (PLOS, SciELO, USGS Publications, JSTOR open
  reports, institutional repositories).

## Proposed narrow fixes (none implemented; each needs a commit, tests and the revalidation check)

| # | Fix | Files | Effect on stored data (what-if) | Risk |
|---|---|---|---|---|
| F1 | A host label `<city>publicworks` (and `publicworks<city>`) is the city's own department, classified GOVERNMENT (municipal). Classification is still not authority. | `discovery/normalize.py` | Houston Public Works pages enter the GOVERNMENT tier in acquisition (it ranked 1st, so it would likely be fetched — not proven without a run). | Low |
| F2 | Document scope: a document whose region is the target region and that names no other country counts as the target country. | `services/geo_match.py` | Houston Public Works and Coastal Water Authority verify; no wrong verification on the other 14 stored runs. Also feeds the relevance gate and acceptance authority, so revalidation must be run. | Medium: a region name shared across countries (Punjab) — guard: no other country named |
| F3 | Wholesale role: "untreated / raw surface water" to cities. | `services/utility_registry.py` | Required with F2: CWA → WHOLESALE_SUPPLIER instead of operator. | Low |
| F4 | Contractor: Spanish noun "diseño" is not a verb; a candidate that commissions or awards work ("encargó a", "contracted", "awarded … to") is the client. | `services/utility_registry.py` | Aguas Andinas NON_SERVING → CANDIDATE. | Low |
| F5 | Names made only of descriptors + "water" are rejected for the acronym and title paths too. | `services/utility_registry.py` | Removes Non-Revenue Water (NRW) and "Drinking Water Operations". | Low |
| F6 | Verification queries for unverified candidates get their own slot each round (1 of the 6), whether or not utility_name still needs evidence. | `discovery/queries.py`, `agents/nodes.py` | Santiago would have searched `"Aguas Andinas" Santiago Chile …` in rounds 2–3. Search-only cost. | Low |
| F7 | When the region name contains the city name, the region replaces the city in place terms ("Santiago Metropolitan Region Chile"). | `discovery/queries.py` | Removes the duplicated "Santiago Santiago". | Low |
| F8 | Scholarly: the Semantic Scholar adapter uses `openAccessPdf.url`, then the DOI, never the paper page. OpenAlex asks for `best_oa_location`; non-open-access works stay discovery metadata (abstract only, never fetched). Per run, a host that returned AUTH_REQUIRED or EMPTY twice is skipped for the rest of the run (event logged); `doi.org` failures count against their DOI prefix. | `discovery/semantic_scholar.py`, `discovery/openalex.py`, `acquisition/service.py` | Both run sets: 25 S2 pages + 7 non-OA DOIs = 32 of 79 scholarly attempts freed, and none of them had produced text; the host rule frees later repeats (MDPI ×3). Repository papers (SciELO, PLOS, USGS) are unaffected. | Low |
| F9 | Robots.txt unreachable → error `ROBOTS_UNREACHABLE` (still not fetched). | `acquisition/safe_http.py` | Labelling only (SISS). | None |

Not proposed: loosening the verification rule itself, PDF line joining (above), or any
crawler workaround for bot checks (no bypass, by design).

## What would complete Milestone 3

1. Approve the fixes (any subset); I implement them in separate commits, then re-run the
   offline checks (revalidation of the M1 runs, the counterfactual, discovery_eval) and report
   prevented and lost values.
2. You confirm the labels and expectations (`REVIEW_GUIDE.md`).
3. End-to-end comparison C (needs the VT VPN): baseline e0c8320 vs the improved branch,
   Santiago and Houston only, A-B-B-A, the same settings checked by the runner, persistent LLM
   cache off.
4. A new label pool for the C runs' documents (built after the runs, evaluation-only), then the
   metrics you listed, measured on confirmed labels with provisional figures kept apart.
