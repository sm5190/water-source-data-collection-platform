# Milestone 3 — checkpoint report (Santiago and Houston)

Status: implementation phases P0–P7 committed; offline evaluation (A) done; the fresh-discovery
comparison (B) done for Santiago and Houston **in discovery-only mode**; the end-to-end
comparison (C) is **not done** (the VT model was unreachable: no VT VPN). Work stops here for
your review, as planned.

**Everything labelled provisional below depends on drafts you haven't confirmed yet:** the
city identity file (`config/city_identity.yaml`), the document labels
(`tests/evaluation/discovery/labels_sample.csv`, `fresh_pool.csv`) and the utility
expectations (`tests/evaluation/discovery/utility_expectations.yaml`). No validated (person-
confirmed) figure exists yet.

## Commits

| Phase | Commit | Content |
|---|---|---|
| P0 | b03e865 | Discovery audit of stored runs |
| P1 | d751db6 | City identity draft (GeoNames + Wikidata); catalog without workbook answers |
| P2 | bc51374 | Geographic matching on City + Region + Country |
| P3 | 701831e | Utility registry (roles, verification strength, predecessors); migration 0018 |
| P4 | 96209cb | Source classification v2 (publisher kind, version); migration 0019 |
| P5 | 6c11e13 | Acceptance: UTILITY pages official only for verified serving utilities |
| P6 | ac13c81 | Discovery v2 (verified-host site: queries, verification, fallback, suppression) |
| P7 | 527ffd1, 9036b82, f013628 | Evaluation tooling; registry precision fixes from real data; experiment runner; houstonms.gov fix |

Tests: 1053 passed, 37 skipped (live/Postgres-only); Postgres integration suite 24 passed;
dev database migrated to 0019.

## Root causes confirmed on stored runs (P0)

- Santiago 0d7d4af8: almarwater.com became the "utility host" from its classification alone;
  3 `site:almarwater.com` searches; Almar pages were the only evidence in 16 of 49 extraction
  calls; bare-city Spanish queries reached Colombian (ins.gov.co, minvivienda.gov.co) and
  Mexican (conagua.gob.mx) government sites.
- Houston 993b3415: msdh.ms.gov (Houston, **Mississippi**) fed 19 extraction calls (4 as the
  only source); two directories (leadingutilities.org, waterutility.ai) were the "utility
  hosts"; houstonpublicworks.org was classed TECHNICAL.
- Vienna e800f614: viennava.gov (Vienna, **Virginia**) fed 14 calls.

## A. Offline, stored runs (no network, no model calls)

**Utility identification** (5 runs; registry built by the new rules on each run's evidence vs
the UNCONFIRMED expectations):

| City | Old rule's "utility hosts" (classification) | New rules, draft identity | New rules, no identity |
|---|---|---|---|
| Santiago | almarwater.com, riotimesonline.com, aguashorizonte.cl | none verified (one news statement only) | none |
| Houston | waterutility.ai, leadingutilities.org, greaterhoustonwater.com | Houston Water ✔ | Houston Water ✔ |
| Luanda | worldwater.org | Luanda Provincial Water Company (EPAL) ✔ | ✔ |
| Nairobi | nairobiwater.co.ke | Nairobi Water Company ✔ | none |
| Vienna | leadingutilities.org | Vienna Water ✔ | none |

Verified: 4 correct, 0 wrong (draft identity); 2 correct, 0 wrong (no identity). Missed: 5 of
the 9 expected entries (Santiago's 4 and Houston's wholesaler). The first real-data run had verified "Surface Water"/"Waste Water"
(Nairobi), the government water directorate (Santiago) and "Favoriten Water" (a water tower,
Vienna): those precision bugs were fixed and are regression tests now.

**Automatic acceptance** (revalidation of the Milestone 1 runs, no model calls, judge
cache-only; `reports/eval/discovery/acceptance/`):

| | AUTO accepted / comparable cells | correct | correct values lost | wrong values prevented |
|---|---|---|---|---|
| Milestone 1 | 14/509 | 14 | — | — |
| M3, no approved identity (production today) | 12/509 | 12 | 2 | 0 |
| M3, identity approved (draft) | 15/509 | 15 | 0 | 0 |

- The 2 losses without an identity (Nairobi "Ruiru River", Toronto "Toronto Water") are
  values whose only official source is a utility page that can't be verified until the
  identity establishes the city's country (rule: UTILITY_RELATIONSHIP_UNVERIFIED /
  UTILITY_SERVICE_NOT_STATED). Both return with the identity.
