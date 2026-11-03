# Research runs before and after Milestone 3

Evaluation: dataset a7f6cbdea3f4273cc7166229, judge live. Answered/comparable cells and correct/answered are absolute counts; judged cells are provisional until adjudicated.

## Answers against the reference

| City | | PROPOSED answered | correct (strict) | lenient | AUTO answered | AUTO correct | water source correct/answered | utility correct/answered |
|---|---|---|---|---|---|---|---|---|
| Santiago | before `0d7d4af8` | — | — | — | — | — | — | — |
|  | after `b844390c` | — | — | — | — | — | — | — |
| Houston | before `993b3415` | 8/49 | 1/8 | 1.5/8 | 4/49 | 1/4 | 0/0 (of 3) | 0/1 (of 1) |
|  | after `b13e0d1d` | 7/49 | 3/7 | 4/7 | 2/49 | 2/2 | 1/1 (of 3) | 0/1 (of 1) |
| Nairobi | before `f622ab6a` | 9/58 | 7/9 | 7/9 | 6/58 | 6/6 | 0/0 (of 4) | 1/1 (of 1) |
|  | after `21b46e42` | 9/58 | 4/9 | 4.5/9 | 1/58 | 1/1 | 0/0 (of 4) | 0/1 (of 1) |
| Vienna | before `e800f614` | 6/58 | 3/6 | 3/6 | 4/58 | 3/4 | 0/0 (of 4) | 1/1 (of 1) |
|  | after `6f48e458` | 11/58 | 3/11 | 5/11 | 0/58 | 0 | 1/2 (of 4) | 1/1 (of 1) |
| Luanda | before `80aafe20` | 10/31 | 5/10 | 5/10 | 0/31 | 0 | 0/0 (of 2) | 1/1 (of 1) |
|  | after `707df87b` | 5/31 | 3/5 | 3.5/5 | 1/31 | 1/1 | 1/1 (of 2) | 1/1 (of 1) |
| Toronto | before `0618af70` | 0/27 | 0 | 0 | 0/27 | 0 | 0/0 (of 1) | 0/0 (of 1) |
|  | after `d26a016b` | 15/27 | 3/15 | 5/15 | 0/27 | 0 | 0/1 (of 1) | 1/1 (of 1) |
| Hyderabad | before `f951b63c` | 10/94 | 4/10 | 4/10 | 1/94 | 1/1 | 1/1 (of 6) | 0/0 (of 6) |
|  | after `b617e84b` | 13/94 | 6/13 | 6/13 | 0/94 | 0 | 1/1 (of 6) | 1/1 (of 6) |

## Research behaviour

| City | | status | searches | site: searches (hosts) | documents (OK) | country-code hosts of OK documents | extraction calls | input / output tokens | VALID claims | review items |
|---|---|---|---|---|---|---|---|---|---|---|
| Santiago | before | CANCELLED | 32 | almarwater.com ×3 | 18 (14) | .cl 4, .co 2 | 49 | 62,476 / 12,386 | 7 | 22 |
|  | after | AWAITING_REVIEW | 34 | — | 18 (10) | .cl 9 | 51 | 79,571 / 15,916 | 15 | 25 |
| Houston | before | CANCELLED | 26 | — | 18 (16) | .ai 1 | 43 | 62,211 / 16,205 | 28 | 20 |
|  | after | AWAITING_REVIEW | 38 | nfbwa.com ×4 | 18 (17) | — | 44 | 68,361 / 13,134 | 22 | 21 |
| Nairobi | before | CANCELLED | 26 | — | 18 (13) | .ke 9 | 53 | 86,111 / 23,532 | 25 | 21 |
|  | after | AWAITING_REVIEW | 37 | portal.nairobiwater.co.ke ×5 | 18 (9) | .ke 7 | 42 | 71,802 / 16,966 | 27 | 27 |
| Vienna | before | CANCELLED | 26 | — | 18 (16) | .at 8, .eu 1 | 50 | 74,437 / 16,106 | 22 | 22 |
|  | after | AWAITING_REVIEW | 36 | — | 18 (16) | .at 16 | 46 | 62,194 / 14,167 | 20 | 27 |
| Luanda | before | CANCELLED | 23 | — | 18 (10) | .cn 1 | 59 | 137,824 / 22,243 | 18 | 26 |
|  | after | AWAITING_REVIEW | 38 | — | 18 (6) | .ao 2 | 17 | 22,703 / 4,592 | 5 | 20 |
| Toronto | before | SUCCEEDED | 26 | — | 18 (16) | .ca 13 | 62 | 92,235 / 18,606 | 0 | 0 |
|  | after | AWAITING_REVIEW | 29 | — | 18 (17) | .ca 12 | 64 | 137,476 / 18,659 | 26 | 25 |
| Hyderabad | before | CANCELLED | 26 | — | 18 (9) | .in 2 | 63 | 110,573 / 21,536 | 16 | 26 |
|  | after | AWAITING_REVIEW | 32 | — | 18 (6) | .in 2 | 27 | 35,813 / 9,046 | 22 | 30 |

## Like-for-like counts (city name and country left out)

Since Milestone 2 the city name and country come from the catalog when records are assembled,
not from extraction, so old runs get correct "answers" there that new runs can't. Without
those two fields (`cells_without_identity.md`):

| 5 cities (Toronto excluded) | PROPOSED answered / comparable | correct | partial | wrong | AUTO accepted | AUTO wrong |
|---|---|---|---|---|---|---|
| before (Oct runs) | 35/280 | 12 | 1 | 22 | 10 | 4 |
| after (Milestone 3) | 45/280 | 19 | 8 | 13 | 4 | 0 |

- Toronto is excluded: its old run (Oct 12) predates validation, so nothing in it scores
  (0/25). The new Toronto run proposes 15/25 (3 correct, 4 partial, 8 wrong).
- AUTO wrong 4 → 0: the old runs had accepted Houston "Houston Water" from a directory, two
  2018 years read from a member page's date, and Vienna's withdrawal (390,000 m³/d) wrongly.
- AUTO correct 6 → 4, explained by rules: Nairobi's city-population year (no designated
  dataset, Milestone 1), Nairobi's utility and water type (four water sources found now, so
  record-level values need attribution; two utility names were extracted), Vienna's utility
  name (proposed correctly, not auto-accepted: attribution across sources) and city population
  (Milestone 1).
- The "before" runs ran older rules (pre-Milestone-1/2 for most), so this is the end-to-end
  change across Milestones 1-3, not Milestone 3 alone.
- One wrong new-run behaviour, fixed afterwards (not affecting these numbers): Houston's run
  verified a fragment name on nfbwa.com from a first-person sentence whose place came only
  from the document, and ran 4 `site:nfbwa.com` searches; Nairobi's run bound the County
  Assembly's site as the utility's own and verified "Non-Revenue Water". Both are regression
  tests now.
- Judged cells are provisional (live judge, not adjudicated); the improved runs used the
  drafted (unapproved) city identity.
