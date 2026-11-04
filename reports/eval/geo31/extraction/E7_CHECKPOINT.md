# E7: geographic-aware extraction, controlled saved-document comparison

Date: 2026-11-04. A checkpoint for review. These are saved-document replays, so they support
**no claim about live end-to-end improvement**.

## 1. Setup

**Arms:**
- **baseline = `ff2c559`**, the commit immediately before E1;
- **improved = `8699898`**, E6.

`git log ff2c559..8699898` contains exactly the six E commits:

| Step | Commit |
|---|---|
| E1 | 48c07c4 |
| E3 | 644b707 |
| E2 | 9e1533e |
| E4 | 67ea163 |
| E5 | b6c3fe3 |
| E6 | 8699898 |

The later Firecrawl and PDF commits are in neither arm.

**Frozen code:** each arm is a `git archive` copy. It runs its own `scripts/replay_run.py`
with `PYTHONPATH=<arm>/src`. Before every replay, a preflight asserted that
`water_source_research` and `REPO_ROOT` (config, field definitions, identities) resolve inside
the arm. It passed 16/16.

**Same for both arms:**
- the `.venv`;
- the `.env` (VT `gpt-oss-120b`, read in place);
- `LLM__PERSISTENT_CACHE=off`, with one fresh process per replay, so no application cache;
- `CITY_IDENTITY_ALLOW_DRAFT` unset. In the improved arm the 7 pilot identities are APPROVED;
  in the baseline they are DRAFT, so geography is name-only. This is part of the treatment.

**Inputs:** stored documents of the parents Houston `b13e0d1d`, Santiago `b844390c`, Vienna
`6f48e458` and Nairobi `21b46e42`. Each has 18 documents; chunks: 82 / 576 / 200 / 183. The
sha256 fingerprints over (chunk id, text hash) were identical before and after
(`inputs_before.json` = `inputs_after.json`).

**Run design:** 2 repeats per arm per city, 16 replays in total, all exit 0
(`replays.jsonl`). Arm order was alternated per repeat, one replay at a time.

**Evaluation:** `evaluate_run.py --judge-cache-only` per replay, followed by
`scripts/extraction_comparison.py` (`e7_comparison.json`). Reference values stay in eval
tables and never reach research, extraction or prompts. Santiago was added to the existing
reference dataset version from the same workbook (evaluation tables only; other cities
unchanged).

**Unavoidable confounding:**
- the database schema is the current one for both arms (additive migrations up to 0023);
- the model is a remote service: drift between replays is spread by interleaving, and the two
  repeats show its variation;
- E1 (approval) cannot be separated from E2–E5 in this design.

## 2. Scoring rules

Cells are PROPOSED stage, comparable cells only.

| Category | Meaning |
|---|---|
| Confirmed | A deterministic comparison (no semantic judgment) or a person's adjudication |
| Provisional | Rests on a cached, unverified judge verdict |
| Unscored | NEEDS_REVIEW (uncached judge cell or a cell needing a person); **never counted as wrong** |

Missing predictions are coverage gaps, not errors.

For water sources, a predicted source absent from the reference is "not in reference". It may
be wrong, or the reference may name it differently; a person decides.

## 3. Results by city

Values are means over 2 repeats, with the range in brackets when the repeats differ.

