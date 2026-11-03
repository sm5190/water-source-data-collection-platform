# Milestone 3.1 — Phase 0 baseline

Date: 2026-11-03. Baseline code: `37fedfd` (frozen as a git archive for the controlled runs).
Versions: extract-5, normalize-6, resolve-5, classify-2, discovery-2, retrieval 10, utility-1.

Stored runs used (read only): the 7 Milestone 3 runs (b844390c Santiago, b13e0d1d Houston,
21b46e42 Nairobi, 6f48e458 Vienna, 707df87b Luanda, d26a016b Toronto, b617e84b Hyderabad) and
the 7 earlier runs of the same cities (0d7d4af8, 993b3415, f622ab6a, e800f614, 80aafe20,
0618af70, f951b63c).

**Geography basis:** no city identity is approved, so production ran name-only. Everything
below uses the DRAFT identity (`CITY_IDENTITY_ALLOW_DRAFT=1`, evaluation only), i.e. "as if
approved". All labels are drafts (UNCONFIRMED); every figure is provisional.

## What changed in Phase 0 (no research behaviour change in live runs)

- A replayed search missing from the recording is a `NOT_RECORDED` failure, never an empty
  result; it is not counted by `ineffective_topics`. The experiment runner checks coverage
  itself (so the baseline code is covered too) and marks a run `like_for_like: false` when any
  planned search was not recorded; `discovery_compare.py` reports it apart and leaves those
  searches out of the counts. Related-paper and Crossref calls are listed as NOT_REPLAYED.
- New geographic label sheet (`tests/evaluation/geo31/geo_labels.csv`, key file apart): one
  place question per document (TARGET / SUPPLY_SOURCE / MIXED / OTHER_PLACE / UNCERTAIN),
  sampled equally from documents the rules include and exclude, so false exclusions get the
  same scrutiny as wrong inclusions (`evaluation/geo_labels.py`, `scripts/geo_labels.py`).

## Geographic precision and false exclusions (74 documents, 37 per stratum; drafts)

| | about target / decided | uncertain |
|---|---|---|
| Precision (documents the rules keep) | 25/32 | 5 |
| False exclusions (documents the rules treat as elsewhere) | **17/34** | 3 |

Per city in `geo_labels_baseline.md`. The excluded pools are small for some cities (Houston 3,
Toronto 1, Hyderabad 1 documents), so those per-city rates rest on 1–3 documents.

**Main cause of false exclusions (on these drafts): the publisher's country is taken as the
document's subject.** All 17 are OTHER_COUNTRY decisions: 12 from the host alone (search
results never fetched) and 5 from document text (multi-country reports, a gov.cn article, a
North Fort Bend Water Authority minutes PDF for Houston). Examples: grida.no and afd.fr pages about Nairobi, a Vienna drinking-water page
on an .it site and an Interreg (.eu) pilot on Vienna Water, a gov.cn article datelined Luanda,
World Bank reports on Luanda projects, USDA/trade.gov reports on Chile, Kenya fiches on .ch/.nl
hosts. 10 of the 17 are MIXED and 7 TARGET; MIXED means national or multi-city documents, and whether those count as
"about the target" is a labelling decision for you. The correct exclusions are homonyms and unrelated places (Vienna VA, Houston MS,
Santiago de Compostela/Tolú, ideam.gov.co, corporate reports). This is the publisher-vs-
document split planned for Phase 4; it also affects ranking (×0.5) and acquisition (last,
1 per round) through `geo_scope`.

## Other baseline figures

`discovery_eval_draft.md` (same runs): counterfactual steering, utility accuracy against the
UNCONFIRMED expectations, acquisition precision on `labels_sample.csv`.
