# Evaluator vs manual findings: evaluation ff3dbb5f-ee7e-4699-86e4-18c3026a875d (as evaluated) of research run 8ad1fa90-cff6-4572-864d-a9997abea332

AGREE: 30, DISAGREE: 2

| verdict | field | stage | attribute | manual | evaluator | note / detail |
|---|---|---|---|---|---|---|
| DISAGREE | total_city_water_withdrawals_m3_day | RAW | root_cause | ['DISCOVERY_FAILURE'] | ['EVIDENCE_UNAVAILABLE'] | 1.42M is an estimated 2010 academic figure; the workbook's 2024 Summary Report was never acquired (and the acquired daily-consumption page lacked the 2024 figure) UNKNOWN: no acquired chunk contains the reference value; some of the reference row's sources reached the run, but the reference doesn't s |
| DISAGREE | infrastructure_notes | RAW | status | ['PARTIAL_MATCH', 'NEEDS_REVIEW'] | ['MISMATCH'] | a long paragraph covering similar items to the workbook's list |
| AGREE | city_name | AUTO | status | ['MATCH'] | ['MATCH'] |  |
| AGREE | utility_name | AUTO | status | ['MATCH'] | ['MATCH'] |  |
| AGREE | water_source | AUTO | status | ['MATCH'] | ['MATCH'] |  |
| AGREE | water_type | AUTO | status | ['MATCH'] | ['MATCH'] |  |
| AGREE | jurisdiction | RAW | status | ['MATCH'] | ['MATCH'] | Ontario is right; blocked because only a technical source cites it |
| AGREE | jurisdiction | AUTO | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | source_withdrawal_contribution_method | RAW | status | ['MATCH'] | ['MATCH'] | Reported Daily, matches; not stated in the quote |
| AGREE | city_population_millions | RAW | status | ['SCOPE_MISMATCH', 'MISMATCH'] | ['SCOPE_MISMATCH'] | 3.6M is 'Toronto and portions of York and Peel' (wider area), hedged |
| AGREE | city_population_millions | RAW | symptoms | ['GEOGRAPHIC_SCOPE_MISMATCH'] | ['GEOGRAPHIC_SCOPE_MISMATCH', 'MEASUREMENT_DEFINITION_MISMATCH'] | 3.6M is 'Toronto and portions of York and Peel' (wider area), hedged |
| AGREE | city_population_millions | AUTO | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | utility_reported_city_population_millions | RAW | status | ['SCOPE_MISMATCH', 'MISMATCH'] | ['SCOPE_MISMATCH'] |  |
| AGREE | utility_reported_city_population_millions | RAW | symptoms | ['GEOGRAPHIC_SCOPE_MISMATCH'] | ['GEOGRAPHIC_SCOPE_MISMATCH', 'MEASUREMENT_DEFINITION_MISMATCH'] |  |
| AGREE | total_city_water_withdrawals_m3_day | RAW | status | ['TEMPORAL_MISMATCH', 'SCOPE_MISMATCH', 'MISMATCH'] | ['TEMPORAL_MISMATCH'] | 1.42M is an estimated 2010 academic figure; the workbook's 2024 Summary Report was never acquired (and the acquired daily-consumption page lacked the 2024 figure) |
| AGREE | total_city_water_withdrawals_m3_day | RAW | symptoms | ['TEMPORAL_MISMATCH'] | ['MEASUREMENT_DEFINITION_MISMATCH', 'TEMPORAL_MISMATCH'] | 1.42M is an estimated 2010 academic figure; the workbook's 2024 Summary Report was never acquired (and the acquired daily-consumption page lacked the 2024 figure) |
| AGREE | total_city_water_withdrawals_m3_day | AUTO | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | water_system | RAW | status | ['NOT_COMPARABLE'] | ['NOT_COMPARABLE'] | the workbook holds the placeholder 'HYBAS ID' |
| AGREE | water_transfer_system | RAW | status | ['MISMATCH'] | ['MISMATCH'] | Coxwell Bypass Tunnel is a sewer tunnel; the workbook says N/A |
| AGREE | water_transfer_system | RAW | symptoms | ['FIELD_SEMANTICS_MISMATCH'] | ['FIELD_SEMANTICS_MISMATCH'] | Coxwell Bypass Tunnel is a sewer tunnel; the workbook says N/A |
| AGREE | seasonal_uses | RAW | status | ['MISMATCH', 'NEEDS_REVIEW'] | ['NEEDS_REVIEW'] | a taste/odour sentence, not a seasonal use |
| AGREE | seasonal_uses | RAW | symptoms | ['FIELD_SEMANTICS_MISMATCH'] | ['FIELD_SEMANTICS_MISMATCH'] | a taste/odour sentence, not a seasonal use |
| AGREE | water_quality_notes | RAW | status | ['MISMATCH', 'NEEDS_REVIEW'] | ['NEEDS_REVIEW'] | treated-water testing text; the rules want raw-water quality |
| AGREE | water_quality_notes | RAW | symptoms | ['FIELD_SEMANTICS_MISMATCH'] | ['FIELD_SEMANTICS_MISMATCH'] | treated-water testing text; the rules want raw-water quality |
| AGREE | water_quality_year | RAW | status | ['MISMATCH'] | ['MISMATCH'] | 2025 vs 2024 |
| AGREE | city_latitude | RAW | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | city_longitude | RAW | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | pod_latitude | RAW | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | pod_longitude | RAW | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | pod_coordinate_method | RAW | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | city_population_reference_year | RAW | status | ['MISSING_PREDICTION'] | ['MISSING_PREDICTION'] |  |
| AGREE | (run) | RUN | symptom | SOURCE_CLASSIFICATION_ERROR (toronto.name) | 1 | a third-party history site labelled GOVERNMENT |
