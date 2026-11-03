# Controlled replays: before / after

Same saved documents in their original acquisition rounds, real VT model calls. Baseline = code before Milestone 2 (4c63007); optimized = milestone code with the persistent cache in write_only (every call paid); *_cache = one more optimized replay reading the persistent cache; optimized_v2 = after restoring the target-field header (extract-5). Values: mean of repeats, with the range across repeats (the model's run-to-run variation). Evaluation uses cached judge verdicts only; city_name / country are scored apart (catalog-filled after).

## Nairobi

| metric | baseline (mean) | baseline range | optimized (mean) | optimized range | optimized_cache (mean) | optimized_cache range | optimized_v2 (mean) | optimized_v2 range | optimized_v2_cache (mean) | optimized_v2_cache range |
|---|---|---|---|---|---|---|---|---|---|---|
| Model calls | 54.5 | 52–57 | 49.75 | 48–51 | 0 | 0–0 | 50.5 | 50–51 | 0 | — |
| App-cache hits | 0 | 0–0 | 0 | 0–0 | 50 | 50–50 | 0 | 0–0 | 51 | — |
| Input tokens | 100179.5 | 95355–105004 | 91740 | 88381–95669 | 0 | 0–0 | 95409 | 95050–95768 | 0 | — |
| Output tokens | 25287.5 | 22180–28395 | 22518.75 | 21525–23606 | 0 | 0–0 | 22683.5 | 21699–23668 | 0 | — |
| Provider-cached input tokens | 59984 | 35120–84848 | 73612 | 27200–92032 | 0 | 0–0 | 86560 | 83872–89248 | 0 | — |
| Tokens avoided by app cache | 0 | 0–0 | 0 | 0–0 | 114101 | 114101–114101 | 0 | 0–0 | 117467 | — |
| Zero-claim calls | 32.5 | 29–36 | 29.5 | 29–30 | 30 | 30–30 | 31.5 | 30–33 | 33 | — |
| Usable (VALID) claims | 32.5 | 31–34 | 31.25 | 28–34 | 34 | 34–34 | 28.5 | 28–29 | 28 | — |
| Fields with a usable claim (of 32) | 18 | 17–19 | 16.5 | 16–17 | 17 | 17–17 | 15.5 | 15–16 | 15 | — |
| Fields resolved automatically | 2.5 | 2–3 | 1.25 | 1–2 | 1 | 1–1 | 1 | 1–1 | 1 | — |
| Wall time (s) | 147.5 | 109–186 | 144 | 110–217 | 9 | 9–9 | 103.5 | 101–106 | 9 | — |
| PROPOSED accuracy (strict) | 0.243 | 0.2–0.2857 | 0.361 | 0.2381–0.5 | 0.308 | 0.3077–0.3077 | 0.414 | 0.4–0.4286 | 0.4 | — |
| PROPOSED coverage | 0.366 | 0.3571–0.375 | 0.246 | 0.1071–0.375 | 0.232 | 0.2321–0.2321 | 0.259 | 0.25–0.2679 | 0.268 | — |
| AUTO accuracy (strict) | 0.583 | 0.5–0.6667 | 0.667 | 0.5–1 | 0.5 | 0.5–0.5 | 1.0 | 1–1 | 1.0 | — |
| AUTO coverage | 0.062 | 0.0536–0.0714 | 0.036 | 0.0179–0.0536 | 0.036 | 0.0357–0.0357 | 0.045 | 0.0357–0.0536 | 0.036 | — |
| PROPOSED source recall | 0.5 | 0.5–0.5 | 0.312 | 0–0.5 | 0.25 | 0.25–0.25 | 0.375 | 0.25–0.5 | 0.25 | — |
| PROPOSED source precision | 0.292 | 0.25–0.3333 | 0.219 | 0–0.4 | 0.143 | 0.1429–0.1429 | 0.226 | 0.1667–0.2857 | 0.167 | — |
| Tokens per correct PROPOSED answer | 25808.5 | 22233–29384 | 26947.5 | 19879–36635 | 0 | 0–0 | 19682 | 19578–19786 | 0 | — |
| Tokens per correct AUTO answer | 62734 | 58768–66700 | 99349.5 | 59638–114101 | 0 | 0–0 | 49153.5 | 39573–58734 | 0 | — |

- optimized #1 `ef02c039`: water_source Gikigie River, Irati River, Maragua River, Nairobi Aquifer, Nairobi River, Tana River, Thika River; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- baseline #1 `54158a05`: water_source Nairobi River, Ruiru River, Tana River, Thika River, groundwater, groundwater (local wells); skipped {'NO_SUFFICIENT_EVIDENCE': 12}; identity AUTO {'city_name': 'MATCH', 'country': 'MATCH'}
- optimized #2 `96af5b29`: water_source Chania River, Gikigie River, Irati River, Nairobi Aquifer, Thika River, groundwater; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- optimized_cache #1 `38e43510`: water_source Gikigie River, Irati River, Maragua River, Nairobi Aquifer, Nairobi River, Tana River, Thika River; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- optimized #1 `97068451`: water_source Chania River, Kiama River, Nairobi River, Nairobi groundwater basin, Thika River; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- baseline #2 `9c36ed5a`: water_source Gikigie River, Irati River, Maragua River, Nairobi Aquifer, Nairobi groundwater basin, Ruiru River, Tana River, Thika River; skipped {'NO_SUFFICIENT_EVIDENCE': 12}; identity AUTO {'city_name': 'MATCH', 'country': 'MISSING_PREDICTION'}
- optimized #2 `4f804da4`: water_source Nairobi Aquifer, Nairobi groundwater basin, Spring No.1; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- optimized_cache #1 `64bf1d61`: water_source Gikigie River, Irati River, Maragua River, Nairobi Aquifer, Nairobi River, Tana River, Thika River; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- optimized_v2 #1 `e497011d`: water_source Gikigie River, Irati River, Maragua River, Nairobi Aquifer, Nairobi River, Thika River; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- optimized_v2 #2 `649d613f`: water_source Maragua, Nairobi Aquifer, Ruiru River, Sagana-Gura, Thika River, Thika-Chania, groundwater; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}
- optimized_v2_cache #1 `8dc29be8`: water_source Gikigie River, Irati River, Maragua River, Nairobi Aquifer, Nairobi River, Thika River; skipped {'NOT_NEEDED': 5, 'NO_SUFFICIENT_EVIDENCE': 11}; identity AUTO {'city_name': 'MISSING_PREDICTION', 'country': 'MISSING_PREDICTION'}

## Santiago

| metric | baseline (mean) | baseline range | optimized (mean) | optimized range | optimized_cache (mean) | optimized_cache range | optimized_v2 (mean) | optimized_v2 range | optimized_v2_cache (mean) | optimized_v2_cache range |
|---|---|---|---|---|---|---|---|---|---|---|
| Model calls | 50 | 49–51 | 31.5 | 31–32 | 0 | 0–0 | 32.5 | 32–33 | 0 | — |
| App-cache hits | 0 | 0–0 | 0 | 0–0 | 31 | 31–31 | 0 | 0–0 | 33 | — |
| Input tokens | 63427 | 61838–65016 | 44529.5 | 43819–45240 | 0 | 0–0 | 46976 | 45884–48068 | 0 | — |
| Output tokens | 12408.5 | 11802–13015 | 8318.25 | 7897–8888 | 0 | 0–0 | 8958.5 | 8446–9471 | 0 | — |
| Provider-cached input tokens | 46352 | 34272–58432 | 37336 | 16432–44992 | 0 | 0–0 | 42920 | 40256–45584 | 0 | — |
| Tokens avoided by app cache | 0 | 0–0 | 0 | 0–0 | 52707 | 52707–52707 | 0 | 0–0 | 56514 | — |
| Zero-claim calls | 42 | 40–44 | 26.75 | 25–28 | 26 | 26–26 | 27 | 26–28 | 28 | — |
| Usable (VALID) claims | 9.5 | 7–12 | 5 | 4–6 | 6 | 6–6 | 5.5 | 5–6 | 5 | — |
| Fields with a usable claim (of 32) | 7 | 6–8 | 4.75 | 4–6 | 5 | 5–5 | 5 | 5–5 | 5 | — |
| Fields resolved automatically | 1 | 1–1 | 0.25 | 0–1 | 0 | 0–0 | 0 | 0–0 | 0 | — |
| Wall time (s) | 98.5 | 92–105 | 70.75 | 70–71 | 7.5 | 7–8 | 70.5 | 70–71 | 8 | — |

- optimized #1 `06a014a3`: water_source —; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 16, 'NO_SUFFICIENT_EVIDENCE': 27}
- baseline #1 `8402d4ac`: water_source groundwater; skipped {'NO_SUFFICIENT_EVIDENCE': 32}
- optimized #2 `1cc75593`: water_source groundwater; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 17, 'NO_SUFFICIENT_EVIDENCE': 27}
- optimized #1 `ee5636fd`: water_source groundwater wells; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 17, 'NO_SUFFICIENT_EVIDENCE': 27}
- optimized_cache #1 `4577a905`: water_source —; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 16, 'NO_SUFFICIENT_EVIDENCE': 27}
- baseline #2 `3af051c7`: water_source aquifers, groundwater, lakes, rivers, sea; skipped {'NO_SUFFICIENT_EVIDENCE': 32}
- optimized #2 `3a547b14`: water_source groundwater; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 16, 'NO_SUFFICIENT_EVIDENCE': 27}
- optimized_cache #1 `2f16e617`: water_source —; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 16, 'NO_SUFFICIENT_EVIDENCE': 27}
- optimized_v2 #1 `4ac90c69`: water_source groundwater wells; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 18, 'NO_SUFFICIENT_EVIDENCE': 25}
- optimized_v2 #2 `5a465c77`: water_source groundwater (local wells); skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 17, 'NO_SUFFICIENT_EVIDENCE': 27}
- optimized_v2_cache #1 `b107cf5e`: water_source groundwater wells; skipped {'NOT_NEEDED': 5, 'NO_RELEVANT_EVIDENCE': 18, 'NO_SUFFICIENT_EVIDENCE': 25}
