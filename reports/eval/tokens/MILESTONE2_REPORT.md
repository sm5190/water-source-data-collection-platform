# Milestone 2: LLM token efficiency — checkpoint report (Santiago + Nairobi)

Status: phases 0–5 implemented and committed; deterministic checks on all five saved runs;
controlled VT replays for Santiago and Nairobi. **Houston, Luanda and Vienna replays wait for
your go-ahead** (plan §4 step 5).

## Results

### Measured with real VT calls (same saved documents, original rounds)

| | Santiago baseline | Santiago optimized | Nairobi baseline | Nairobi optimized |
|---|---|---|---|---|
| Model calls | 50 | **32.5 (−35%)** | 54.5 | **50.5 (−7%)** |
| Input + output tokens | 75,836 | **55,935 (−26%)** | 125,467 | **118,093 (−6%)** |
| Calls returning no claim | 42 | 27 | 32.5 | 31.5 |
| Wall time | 98.5 s | 70.5 s | 147.5 s* | 103.5 s |
| Correct PROPOSED answers (of 56 cells) | — | — | 5.0 (4–6) | 6.0 (6–6) |
| PROPOSED accuracy (strict) | — | — | 0.24 | 0.41 |
| Automatically accepted values that were correct | — | — | 58% | 100% |
| Tokens per correct PROPOSED answer | — | — | 25,809 | **19,682 (−24%)** |
| Workbook sources proposed | — | — | 2/4, 2/4 | 1/4, 2/4 |

Means of 2 repeats per arm ("optimized" = final code, `optimized_v2` in comparison.md). Santiago
has no reference row: efficiency only. *One baseline Nairobi run overlapped with another
replay; the clean one took 109 s.

**Cache:** re-running an identical replay read every extraction from the persistent cache —
0 model calls, 0 tokens spent; 56,514 (Santiago) and 117,467 (Nairobi) tokens avoided, reported
apart from spent tokens.

### Deterministic, no model calls (all five saved runs)

Counterfactual replay: current code, extraction answered with each run's recorded claims.

| | Calls | Tokens (est.) | Skipped slots that had a usable claim |
|---|---|---|---|
| Santiago | 49 → 32 | −29% | 0 |
| Luanda | 66 → 51 | −16% | 0 |
| Vienna | 57 → 45 | −13% | 0 |
| Houston | 58 → 53 | −10% | 0 |
| Nairobi | 57 → 52 | −9% | 0 |
| **All five** | 287 → 233 | **−14%** | **0** |

Every run keeps all its water_source candidates.

## Against the target

- 30–40% is reached for runs like Santiago, whose later rounds bring documents about other
  places (−35% calls, −26% to −29% tokens).
- For source-rich cities the saving is 6–16%. Their later rounds are mostly legitimate calls on
  city-relevant evidence, and gating them would cost recall.
- What remains is mostly per-call overhead (about 770 fixed prompt tokens per call) and
  gpt-oss reasoning output (about 250 tokens per call, even for empty answers). Reducing those
  needs a different lever (see "Next levers").

## Quality: what changed and why

- **Fields no longer extracted:** `references` (generated at assemble) and catalog
  `city_name`/`country` (filled from the catalog). In the evaluation they now show as missing
  at the PROPOSED/AUTO stages. Scores above exclude them in both arms.
- **The gate never skipped a slot that produced a usable claim** (attribution per field and
  round, all runs).
- **Round-1 bundles are identical** in 29 of 32 fields for both cities. The 3 that differ are
  the pruned fields.
- **One regression was found and fixed.** The phase-3 "stable header" (dropping the Target
  fields section) made the model miss Ruiru River, which a utility page names only as a dam's
  location. With the section the model found it in 8 of 9 calls; without it, 3 of 10 (replays
  plus a 6-vs-6 A/B on VT). The header is restored (extract-5). Caching doesn't need it: the
  cache-reading replays still hit 100%.
- **Remaining differences sit within the model's run-to-run spread.** The baseline's own two
  Santiago repeats returned 1 vs 5 water_source values. Two repeats per arm can't settle a
  one-source difference in Nairobi's source recall.
- **Milestone 1 validation and resolution are unchanged.**

## What was built (commits on evaluation-pipeline)

1. `d007afc` Token audit (`scripts/token_audit.py`): where tokens go, per round, field and stage.
2. `c0f464b` No extraction for references and catalog city/country.
3. `29c6962`, `6f297a5` Relevance gate (NO_RELEVANT_EVIDENCE):
   - a passage counts if the city is named nearby or in the same document/page, it is on the
     city's own website, it is on a verified utility's site, or it names a known local water
     feature (upstream or regional);
   - scores are never used;
   - plus the counterfactual tool.
4. `8dc63ed` Persistent extraction cache (migration 0017):
   - multi-component key;
   - hits re-grounded against the run's own passages;
   - hits spend nothing; avoided tokens reported apart; app cache vs provider cache shown
     separately.
5. `c260591`, `a251a72` Deduplication with provenance: the most authoritative copy is shown,
   other copies are kept and attached as verified corroborating evidence.
6. `fc4781e` NO_NEW_ENTITY re-extraction skip: built, **off**. It never fired on the five runs.
7. `7068a67` Target-field header restored (extract-5).

Tests: 932 passed, 37 skipped; Postgres integration 24 passed (migration up/down, drift).

## Next levers (not done; need your decision)

- **Reasoning effort** for gpt-oss: output is about 18% of tokens, mostly reasoning. This
  changes generation, so it would be an opt-in measured experiment.
- **Several fields in one call** for small, related fields sharing evidence. This removes the
  per-call overhead, but changes prompts and schemas and needs its own quality check.
- **More repeats** if a one-source difference in recall must be resolved statistically.

## Files

- `replays/comparison.md` / `comparison.json`: every replay, every metric, ranges.
- `replays/replays.jsonl`: run ids per arm.
- `replays/replays_failed_attempt.jsonl`: the first driver attempt. Two baseline setup
  failures, no model calls. Its optimized runs completed and are included, labelled
  "optimized" (pre-fix header).
- `baseline/`: token audit of the original runs.
- `counterfactual/`, `counterfactual_no_new_entity/`, `counterfactual_phase2/`.
