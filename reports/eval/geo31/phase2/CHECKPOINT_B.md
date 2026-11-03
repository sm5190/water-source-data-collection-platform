# Milestone 3.1 — Checkpoint B (Phase 2), PARTIAL

Date: 2026-11-03. All checks offline on stored runs (no searches, fetches or model calls).
Geography uses the DRAFT identity (`CITY_IDENTITY_ALLOW_DRAFT=1`); labels are UNCONFIRMED
drafts, so every figure is provisional. Nothing here shows that live research improved: that
needs controlled acquisition and end-to-end evaluations, which were not run.
Before = `6deb746` (git archive), after = `c2936d4`.

## Commits

| Commit | Content |
|---|---|
| 3335475 | Identity curation rules (shared native names → unverified; metro aliases → context); `IDENTITY_REVIEW.md` (7 cities, all still DRAFT) |
| 41c1ae3 | geo_basis per run (APPROVED / DRAFT_EVALUATION / NAME_ONLY) |
| e4110b5 | Publisher apart from document subject; MIXED; missing homonym → AMBIGUOUS; region inference; section context |
| 97926ea | Reserved utility slot (max 1 per round, within budget) |
| 7530446 | Document vs passage labels; 43 passage labels drafted (UNCONFIRMED) |
| 1a27f5c | Fix found on the labels: a foreign homonym's region (Bissau) made a multi-country report "another region" |
| c2936d4 | Slot precision fixes found on real Houston data (below) |

Full suite at e4110b5/97926ea: 1128 passed, 37 skipped; the targeted suites pass after c2936d4
(full suite not rerun after the last two commits).

## 1. Geographic inclusions and exclusions (74 labelled documents, 43 passages)

Documents (relevance = TARGET/SUPPLY_SOURCE, or MIXED with a relevant passage):

| | before | after |
|---|---|---|
| Relevant documents included | 18 | 21 |
| False exclusions (relevant, excluded) | 8 | 5 |
| False inclusions (label OTHER_PLACE, included) | 7 | 14 |
| Old Phase-0 measure (MIXED = relevant): excluded about-target / decided | 17/34 | 13/23 |
| Previously included relevant documents now excluded | — | 0 |
| Labelled-relevant passages included before, not now | — | 0 |

Regained: gov.cn article datelined Luanda, World Bank Luanda Bita project report, NFBWA minutes
(City of Houston's purification plant). The 7 new "false inclusions" are mostly **my own draft
labels being wrong for a place question**: Borealis (Vienna HQ), WaterEquity (Nairobi bank),
Amazon (Hyderabad projects), Eni (Luanda hospitals), IWA congress (Toronto venue), World Bank
VDKC (Vienna). They name the correct city in an off-topic way; their passages are drafted
NOT_ABOUT_PLACE. Geography now keeps them (right place), and topic relevance is left to the
relevance gate. Please confirm whether those document labels should be MIXED rather than
OTHER_PLACE. Passages: before 4 relevant passages falsely excluded / 0 correctly included;
after 0 / 4. "Wrong inclusions" rose 5 → 18, almost all NOT_ABOUT_PLACE mentions of the right
city (addresses, headquarters), not other places. Details: `geo_labels_compare.json`.

## 2. Reserved slot: candidate acquisition outcomes (13 stored runs, 28 rounds, 168 slots)

The registry was rebuilt per round with the new code (run history kept, no cascade).

- Slot used 4×: nairobiwater.co.ke (Nairobi Water Company; 21b46e42 r2, r3; f622ab6a r2) and
  toronto.ca utility page (Toronto Water, d26a016b r2). All correct utilities.
- Qualifying candidate but no page among discovery results: 6× (Houston Water, EMOS/Aguas
  Andinas, Vienna Water, EPAL). Slot unused: 18×.
- Sources picked, before → after: official 97 → 102, technical/academic 48 → 42,
  UTILITY-classified 18 → 19, other 5 → 5, about elsewhere 0 → 0. 13 picks displaced; 1 of them
  was cited by a claim in its run (an academic page in Luanda 80aafe20 r1, not cited by a
  selected claim).
- Two wrong picks found on the first pass and fixed generically in c2936d4:
  - houstonwatersolutions.net (a commercial company) matched the "Houston Water" claim. The
    host now has to contain a distinctive word of the name or be the name itself, and a
    commercial/directory/news host never matches before the utility's own pages are known.
  - nfbwa.com for a junk name, through a sentence that names no place. A statement now has to
    place the city in its own sentence or section.

**Coastal Water Authority (Houston b13e0d1d, 993b3415): does not qualify for the slot in any
round, so the reservation would not fetch it.** Why, from the stored evidence:
- No utility_name claim ever names CWA (claims: "Houston Water", "City of Houston Water Utility
  System").
- The only statement that CWA supplies Houston is on CWA's own site ("CWA provides untreated
  surface water to the cities of Houston, Baytown, and Deer Park"). That site was fetched in
  round 1 only because the old order put UTILITY first. Under the new order it isn't fetched in
  round 1, and nothing else carries the statement.
- Other documents describe CWA only as Lake Houston's operator ("operated and maintained by
  the Coastal Water Authority", twdb.texas.gov, fetched in round 3 / round 1 of 993b3415), as a
  partner in a list (nfbwa.com), or as project titles (CIP PDFs). None is a supply statement.
- In 993b3415, CWA was never returned by discovery at all.
- Once its own site is acquired, CWA now **verifies** as WHOLESALE_SUPPLIER (region inference:
  "State of Texas"); in Checkpoint A it didn't.
- Missing mechanism: bootstrap. A possible generic option, not implemented and needing your
  decision: let an explicit "operated/maintained by X" predicate on a water feature the run's
  water_source claims name qualify X for the slot (acquisition only, never verification).

## 3. Acceptance (no-LLM revalidation of 8 M1 + 7 M3 runs)

Both arms were revalidated (`revalidation_before.json`, `revalidation_after.json`).
- **Not done:** the arm-vs-arm comparison of automatic decisions, and the cache-only evaluation
  plus `acceptance_report.py`. The differences printed by `--verify` are against the original
  parents (older resolve-3 decisions): 9 of 15 parents show 0 differences in both arms.
- Also not done: the after-arm revalidation ran before c2936d4 (the slot changes don't affect
  validation) but after the region fix.

## Still needed from you

1. Approve the 7 identities (`IDENTITY_REVIEW.md`); all are still DRAFT.
2. Review the labels: the 43 passage labels, and whether the 6 corporate-report documents
   should be MIXED.
3. Decide on the CWA bootstrap option above.