- 0 wrong values prevented because Milestone 1 had already removed the Houston five; the new
  rule now also covers pages like Almar's for any future claim.
- 1 newly accepted with the identity: Nairobi's utility name (correct).
- Small numbers: 15 AUTO cells out of 509 is low coverage; most values still go to a person.

**Labels (provisional):** 69 stored documents, drafted: relevant 32, irrelevant 30,
uncertain 7. Publisher kind matches the draft labels 44/68 under classify-2 vs 35/68 under
classify-1.

## B. Fresh discovery, Santiago and Houston (live search, discovery-only, A-B-B-A)

Setup: baseline = e0c8320 (last Milestone 2 commit, normalize-4/resolve-4/extract-5) vs
improved = 9036b82; same SearXNG, OpenAlex, Semantic Scholar, Crossref, budgets (6 queries and
6 documents per round, 18 per run, 3 rounds), checked by the runner's settings snapshot. Both
arms ran the real discover and acquire nodes with every field open each round and **no
extraction** (VT was unreachable), so later rounds are steered only by documents, not claims.
The improved arm used the drafted identity. One session per city, 2 repeats per arm; search
snapshots and timestamps in `reports/eval/discovery/fresh/`. A first session was discarded
(network outage, then a registry bug: superseded files kept in `fresh/`).

| | Baseline (4 runs) | Improved (4 runs) |
|---|---|---|
| searches | 134 | 141 |
| `site:` searches on hosts that don't serve the city | **8** (mywater.us ×4, a private Texas utility; aguasdesantiago.com.ar ×4, Santiago del Estero, Argentina) | **0** |
| documents fetched about another place | 2 (Argentina) | 0 |
| documents OK | 39 | 39 |
| relevant / irrelevant / uncertain (provisional) | 13 / 21 / 5 | 15 / 13 / 11 |
| acquisition precision (provisional, uncertain excluded) | 13/34 | 15/28 |
| pooled recall (provisional; pool = relevant docs any run or the stored run found) | Houston 5/27 and 5/27; Santiago 0/10 and 3/10 | Houston 1/27 and 11/27; Santiago 3/10 and 0/10 |
| utilities verified from the run's own evidence | 0 (one wrong mined name in a registry built afterwards) | 0 |

Reading:
- The steering failures the milestone targets are gone in the fresh runs: no `site:` search
  and no acquisition priority for an unverified host, no other-place documents.
- Precision is better on these 8 runs but **repeat-to-repeat variance is as large as the
  difference** (Houston improved 1/9 vs 11/15). Two repeats per arm can't establish a gain.
- Neither arm verified a utility from its own fresh evidence: aguasandinas.cl answers
  automated requests with a bot-check page; Houston Public Works pages weren't returned in the
  improved runs. Verification relies on explicit statements, so when the utility's own site
  can't be fetched, the utility stays a candidate (by design, not guessed).
- Shared problem not addressed in this milestone: about a third of each arm's fetch budget
  goes to scholarly links (doi.org redirects, Semantic Scholar pages) that fail or come back
  empty.

## C. End-to-end impact — not done

Needs the VT model (VPN) for extraction. Not started: correct water sources, field coverage,
quantitative fields, geographically irrelevant claims, review burden and tokens per verified
correct value on fresh runs.

## Known limits and open issues

- Without an approved identity the stricter rules cost 2 correct AUTO values (above) and
  bare-city own-site statements only support, never verify.
- A company page titled like a utility ("Sustainability - <X> Water") still classifies as
  UTILITY_OPERATOR; only the registry prevents it from carrying authority.
- Name mining is heuristic: one wrong mined name remained in a baseline run's evidence
  ("NFBWA Internal Distribution Water", verified by nobody but listed).
- Discovery-only runs are not full runs; claims steer real later rounds.

## What I need from you

1. Review and approve `config/city_identity.yaml` (sheet: `reports/eval/discovery/identity_review.md`;
   Bogotá, Kyiv and Ho Chi Minh City have unverified regions).
2. Confirm or correct the labels in `tests/evaluation/discovery/labels_sample.csv` (69) and
   `fresh_pool.csv` (56), and the utility expectations; then the figures above can be
   recomputed as validated (`scripts/discovery_eval.py`, `scripts/discovery_compare.py report`).
3. Decide on the next step: (a) end-to-end comparison (C) once the VT VPN is available;
   (b) more repeats for Santiago/Houston to get past the variance; (c) the further cities
   proposed (Hyderabad, Cali, Nairobi, Vienna).
