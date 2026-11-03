# Milestone 3.1 — Checkpoint A (Phase 0 + Phase 1)

Date: 2026-11-03. All checks below are offline on stored runs (no searches, fetches or model
calls). Geography uses the DRAFT identity (none is approved yet); labels and utility
expectations are UNCONFIRMED drafts, so every quality figure is provisional.

## Commits

| Commit | Content |
|---|---|
| 549f0e5 | P0: replay misses are `NOT_RECORDED` (never "no results"); runner checks replay coverage itself; geographic label sheet (inclusion and false-exclusion strata) |
| e2708c8 | F3–F5: client vs contractor ("diseño", "encargó a", "contracted"), untreated water = wholesale, descriptor names rejected on every path; utility-2 |
| e05e629 | F6, F7: reserved verification slot (independent of utility_name), local-language verification topics, natural place variants, `ineffective_topics` counts each search once; discovery-3 |
| 40fd31b | Acquisition by selection group; MUNICIPAL_CANDIDATE host hint (priority only, no authority) |
| 16c7bb4 | Bot-check pages = FAILED BOT_CHECK (no bypass); ROBOTS_UNREACHABLE (F9); failing hosts / DOI prefixes skipped after 2 failures (HOST_SKIPPED); F8 open-access URLs, closed works never fetched |
| 0d04159 | Replay acquirer signature (test fix) |

## 1. Acquisition order (counterfactual on stored discovery results)

Per stored round: the run's candidate pool and already-attempted URLs, picked by the old order
(37fedfd) and the new groups. Per-round only (the run's real history kept, no cascade).
`counterfactual_acquisition_all.json`.

| | old | new |
|---|---|---|
| Unverified UTILITY-classified pages picked (11 runs, 168 slots) | 45 | 20 |
| Other-place pages picked | 0 | 0 |

- **Houston b13e0d1d:** houstonpublicworks.org pages are picked in round 1 (the drinking-water
  operations page and its PDF). Dropped: precisionutility.biz, an American Water (Sam Houston)
  report, **and coastalwaterauthority.org** (the expected wholesaler: classified UTILITY and
  unverified, it lost its slot to government pages and the municipal hint).
- **Santiago b844390c:** rounds 2–3 take mop.gob.cl, dga.mop.gob.cl and siss.gob.cl documents
  instead of aguasandinas.cl / investor-site pages (which return a bot check anyway).
- **Nairobi 21b46e42:** utilityradar.com dropped; kewi.go.ke, nrc.go.ke, parliament.go.ke,
  wasreb.go.ke added; nairobiwater.co.ke pages still picked.
- Vienna, Luanda, Toronto, Hyderabad: little or no change.

**Trade-off for your decision:** government pages can now fill a whole round, so an unverified
real supplier (CWA) can wait several rounds. Option: reserve 1 slot per round for the best
claim-named candidate or UTILITY-classified page. Not implemented; your call.

**Deviation from the plan text:** unverified UTILITY pages (max 2 per round) sit *before*
technical/academic, not after, so a real utility not yet verified isn't starved in round 1.

## 2. Verification searches (offline planning on Santiago b844390c and Houston b13e0d1d)

Registry rebuilt with the new rules on the evidence each run had before the round, planned
with the fields the run actually planned:

- Santiago: Aguas Andinas is **CANDIDATE** (was NON_SERVING from the "diseño" bug), and
  `"Aguas Andinas" Santiago Chile drinking water supply` is planned in rounds 2 and 3 although
  utility_name was not among the planned fields. (The round-3 repeat is an artefact of not
  cascading; a real run moves to the next candidate or the Spanish verification query.)
- Houston: `"Houston Water" Houston Texas drinking water supply` planned in rounds 2–3.
- Weak names remain candidates (NFBWA Internal Distribution Water, USGS Oklahoma-Texas Water,
  Texas Water); claim-named candidates are verified first, so the slot goes to real names.

Houston Public Works and CWA still **do not verify** on stored evidence: their pages name Texas,
not the country. That is Phase 2 (region → country), as planned.

## 3. Revalidation (draft identity, no model calls)

- 7 Milestone 3 runs: **0 automatic decisions changed by Phase 1.** One difference against the
  stored parent (Houston "Trinity River", AUTO → review: its only source is nfbwa.com, no longer
  a verified utility) comes from commit 37fedfd, before this milestone — the baseline code
  builds the same registry.
- 8 Milestone 1 runs, evaluated with cached judge verdicts only: AUTO 15/509, 15 correct before
  and after; 0 correct values lost, 0 wrong values, 0 decisions moved
  (`acceptance/acceptance_report.md`).

## 4. Phase 0 baseline (from `../baseline/BASELINE.md`)

Geographic precision 25/32, **false exclusions 17/34** (drafts). All 17 are OTHER_COUNTRY
decisions from the publisher's host or a multi-country text (World Bank, grida.no, afd.fr, an
.it page about Vienna's fountains, gov.cn on Luanda). Planned fix: Phase 4 (publisher vs
document scope). Excluded pools for Houston/Toronto/Hyderabad are 1–3 documents.

## Tests

Full suite after 0d04159: 1088 passed, 37 skipped (live/Postgres-only). Ruff clean on changed files.

## Needed from you before Phase 2

1. **Checkpoint B — approve the 7 city identities** in `config/city_identity.yaml`
   (Houston, Santiago, Vienna, Hyderabad, Nairobi, Luanda, Toronto) after the flagged fixes
   (Hyderabad's Urdu names → unverified; Gran/Greater Santiago → context names). I can apply
   those two fixes first if you agree.
2. Decide the acquisition trade-off above (reserved candidate slot or not).
3. Optional now, required for validated figures later: confirm labels in
   `tests/evaluation/geo31/geo_labels.csv` (74) — MIXED (national/multi-city) documents count
   as "about the target" in my drafts; tell me if they shouldn't.
