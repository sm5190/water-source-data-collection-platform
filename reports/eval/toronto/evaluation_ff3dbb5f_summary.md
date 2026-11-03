# Evaluation ff3dbb5f-ee7e-4699-86e4-18c3026a875d

- Research run: `8ad1fa90-cff6-4572-864d-a9997abea332` (history from `0618af70-5e17-4719-b040-22509451d523`)
- City: toronto|canada
- Reference dataset: `a7f6cbdea3f4273cc7166229`
- Stages: RAW, AUTO
- Comparison policy: compare-1; snapshot `28329a2b8abfd326`; evaluated 2026-10-19T14:30:07.244520+00:00
- Metrics basis: **as evaluated** (0 field result(s) and 0 diagnosis(es) adjudicated)

## Metrics (numerator/denominator)

| stage | metric | value | note |
|---|---|---|---|
| RAW | coverage | 14/27 (52%) |  |
| RAW | accuracy | 6/14 (43%) |  |
| RAW | top_candidate_accuracy | 6/14 (43%) |  |
| RAW | missed | 13/27 (48%) |  |
| RAW | evidence_adjusted_recall | 6/8 (75%) |  |
| RAW | off_field_claim_rate | 3/15 (20%) |  |
| AUTO | coverage | 4/27 (15%) |  |
| AUTO | precision | 4/4 (100%) | at coverage 4/27 |
| AUTO | wrong_acceptance_rate | 0/4 (0%) |  |
| AUTO | escalation_rate | 10/27 (37%) |  |
| AUTO | unnecessary_escalation_rate | 2/10 (20%) |  |

Excluded from denominators: MISSING_REFERENCE 1, NOT_COMPARABLE 1

## Field comparison

| field | record | reference | RAW prediction | RAW status | AUTO prediction | AUTO status |
|---|---|---|---|---|---|---|
| city_name |  | Toronto | Toronto [MATCH] | MATCH | Toronto | MATCH |
| jurisdiction |  | Ontario | Ontario [MATCH] | MATCH | escalated | MISSING_PREDICTION |
| country |  | Canada |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| city_latitude |  | 43.5632 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| city_longitude |  | -79.3832 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| city_population_millions |  | 3.273 (2024) | 3.6 [SCOPE_MISMATCH] | SCOPE_MISMATCH | escalated | MISSING_PREDICTION |
| city_population_reference_year |  | 2024 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| utility_reported_city_population_millions |  | 3.273 (2025) | 3.6 [SCOPE_MISMATCH] | SCOPE_MISMATCH | escalated | MISSING_PREDICTION |
| utility_reported_city_population_reference_year |  | 2025 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| population_served_millions | lake ontario | 3.273 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| utility_name | lake ontario | Toronto Water | Toronto Water [MATCH] | MATCH | Toronto Water | MATCH |
| water_source | lake ontario | Lake Ontario | Lake Ontario [MATCH] | MATCH | Lake Ontario | MATCH |
| water_system | lake ontario | PLACEHOLDER (HYBAS ID) | Toronto’s water distribution system is divided into six pressure zones and subdivided into 13 pressure districts [NOT_COMPARABLE] | NOT_COMPARABLE | escalated | NOT_COMPARABLE |
| water_type | lake ontario | Surface | Surface [MATCH] | MATCH | Surface | MATCH |
| water_transfer_system | lake ontario | NOT_APPLICABLE (N/A) | Coxwell Bypass Tunnel [MISMATCH] | MISMATCH | escalated | MISSING_PREDICTION |
| seasonal_uses | lake ontario | Peak Demand Periods | Earthy or musty smell or taste can occur in late summer or early fall and can be caused by naturally occurring algae and high water temperatures in Lake Ontario | NEEDS_REVIEW | escalated | MISSING_PREDICTION |
| reservoir_names | lake ontario | NOT_APPLICABLE (N/A) |  | MATCH | no decision | MISSING_PREDICTION |
| pod_latitude | lake ontario | 43.6678 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| pod_longitude | lake ontario | -79.274 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| pod_coordinate_method | lake ontario | Visual Estimation |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| infrastructure_notes | lake ontario | lake intake; intake pipe; treatment plant; pumping station; storage reservoir; elevated storage tank; trunk watermain; distribution main | toronto water manages a comprehensive water‑supply infrastructure that includes intake pipes located 1‑5 km offshore in lake ontario, screens to remove large de | MISMATCH | escalated | MISSING_PREDICTION |
| total_city_water_withdrawals_m3_day |  | 1159745 (2024) | 1420000 [TEMPORAL_MISMATCH] | TEMPORAL_MISMATCH | escalated | MISSING_PREDICTION |
| total_city_water_withdrawals_year |  | 2024 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| past_total_city_water_withdrawals |  | UNKNOWN (Unknown) |  | MISSING_REFERENCE | no decision | MISSING_REFERENCE |
| source_withdrawal_contribution_m3_day | lake ontario | 1159745 (2024) |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| source_withdrawal_contribution_year | lake ontario | 2024 |  | MISSING_PREDICTION | no decision | MISSING_PREDICTION |
| source_withdrawal_contribution_method | lake ontario | Reported Daily | Reported Daily [MATCH] | MATCH | escalated | MISSING_PREDICTION |
| water_quality_notes | lake ontario | freshwater lake source, offshore intakes, seasonal taste and odor risk, turbidity monitoring (2024) | Toronto Water conducts laboratory testing for water quality standards and continuous monitoring, including bacteriological testing up to 6,000 times a year, ove | NEEDS_REVIEW | escalated | MISSING_PREDICTION |
| water_quality_year | lake ontario | 2024 | 2025 [MISMATCH] | MISMATCH | escalated | MISSING_PREDICTION |

