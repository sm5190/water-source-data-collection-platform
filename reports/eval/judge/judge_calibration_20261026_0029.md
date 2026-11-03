# Semantic judge calibration (2026-10-26)

- Cases: `tests\evaluation\judge_cases_toronto.yaml` (13; 0 confirmed by a person)
- Judge: vt / gpt-oss-120b, prompt judge-2
- Agreement on DRAFT (unconfirmed) labels: 13/13 (100%)
- False equivalence (expected MISMATCH, judged MATCH): none
- Hard-rule cases sent to the judge (must be none): none
- Result: **NOT VALIDATED** (labels are not all confirmed by a person)

| case | field | expected | judged | agree | confirmed | justification |
|---|---|---|---|---|---|---|
| toronto-utility-same | utility_name | MATCH | MATCH | yes | no | Both manual and automated values state the utility as Toronto Water, and the quote confirms this is the city’s water utility. |
| toronto-utility-division-name | utility_name | MATCH | MATCH | yes | no | Toronto Water is identified as the City of Toronto division providing drinking water, matching the manual's 'City of Toronto Water Division' as the same organiz |
| toronto-source-word-order | water_source | MATCH | MATCH | yes | no | Both values name the same natural lake; word order difference is allowed by the definition, and the quote confirms the source as Lake Ontario. |
| toronto-source-different-lake | water_source | MISMATCH | MISMATCH | yes | no | The manual states the source is Lake Ontario, while the automated output cites Lake Simcoe, which are distinct water bodies, thus they do not match. |
| toronto-jurisdiction-long-form | jurisdiction | MATCH | MATCH | yes | no | Both values identify the same province where the city is located; 'Province of' is just an extra descriptor, not a different meaning. |
| toronto-infrastructure-partial | infrastructure_notes | PARTIAL_MATCH | PARTIAL_MATCH | yes | no | Both values refer to drinking-water conveyance infrastructure for the city. They share intake and treatment elements, but the automated text omits several infra |
| toronto-water-quality-treated-vs-raw | water_quality_notes | MISMATCH | MISMATCH | yes | no | Manual notes describe raw source water characteristics, while Automated notes describe treated water quality and compliance, which are different types of inform |
| toronto-seasonal-taste-not-use | seasonal_uses | MISMATCH | MISMATCH | yes | no | The manual value refers to seasonal peak demand (usage), while the automated value reports a seasonal water‑quality/taste event, which the definition excludes.  |
| contrast-transfer-sewer-tunnel | water_transfer_system | MISMATCH | MISMATCH | yes | no | The manual value names an imported drinking‑water aqueduct, which matches the field definition. The automated value describes a sewer overflow/stormwater tunnel |
| contrast-infrastructure-wastewater | infrastructure_notes | MISMATCH | MISMATCH | yes | no | Manual lists drinking‑water conveyance (intake, pipe, treatment plant). Automated lists wastewater/sewer infrastructure, which the definition excludes. Thus the |
| contrast-raw-quality-paraphrase | water_quality_notes | MATCH | MATCH | yes | no | Both values describe raw water quality, noting low turbidity (clear water) and influence from agricultural runoff, with no contradictory or extra information. |
| contrast-infrastructure-subset | infrastructure_notes | PARTIAL_MATCH | PARTIAL_MATCH | yes | no | Both values list drinking-water conveyance elements and match on the lake intake and treatment plant. The manual includes a pumping station which the automated  |
| contrast-country-different | country | MISMATCH | MISMATCH | yes | no | The manual value lists Canada as the country, while the automated value lists United States of America. They refer to different countries, so the values are dif |

Confusion (rows: expected, columns: judged)

| expected \ judged | MATCH | PARTIAL_MATCH | MISMATCH | NEEDS_REVIEW | ERROR |
|---|---|---|---|---|---|
| MATCH | 5 | 0 | 0 | 0 | 0 |
| PARTIAL_MATCH | 0 | 2 | 0 | 0 | 0 |
| MISMATCH | 0 | 0 | 6 | 0 | 0 |
| NEEDS_REVIEW | 0 | 0 | 0 | 0 | 0 |