| City | Arm | Source recall (confirmed) | Proposed correct | Proposed wrong | Prov. / unscored | AUTO accepted (confirmed correct / wrong) | Review items | LLM calls | Input / output tokens |
|---|---|---|---|---|---|---|---|---|---|
| Houston | baseline | 1/3 | 2.5 (2–3) | 1.5 (1–2) | 1 / 2.5 | 5.5 (1 / 0) | 13.5 | 43 | 65.8k / 14.3k |
| | improved | 1/3 | 2 | 1 | 0.5 / 3.5 | 5.5 (0.5 / 0) | 11.5 | 43.5 | 72.3k / 14.6k |
| Santiago | baseline | 2/6 | 5 | 3.5 (2–5) | 0 / 2 | 1 (0 / 0) | 14.5 | 50 | 75.9k / 15.6k |
| | improved | 2/6 | 4.5 (4–5) | 3 (2–4) | 0 / 2.5 | 1.5 (0.5 / 0) | 12.5 | 48 | 77.8k / 15.5k |
| Vienna | baseline | 0/4 (see §4) | 1 | 1 | 1 / 0 | 1 (0 / 0) | 16.5 | 41.5 | 56.3k / 13.3k |
| | improved | 0/4 (see §4) | 1.5 (1–2) | 1 | 0.5 / 0 | 2.5 (0 / 0) | 15 | 43.5 | 62.2k / 13.3k |
| Nairobi | baseline | 0/4 | 4 | 3 | 1 / 0 | 2 (1 / 0) | 19.5 | 42.5 | 68.0k / 17.2k |
| | improved | 0/4 | 5 | 2.5 (2–3) | 1 / 0 | 2 (1 / 0) | 21.5 | 41 | 68.2k / 17.4k |

**Totals** (sum of city means, baseline → improved):

| Measure | Baseline | Improved |
|---|---|---|
| Confirmed correct proposed answers | 12.5 | 13.0 |
| Confirmed wrong proposed answers | 9.0 | 7.5 |
| Provisional | 3 | 2 |
| Unscored | 4.5 | 6 |
| AUTO accepted | 9.5 | 11.5 |
| AUTO confirmed correct | 2 | 2 |
| AUTO confirmed wrong | 0 | 0 |
| Review items | 64 | 60.5 |
| LLM calls | 177 | 176 |
| Input tokens | 266k | 281k (**+5.4%**, from the geographic header and passage place tags) |
| Output tokens | 60.4k | 60.9k |

**The differences are small. Most are no larger than the variation between repeats** (e.g.
Santiago wrong answers 2–5 in the baseline, 2–4 in the improved arm). There is no evidence of
a large effect in either direction.

## 4. Water sources

**Confirmed recall is identical in both arms for all four cities:**

| City | Matched | Reference sources |
|---|---|---|
| Houston | Trinity River | 3 |
| Santiago | Maipo River, Mapocho River | 6 |
| Vienna | none | 4 |
| Nairobi | none | 4 |

Most of the remaining differences can't be scored by name matching, and with cached verdicts
only the judge didn't pair them:

- **Vienna:** the reference names "Rax-Schneeberg-Schneealpe Karst Springs", "Hochschwab Karst
  Springs", "Lobau Groundwater" and "Mitterndorfer Senke Aquifer". Both arms propose
  descriptions that plausibly correspond to the first three: "First Vienna Spring Water Main
  (… Schneeberg, Rax, Schneealpe)", "springs of the Hochschwab massif", "Lobau, Moosbrunn,
  Danube Island and Nussdorf wells". **Vienna's 0/4 is unscored, not zero.** It needs a person
  or the judge.
- **Houston:** both arms propose reservoirs (Lake Houston, Conroe, Livingston). The reference
  lists the rivers they impound (Trinity, San Jacinto). "Gulf Coast Aquifer" (auto-accepted in
  both improved repeats and in 1 of 2 baseline repeats) contains the reference's "Chicot &
  Evangeline Aquifers". Whether that counts is for a person.
- **Santiago:**
  - "Río Maipo" (auto-accepted in both improved repeats and one baseline repeat) is the Spanish
    name of the reference's Maipo River but is counted as "not in reference". In improved
    repeat 1, both "Maipo River" and "Río Maipo" were auto-accepted as **separate entities**
    (entities are never merged by design). That is a duplicate a person would have to collapse.
  - The improved arm proposed fewer unmatched sources (1 vs 3: the baseline's "Ramon's Creek",
    "Cerro Negro-Lo Mena wells", "Río Clarillo").
- **Nairobi:** both arms propose Irati, Gikigie and Maragua rivers and groundwater. The
  reference lists Thika, Chania and Ruiru rivers and Kikuyu Springs. No arm difference.