`*` = adjudicated.

## Failures (earliest demonstrable cause)

EVIDENCE_UNAVAILABLE: 8, UNKNOWN: 6, DISCOVERY_FAILURE: 4, UNNECESSARY_ESCALATION: 2, EXTRACTION_FAILURE: 2

| stage | field_key | record_key | root_cause | confidence | symptoms | explanation |
|---|---|---|---|---|---|---|
| AUTO | jurisdiction |  | UNNECESSARY_ESCALATION | CONFIRMED |  | a candidate matching the reference existed but the rules sent the field to review (not a failure; a coverage cost) |
| RAW | country |  | EXTRACTION_FAILURE | PROBABLE |  | the value was in the evidence the model saw for this field, but no claim carried it |
| RAW | city_latitude |  | EVIDENCE_UNAVAILABLE | UNKNOWN |  | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | city_longitude |  | EVIDENCE_UNAVAILABLE | UNKNOWN |  | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | city_population_millions |  | EVIDENCE_UNAVAILABLE | UNKNOWN | GEOGRAPHIC_SCOPE_MISMATCH, MEASUREMENT_DEFINITION_MISMATCH | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | city_population_reference_year |  | UNKNOWN | UNKNOWN |  | years can't be traced to evidence deterministically |
| RAW | utility_reported_city_population_millions |  | EVIDENCE_UNAVAILABLE | UNKNOWN | GEOGRAPHIC_SCOPE_MISMATCH, MEASUREMENT_DEFINITION_MISMATCH | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | utility_reported_city_population_reference_year |  | UNKNOWN | UNKNOWN |  | years can't be traced to evidence deterministically |
| RAW | population_served_millions | lake ontario | EVIDENCE_UNAVAILABLE | UNKNOWN |  | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | water_transfer_system | lake ontario | EVIDENCE_UNAVAILABLE | UNKNOWN | FIELD_SEMANTICS_MISMATCH | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | seasonal_uses | lake ontario | UNKNOWN | UNKNOWN | FIELD_SEMANTICS_MISMATCH | the reference's source https://www.toronto.ca/services-payments/water-environment/tap-water-in-toronto/ was acquired; its text doesn't contain the reference wor |
| RAW | pod_latitude | lake ontario | DISCOVERY_FAILURE | PROBABLE |  | none of the reference's sources is among the retained discovery candidates (raw search results are not stored, so 'never found' and 'found but ranked out' can't |
| RAW | pod_longitude | lake ontario | DISCOVERY_FAILURE | PROBABLE |  | none of the reference's sources is among the retained discovery candidates (raw search results are not stored, so 'never found' and 'found but ranked out' can't |
| RAW | pod_coordinate_method | lake ontario | EVIDENCE_UNAVAILABLE | UNKNOWN |  | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | infrastructure_notes | lake ontario | EXTRACTION_FAILURE | PROBABLE |  | the value was in the evidence the model saw for this field, but no claim carried it |
| RAW | total_city_water_withdrawals_m3_day |  | EVIDENCE_UNAVAILABLE | UNKNOWN | MEASUREMENT_DEFINITION_MISMATCH, TEMPORAL_MISMATCH | no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't say which one gave this field — n |
| RAW | total_city_water_withdrawals_year |  | UNKNOWN | UNKNOWN |  | years can't be traced to evidence deterministically |
| RAW | source_withdrawal_contribution_m3_day | lake ontario | DISCOVERY_FAILURE | PROBABLE |  | none of the reference's sources is among the retained discovery candidates (raw search results are not stored, so 'never found' and 'found but ranked out' can't |
| RAW | source_withdrawal_contribution_year | lake ontario | UNKNOWN | UNKNOWN |  | years can't be traced to evidence deterministically |
| AUTO | source_withdrawal_contribution_method | lake ontario | UNNECESSARY_ESCALATION | CONFIRMED |  | a candidate matching the reference existed but the rules sent the field to review (not a failure; a coverage cost) |
| RAW | water_quality_notes | lake ontario | DISCOVERY_FAILURE | PROBABLE | FIELD_SEMANTICS_MISMATCH | none of the reference's sources is among the retained discovery candidates (raw search results are not stored, so 'never found' and 'found but ranked out' can't |
| RAW | water_quality_year | lake ontario | UNKNOWN | UNKNOWN |  | years can't be traced to evidence deterministically |
| RUN |  |  | UNKNOWN | UNKNOWN | SOURCE_CLASSIFICATION_ERROR | suspected source-type misclassification: toronto.name labelled GOVERNMENT but its .name domain is not typically official |

## Evidence checks

| check | pass | results |
|---|---|---|
| AUTHORITY | 17/21 (81%) | {"PASS": 17, "WARN": 4} |
| CITATION_LINKED | 21/21 (100%) | {"PASS": 21} |
| CITATION_PRESENT | 15/15 (100%) | {"PASS": 15} |
| DOCUMENT_ACQUIRED | 21/21 (100%) | {"PASS": 21} |
| METADATA | 21/21 (100%) | {"PASS": 21} |
| QUOTE_VERIFIED | 21/21 (100%) | {"PASS": 21} |
| SEMANTIC_SUPPORT | 12/15 (80%) | {"PASS": 12, "NEEDS_REVIEW": 3} |

## Adjudications

_none_

## Instrumentation gaps

Conclusions that depend on these are PROBABLE or UNKNOWN, never CONFIRMED.

- **RAW_SEARCH_RESULTS**: only retained, ranked discovery candidates are stored; 'not found by search' vs 'ranked out' can't be told apart
- **BUNDLE_PER_ATTEMPT**: only the last context bundle per field is linked to the run; earlier attempts are matched by city and time window
- **LLM_RAW_OUTPUT**: raw model responses and rejected citations/claims are not persisted (only counts in tracing spans)
- **SOURCE_TYPE_REASON**: why a document got its source type is not recorded
- **CHUNK_SCORES**: scores of chunks not selected into a bundle are not stored (only counts)
- **DYNAMIC_CONTENT**: acquisition doesn't record whether a page had script-rendered data that static fetching missed