## 5. Automatic acceptance

**No confirmed-wrong AUTO value in any replay of either arm.**

Values accepted in both repeats of one arm and in neither repeat of the other:

| City | Only the improved arm | Only the baseline arm |
|---|---|---|
| Houston | `water_source = Gulf Coast Aquifer` | `jurisdiction = Texas`, a confirmed-correct value |

The Texas difference is model variance, not E1–E6: in improved repeat 2 the model produced no
jurisdiction claim at all, while improved repeat 1 extracted Texas (VALID, AUTO_STRICT). Two
repeats can't separate this from an effect.

**Vienna, improved arm:** also auto-accepted "Lobau, Moosbrunn, Danube Island and Nussdorf
wells", "Seven Springs in Karlgraben" and `water_type = Groundwater` for Hochschwab. All are
unscored by the reference matching; plausibly correct, not verified.

## 6. Useful evidence skipped (E3)

**In the replays:** the improved arm skipped calls as OFF_TOPIC:
- Santiago `water_quality_year`: 3 per replay;
- Santiago `water_transfer_system`: 1, in one replay;
- Houston `city_population_reference_year`: 1 per replay.

What the baseline produced for those fields:

| Field | Baseline output | Effect of the skip |
|---|---|---|
| Santiago `water_quality_year` | "2022" (a garbage-sampling study), **confirmed MISMATCH** for 2 reference sources in repeat 2 | Removed 2 confirmed-wrong answers |
| Santiago `water_transfer_system` | "Laguna Negra aqueduct", MISMATCH in both baseline repeats | Same answer in the improved repeat that didn't skip it |
| Houston `city_population_reference_year` | Nothing in any replay | No change |

**Confirmed-correct answers lost: 0.**

**The two productive skips found earlier** (no-model counterfactual of 14 saved runs):
- **Santiago `b844390c`, 2022:** the quote is "in 2022, researchers … conducted a national
  sampling of garbage in the rivers of Chile". It is not drinking-water quality, so the skip
  was correct.
- **Luanda `80aafe20`, 2023:** the quote is the bibliography line "(2023). Integrated Annual
  Report. Johannesburg: Rand Water", another city's utility. The skip was correct on the
  evidence. **However, the reference year for Luanda is 2023, so a value-only comparison would
  count this as a correct answer lost.** It is reported here as a coincidental value match
  unsupported by its evidence.
- No gate change is proposed.

## 7. Label limitations

- The reference workbook is one manual answer per cell. It is not person-confirmed for these
  runs, and it names sources differently from the evidence (Vienna, Houston reservoirs vs
  rivers, Spanish names). So deterministic source recall understates both arms equally.
- The semantic judge is not validated (`JUDGE_VALIDATED=False`). Cached verdicts are
  provisional; uncached cells are unscored and never counted wrong.
- Two repeats per arm show model variation but don't establish significance.
- Saved-document replay holds discovery and acquisition fixed. Live runs would differ.

## 8. Reading

On the same documents, E1–E6 left confirmed water-source recall unchanged. The changes in
correct and wrong proposed answers are slight: +0.5 correct and −1.5 wrong across 4 cities,
within repeat variation. They come at **+5.4% input tokens** and 3.5 fewer review items in
total, with no confirmed-wrong automatic acceptance in either arm. The topic gate removed some
confirmed-wrong answers and no confirmed-correct ones.

The main obstacle to measuring source accuracy is scoring coverage, not extraction: most
candidate sources can't be matched to the reference by name. Next steps for a person:
- pair the unscored Vienna, Houston and Santiago source cells, or run the judge on them
  (a model cost) before drawing source-level conclusions;
- decide whether "Gulf Coast Aquifer" ~ "Chicot & Evangeline Aquifers" and "Río Maipo" =
  "Maipo River" should be evaluation aliases.

Stopped here, as asked. No live end-to-end evaluation, no Milestone 3.2, Firecrawl disabled,
P2 not started.
