# Extraction token audit

One slot = one field in one extraction round. *Productive* = produced at least one VALID claim. Provider-cached tokens are part of input tokens (billed by the provider's prompt cache); app-cache hits made no model request. Unknown = not reported, never 0.

## Santiago — 0d7d4af8 (RESEARCH, AWAITING_REVIEW)

Events from job result; ledger rows 49.

- Calls 49 (model 49, app-cache hits 0), zero-claim 42, productive 7
- Input 62,476 (provider-cached 29,248), output 12,386; avoided by app cache 0
- Claims 7 (VALID 7, selected 1); repeated identical contexts Unknown

### By round

| round | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16 | 10 | 6 | 21,691 | 5,420 | 7,520 | 0 | 0 | 6 | NO_SUFFICIENT_EVIDENCE 14 |
| 2 | 20 | 19 | 1 | 23,232 | 3,811 | 12,896 | 0 | 0 | 1 | NO_SUFFICIENT_EVIDENCE 6 |
| 3 | 13 | 13 | 0 | 17,553 | 3,155 | 8,832 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 12 |

### Initial vs re-extraction

| stage | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| initial | 45 | 38 | 7 | 54,892 | 11,367 | 26,416 | 0 | 0 | 7 | NO_SUFFICIENT_EVIDENCE 32 |
| reextraction | 4 | 4 | 0 | 7,584 | 1,019 | 2,832 | 0 | 0 | 0 | — |

### By field

| field | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| city_latitude | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 3 |
| city_longitude | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 3 |
| city_name | 1 | 0 | 1 | 925 | 323 | 432 | 0 | 0 | 1 | — |
| city_population_millions | 3 | 3 | 0 | 3,116 | 380 | 1,296 | 0 | 0 | 0 | — |
| city_population_reference_year | 2 | 2 | 0 | 2,163 | 275 | 944 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| country | 1 | 0 | 1 | 1,073 | 327 | 432 | 0 | 0 | 1 | — |
| infrastructure_notes | 1 | 0 | 1 | 1,663 | 542 | 64 | 0 | 0 | 1 | — |
| jurisdiction | 2 | 1 | 1 | 3,165 | 743 | 1,264 | 0 | 0 | 1 | — |
| past_total_city_water_withdrawals | 2 | 2 | 0 | 2,216 | 322 | 1,440 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_coordinate_method | 1 | 1 | 0 | 919 | 94 | 496 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| pod_latitude | 1 | 1 | 0 | 851 | 132 | 496 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| pod_longitude | 1 | 1 | 0 | 851 | 102 | 496 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| population_served_millions | 1 | 1 | 0 | 916 | 169 | 720 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| references | 2 | 2 | 0 | 2,302 | 1,409 | 1,440 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| reservoir_names | 3 | 3 | 0 | 4,719 | 687 | 2,000 | 0 | 0 | 0 | — |
| seasonal_uses | 1 | 1 | 0 | 968 | 500 | 720 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| source_withdrawal_contribution_m3_day | 3 | 3 | 0 | 3,903 | 658 | 1,936 | 0 | 0 | 0 | — |
| source_withdrawal_contribution_method | 1 | 1 | 0 | 954 | 127 | 496 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| source_withdrawal_contribution_year | 2 | 2 | 0 | 2,128 | 227 | 1,216 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| total_city_water_withdrawals_m3_day | 3 | 3 | 0 | 3,419 | 372 | 1,968 | 0 | 0 | 0 | — |
| total_city_water_withdrawals_year | 2 | 2 | 0 | 2,130 | 215 | 1,216 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| utility_name | 3 | 2 | 1 | 4,582 | 1,276 | 1,888 | 0 | 0 | 1 | — |
| utility_reported_city_population_millions | 1 | 1 | 0 | 1,023 | 135 | 720 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| utility_reported_city_population_reference_year | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 3 |
| water_quality_notes | 3 | 3 | 0 | 4,522 | 486 | 1,872 | 0 | 0 | 0 | — |
| water_quality_year | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 3 |
| water_source | 3 | 2 | 1 | 6,391 | 1,134 | 1,872 | 0 | 0 | 1 | — |
| water_system | 2 | 2 | 0 | 2,048 | 504 | 1,216 | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| water_transfer_system | 3 | 3 | 0 | 3,916 | 850 | 2,176 | 0 | 0 | 0 | — |
| water_type | 1 | 0 | 1 | 1,613 | 397 | 432 | 0 | 0 | 1 | — |

### Calls that returned no claims

| round | field | stage | input tok | output tok |
|---|---|---|---|---|
| 1 | city_population_millions | initial | 974 | 74 |
| 1 | city_population_reference_year | initial | 1,060 | 120 |
| 1 | jurisdiction | initial | 1,881 | 366 |
| 1 | reservoir_names | initial | 1,661 | 263 |
| 1 | seasonal_uses | initial | 968 | 500 |
| 1 | source_withdrawal_contribution_m3_day | initial | 1,497 | 293 |
| 1 | total_city_water_withdrawals_m3_day | initial | 1,217 | 99 |
| 1 | water_quality_notes | initial | 1,073 | 104 |
| 1 | water_system | initial | 1,076 | 209 |
| 1 | water_transfer_system | initial | 1,621 | 412 |
| 2 | city_population_millions | initial | 1,022 | 108 |
| 2 | past_total_city_water_withdrawals | initial | 1,037 | 120 |
| 2 | pod_coordinate_method | initial | 919 | 94 |
| 2 | pod_latitude | initial | 851 | 132 |
| 2 | pod_longitude | initial | 851 | 102 |
| 2 | population_served_millions | initial | 916 | 169 |
| 2 | references | initial | 975 | 518 |
| 2 | reservoir_names | initial | 1,430 | 165 |
| 2 | source_withdrawal_contribution_m3_day | initial | 1,242 | 196 |
| 2 | source_withdrawal_contribution_method | initial | 954 | 127 |
| 2 | source_withdrawal_contribution_year | initial | 993 | 75 |
| 2 | total_city_water_withdrawals_m3_day | initial | 1,030 | 109 |
| 2 | total_city_water_withdrawals_year | initial | 994 | 116 |
| 2 | utility_name | reextraction | 1,804 | 244 |
| 2 | utility_reported_city_population_millions | initial | 1,023 | 135 |
| 2 | water_quality_notes | initial | 1,708 | 115 |
| 2 | water_source | reextraction | 2,073 | 454 |
| 2 | water_system | initial | 972 | 295 |
| 2 | water_transfer_system | initial | 1,154 | 160 |
| 3 | city_population_millions | initial | 1,120 | 198 |
| 3 | city_population_reference_year | initial | 1,103 | 155 |
| 3 | past_total_city_water_withdrawals | initial | 1,179 | 202 |
| 3 | references | initial | 1,327 | 891 |
| 3 | reservoir_names | initial | 1,628 | 259 |
| 3 | source_withdrawal_contribution_m3_day | initial | 1,164 | 169 |
| 3 | source_withdrawal_contribution_year | initial | 1,135 | 152 |
| 3 | total_city_water_withdrawals_m3_day | initial | 1,172 | 164 |
| 3 | total_city_water_withdrawals_year | initial | 1,136 | 99 |
| 3 | utility_name | reextraction | 1,150 | 110 |
| 3 | water_quality_notes | initial | 1,741 | 267 |
| 3 | water_source | reextraction | 2,557 | 211 |
| 3 | water_transfer_system | initial | 1,141 | 278 |

## Nairobi — 293e896f (REPLAY, SUCCEEDED)

Events from run_events; ledger rows 0 (not all matched to slots: event token counts used there).

- Calls 57 (model 57, app-cache hits 0), zero-claim 35, productive 22
- Input 107,525 (provider-cached Unknown), output 26,188; avoided by app cache 0
- Claims 35 (VALID 35, selected 9); repeated identical contexts Unknown

### By round

| round | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 20 | 11 | 9 | 28,337 | 8,371 | Unknown | 0 | 0 | 12 | NO_SUFFICIENT_EVIDENCE 10 |
| 2 | 23 | 14 | 9 | 44,622 | 10,163 | Unknown | 0 | 0 | 13 | — |
| 3 | 14 | 10 | 4 | 34,566 | 7,654 | Unknown | 0 | 0 | 10 | NO_SUFFICIENT_EVIDENCE 2 |

### Initial vs re-extraction

| stage | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| initial | 53 | 35 | 18 | 90,897 | 20,710 | Unknown | 0 | 0 | 23 | NO_SUFFICIENT_EVIDENCE 12 |
| reextraction | 4 | 0 | 4 | 16,628 | 5,478 | Unknown | 0 | 0 | 12 | — |

### By field

| field | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| city_latitude | 1 | 1 | 0 | 921 | 133 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| city_longitude | 1 | 1 | 0 | 921 | 98 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| city_name | 1 | 0 | 1 | 1,287 | 373 | Unknown | 0 | 0 | 1 | — |
| city_population_millions | 3 | 3 | 0 | 5,029 | 668 | Unknown | 0 | 0 | 0 | — |
| city_population_reference_year | 2 | 1 | 1 | 3,082 | 524 | Unknown | 0 | 0 | 1 | — |
| country | 1 | 0 | 1 | 1,648 | 387 | Unknown | 0 | 0 | 1 | NO_SUFFICIENT_EVIDENCE 1 |
| infrastructure_notes | 2 | 1 | 1 | 2,497 | 773 | Unknown | 0 | 0 | 1 | — |
| jurisdiction | 2 | 1 | 1 | 4,019 | 533 | Unknown | 0 | 0 | 1 | — |
| past_total_city_water_withdrawals | 3 | 3 | 0 | 5,569 | 915 | Unknown | 0 | 0 | 0 | — |
| pod_coordinate_method | 2 | 2 | 0 | 2,863 | 232 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_latitude | 2 | 2 | 0 | 2,561 | 188 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_longitude | 2 | 2 | 0 | 2,561 | 335 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| population_served_millions | 3 | 3 | 0 | 4,089 | 404 | Unknown | 0 | 0 | 0 | — |
| references | 3 | 3 | 0 | 4,824 | 2,939 | Unknown | 0 | 0 | 0 | — |
| reservoir_names | 3 | 0 | 3 | 7,432 | 2,870 | Unknown | 0 | 0 | 7 | — |
| seasonal_uses | 1 | 0 | 1 | 1,743 | 575 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_m3_day | 1 | 0 | 1 | 1,518 | 503 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_method | 2 | 1 | 1 | 4,164 | 1,343 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_year | 1 | 0 | 1 | 1,173 | 562 | Unknown | 0 | 0 | 1 | — |
| total_city_water_withdrawals_m3_day | 3 | 3 | 0 | 7,023 | 817 | Unknown | 0 | 0 | 0 | — |
| total_city_water_withdrawals_year | 1 | 0 | 1 | 1,174 | 498 | Unknown | 0 | 0 | 1 | — |
| utility_name | 3 | 2 | 1 | 7,822 | 660 | Unknown | 0 | 0 | 1 | — |
| utility_reported_city_population_millions | 2 | 2 | 0 | 3,235 | 377 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| utility_reported_city_population_reference_year | 2 | 2 | 0 | 3,400 | 505 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| water_quality_notes | 2 | 1 | 1 | 3,861 | 1,524 | Unknown | 0 | 0 | 1 | — |
| water_quality_year | 2 | 1 | 1 | 3,610 | 1,440 | Unknown | 0 | 0 | 3 | NO_SUFFICIENT_EVIDENCE 1 |
| water_source | 3 | 0 | 3 | 13,848 | 4,707 | Unknown | 0 | 0 | 10 | — |
| water_system | 1 | 0 | 1 | 1,233 | 323 | Unknown | 0 | 0 | 1 | — |
| water_transfer_system | 1 | 0 | 1 | 1,413 | 635 | Unknown | 0 | 0 | 1 | NO_SUFFICIENT_EVIDENCE 1 |
| water_type | 1 | 0 | 1 | 3,005 | 347 | Unknown | 0 | 0 | 1 | — |

### Calls that returned no claims

| round | field | stage | input tok | output tok |
|---|---|---|---|---|
| 1 | city_population_millions | initial | 1,030 | 94 |
| 1 | city_population_reference_year | initial | 1,121 | 155 |
| 1 | infrastructure_notes | initial | 966 | 96 |
| 1 | jurisdiction | initial | 1,144 | 198 |
| 1 | past_total_city_water_withdrawals | initial | 1,217 | 235 |
| 1 | population_served_millions | initial | 883 | 65 |
| 1 | references | initial | 1,163 | 539 |
| 1 | source_withdrawal_contribution_method | initial | 1,198 | 772 |
| 1 | total_city_water_withdrawals_m3_day | initial | 1,575 | 260 |
| 1 | utility_name | initial | 1,071 | 194 |
| 1 | water_quality_notes | initial | 1,184 | 483 |
| 2 | city_latitude | initial | 921 | 133 |
| 2 | city_longitude | initial | 921 | 98 |
| 2 | city_population_millions | initial | 1,898 | 264 |
| 2 | past_total_city_water_withdrawals | initial | 1,659 | 216 |
| 2 | pod_coordinate_method | initial | 1,070 | 154 |
| 2 | pod_latitude | initial | 1,002 | 109 |
| 2 | pod_longitude | initial | 1,002 | 148 |
| 2 | population_served_millions | initial | 1,434 | 167 |
| 2 | references | initial | 1,758 | 1,355 |
| 2 | total_city_water_withdrawals_m3_day | initial | 2,507 | 241 |
| 2 | utility_name | initial | 2,852 | 173 |
| 2 | utility_reported_city_population_millions | initial | 1,335 | 126 |
| 2 | utility_reported_city_population_reference_year | initial | 1,516 | 197 |
| 2 | water_quality_year | initial | 1,613 | 196 |
| 3 | city_population_millions | initial | 2,101 | 310 |
| 3 | past_total_city_water_withdrawals | initial | 2,693 | 464 |
| 3 | pod_coordinate_method | initial | 1,793 | 78 |
| 3 | pod_latitude | initial | 1,559 | 79 |
| 3 | pod_longitude | initial | 1,559 | 187 |
| 3 | population_served_millions | initial | 1,772 | 172 |
| 3 | references | initial | 1,903 | 1,045 |
| 3 | total_city_water_withdrawals_m3_day | initial | 2,941 | 316 |
| 3 | utility_reported_city_population_millions | initial | 1,900 | 251 |
| 3 | utility_reported_city_population_reference_year | initial | 1,884 | 308 |

## Houston — 759d7681 (REPLAY, SUCCEEDED)

Events from run_events; ledger rows 0 (not all matched to slots: event token counts used there).

- Calls 58 (model 58, app-cache hits 0), zero-claim 35, productive 23
- Input 106,124 (provider-cached Unknown), output 25,492; avoided by app cache 0
- Claims 41 (VALID 41, selected 13); repeated identical contexts Unknown

### By round

| round | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 21 | 9 | 12 | 29,962 | 9,818 | Unknown | 0 | 0 | 19 | NO_SUFFICIENT_EVIDENCE 9 |
| 2 | 20 | 15 | 5 | 37,873 | 8,286 | Unknown | 0 | 0 | 10 | NO_SUFFICIENT_EVIDENCE 1 |
| 3 | 17 | 11 | 6 | 38,289 | 7,388 | Unknown | 0 | 0 | 12 | NO_SUFFICIENT_EVIDENCE 2 |

### Initial vs re-extraction

| stage | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| initial | 52 | 35 | 17 | 81,962 | 20,070 | Unknown | 0 | 0 | 24 | NO_SUFFICIENT_EVIDENCE 12 |
| reextraction | 6 | 0 | 6 | 24,162 | 5,422 | Unknown | 0 | 0 | 17 | — |

### By field

| field | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| city_latitude | 1 | 1 | 0 | 955 | 71 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| city_longitude | 1 | 1 | 0 | 955 | 76 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| city_name | 1 | 0 | 1 | 1,386 | 400 | Unknown | 0 | 0 | 1 | — |
| city_population_millions | 3 | 2 | 1 | 3,954 | 730 | Unknown | 0 | 0 | 1 | — |
| city_population_reference_year | 1 | 0 | 1 | 1,202 | 354 | Unknown | 0 | 0 | 1 | — |
| country | 1 | 1 | 0 | 901 | 115 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 2 |
| infrastructure_notes | 1 | 0 | 1 | 2,066 | 1,459 | Unknown | 0 | 0 | 1 | NO_SUFFICIENT_EVIDENCE 1 |
| jurisdiction | 1 | 0 | 1 | 1,413 | 704 | Unknown | 0 | 0 | 1 | — |
| past_total_city_water_withdrawals | 3 | 3 | 0 | 4,494 | 413 | Unknown | 0 | 0 | 0 | — |
| pod_coordinate_method | 2 | 2 | 0 | 2,868 | 271 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_latitude | 2 | 2 | 0 | 2,732 | 171 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_longitude | 2 | 2 | 0 | 2,732 | 163 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| population_served_millions | 1 | 0 | 1 | 1,304 | 270 | Unknown | 0 | 0 | 1 | — |
| references | 3 | 3 | 0 | 6,204 | 3,833 | Unknown | 0 | 0 | 0 | — |
| reservoir_names | 3 | 0 | 3 | 8,746 | 3,056 | Unknown | 0 | 0 | 9 | — |
| seasonal_uses | 2 | 2 | 0 | 2,804 | 335 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| source_withdrawal_contribution_m3_day | 1 | 0 | 1 | 1,352 | 751 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_method | 3 | 2 | 1 | 6,974 | 820 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_year | 3 | 3 | 0 | 4,444 | 520 | Unknown | 0 | 0 | 0 | — |
| total_city_water_withdrawals_m3_day | 3 | 3 | 0 | 4,749 | 465 | Unknown | 0 | 0 | 0 | — |
| total_city_water_withdrawals_year | 3 | 3 | 0 | 4,447 | 490 | Unknown | 0 | 0 | 0 | — |
| utility_name | 3 | 0 | 3 | 10,404 | 1,477 | Unknown | 0 | 0 | 3 | — |
| utility_reported_city_population_millions | 1 | 0 | 1 | 1,355 | 505 | Unknown | 0 | 0 | 2 | — |
| utility_reported_city_population_reference_year | 1 | 0 | 1 | 1,098 | 349 | Unknown | 0 | 0 | 1 | — |
| water_quality_notes | 1 | 0 | 1 | 1,365 | 690 | Unknown | 0 | 0 | 1 | — |
| water_quality_year | 1 | 0 | 1 | 856 | 213 | Unknown | 0 | 0 | 1 | — |
| water_source | 3 | 0 | 3 | 12,255 | 4,175 | Unknown | 0 | 0 | 14 | — |
| water_system | 3 | 2 | 1 | 4,184 | 1,138 | Unknown | 0 | 0 | 1 | — |
| water_transfer_system | 2 | 2 | 0 | 2,567 | 448 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| water_type | 2 | 1 | 1 | 5,358 | 1,030 | Unknown | 0 | 0 | 1 | — |

### Calls that returned no claims

| round | field | stage | input tok | output tok |
|---|---|---|---|---|
| 1 | city_population_millions | initial | 1,218 | 196 |
| 1 | past_total_city_water_withdrawals | initial | 1,116 | 75 |
| 1 | references | initial | 1,145 | 644 |
| 1 | source_withdrawal_contribution_method | initial | 1,168 | 201 |
| 1 | source_withdrawal_contribution_year | initial | 1,072 | 95 |
| 1 | total_city_water_withdrawals_m3_day | initial | 1,187 | 173 |
| 1 | total_city_water_withdrawals_year | initial | 1,073 | 156 |
| 1 | water_system | initial | 1,289 | 180 |
| 1 | water_type | initial | 2,120 | 576 |
| 2 | city_latitude | initial | 955 | 71 |
| 2 | city_longitude | initial | 955 | 76 |
| 2 | city_population_millions | initial | 1,251 | 119 |
| 2 | past_total_city_water_withdrawals | initial | 1,511 | 148 |
| 2 | pod_coordinate_method | initial | 1,455 | 184 |
| 2 | pod_latitude | initial | 1,387 | 92 |
| 2 | pod_longitude | initial | 1,387 | 75 |
| 2 | references | initial | 2,458 | 1,708 |
| 2 | seasonal_uses | initial | 1,289 | 185 |
| 2 | source_withdrawal_contribution_method | initial | 2,460 | 240 |
| 2 | source_withdrawal_contribution_year | initial | 1,429 | 222 |
| 2 | total_city_water_withdrawals_m3_day | initial | 1,668 | 170 |
| 2 | total_city_water_withdrawals_year | initial | 1,430 | 203 |
| 2 | water_system | initial | 1,054 | 350 |
| 2 | water_transfer_system | initial | 1,176 | 219 |
| 3 | country | initial | 901 | 115 |
| 3 | past_total_city_water_withdrawals | initial | 1,867 | 190 |
| 3 | pod_coordinate_method | initial | 1,413 | 87 |
| 3 | pod_latitude | initial | 1,345 | 79 |
| 3 | pod_longitude | initial | 1,345 | 88 |
| 3 | references | initial | 2,601 | 1,481 |
| 3 | seasonal_uses | initial | 1,515 | 150 |
| 3 | source_withdrawal_contribution_year | initial | 1,943 | 203 |
| 3 | total_city_water_withdrawals_m3_day | initial | 1,894 | 122 |
| 3 | total_city_water_withdrawals_year | initial | 1,944 | 131 |
| 3 | water_transfer_system | initial | 1,391 | 229 |

## Luanda — 90368a4d (REPLAY, SUCCEEDED)

Events from run_events; ledger rows 0 (not all matched to slots: event token counts used there).

- Calls 66 (model 66, app-cache hits 0), zero-claim 48, productive 18
- Input 169,623 (provider-cached Unknown), output 23,659; avoided by app cache 0
- Claims 22 (VALID 22, selected 0); repeated identical contexts Unknown

### By round

| round | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 30 | 20 | 10 | 63,808 | 11,119 | Unknown | 0 | 0 | 12 | — |
| 2 | 22 | 17 | 5 | 62,611 | 8,324 | Unknown | 0 | 0 | 6 | — |
| 3 | 14 | 11 | 3 | 43,204 | 4,216 | Unknown | 0 | 0 | 4 | NO_SUFFICIENT_EVIDENCE 5 |

### Initial vs re-extraction

| stage | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| initial | 62 | 48 | 14 | 149,869 | 21,020 | Unknown | 0 | 0 | 16 | NO_SUFFICIENT_EVIDENCE 5 |
| reextraction | 4 | 0 | 4 | 19,754 | 2,639 | Unknown | 0 | 0 | 6 | — |

### By field

| field | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| city_latitude | 2 | 2 | 0 | 1,945 | 277 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| city_longitude | 2 | 2 | 0 | 1,833 | 161 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| city_name | 1 | 0 | 1 | 2,307 | 420 | Unknown | 0 | 0 | 1 | — |
| city_population_millions | 1 | 0 | 1 | 2,709 | 554 | Unknown | 0 | 0 | 1 | — |
| city_population_reference_year | 2 | 1 | 1 | 5,865 | 814 | Unknown | 0 | 0 | 1 | — |
| country | 1 | 0 | 1 | 2,335 | 294 | Unknown | 0 | 0 | 1 | — |
| infrastructure_notes | 2 | 1 | 1 | 5,466 | 1,608 | Unknown | 0 | 0 | 1 | — |
| jurisdiction | 1 | 0 | 1 | 2,810 | 439 | Unknown | 0 | 0 | 1 | — |
| past_total_city_water_withdrawals | 3 | 3 | 0 | 9,875 | 1,001 | Unknown | 0 | 0 | 0 | — |
| pod_coordinate_method | 2 | 2 | 0 | 2,370 | 281 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_latitude | 2 | 2 | 0 | 2,658 | 277 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_longitude | 2 | 2 | 0 | 2,548 | 187 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| population_served_millions | 3 | 3 | 0 | 5,468 | 533 | Unknown | 0 | 0 | 0 | — |
| references | 3 | 3 | 0 | 9,156 | 2,534 | Unknown | 0 | 0 | 0 | — |
| reservoir_names | 3 | 3 | 0 | 8,901 | 788 | Unknown | 0 | 0 | 0 | — |
| seasonal_uses | 3 | 3 | 0 | 7,115 | 1,105 | Unknown | 0 | 0 | 0 | — |
| source_withdrawal_contribution_m3_day | 2 | 1 | 1 | 6,912 | 835 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_method | 1 | 0 | 1 | 2,191 | 545 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_year | 3 | 3 | 0 | 10,887 | 872 | Unknown | 0 | 0 | 0 | — |
| total_city_water_withdrawals_m3_day | 3 | 2 | 1 | 10,577 | 807 | Unknown | 0 | 0 | 1 | — |
| total_city_water_withdrawals_year | 3 | 3 | 0 | 10,890 | 696 | Unknown | 0 | 0 | 0 | — |
| utility_name | 3 | 0 | 3 | 9,524 | 1,258 | Unknown | 0 | 0 | 3 | — |
| utility_reported_city_population_millions | 3 | 3 | 0 | 6,141 | 756 | Unknown | 0 | 0 | 0 | — |
| utility_reported_city_population_reference_year | 3 | 3 | 0 | 3,965 | 686 | Unknown | 0 | 0 | 0 | — |
| water_quality_notes | 1 | 0 | 1 | 2,203 | 759 | Unknown | 0 | 0 | 1 | — |
| water_quality_year | 3 | 3 | 0 | 4,073 | 512 | Unknown | 0 | 0 | 0 | — |
| water_source | 3 | 0 | 3 | 17,032 | 2,868 | Unknown | 0 | 0 | 7 | — |
| water_system | 1 | 0 | 1 | 2,413 | 751 | Unknown | 0 | 0 | 1 | — |
| water_transfer_system | 3 | 3 | 0 | 5,799 | 705 | Unknown | 0 | 0 | 0 | — |
| water_type | 1 | 0 | 1 | 3,655 | 336 | Unknown | 0 | 0 | 1 | — |

### Calls that returned no claims

| round | field | stage | input tok | output tok |
|---|---|---|---|---|
| 1 | city_latitude | initial | 992 | 139 |
| 1 | city_longitude | initial | 936 | 84 |
| 1 | city_population_reference_year | initial | 2,132 | 241 |
| 1 | infrastructure_notes | initial | 2,380 | 428 |
| 1 | past_total_city_water_withdrawals | initial | 2,056 | 233 |
| 1 | pod_coordinate_method | initial | 1,397 | 168 |
| 1 | pod_latitude | initial | 1,384 | 142 |
| 1 | pod_longitude | initial | 1,329 | 93 |
| 1 | population_served_millions | initial | 1,841 | 172 |
| 1 | references | initial | 1,872 | 1,032 |
| 1 | reservoir_names | initial | 2,820 | 224 |
| 1 | seasonal_uses | initial | 2,536 | 370 |
| 1 | source_withdrawal_contribution_m3_day | initial | 2,195 | 193 |
| 1 | source_withdrawal_contribution_year | initial | 2,216 | 359 |
| 1 | total_city_water_withdrawals_m3_day | initial | 2,439 | 104 |
| 1 | total_city_water_withdrawals_year | initial | 2,217 | 271 |
| 1 | utility_reported_city_population_millions | initial | 2,005 | 299 |
| 1 | utility_reported_city_population_reference_year | initial | 973 | 351 |
| 1 | water_quality_year | initial | 1,000 | 292 |
| 1 | water_transfer_system | initial | 1,663 | 339 |
| 2 | city_latitude | initial | 953 | 138 |
| 2 | city_longitude | initial | 897 | 77 |
| 2 | past_total_city_water_withdrawals | initial | 3,476 | 469 |
| 2 | pod_coordinate_method | initial | 973 | 113 |
| 2 | pod_latitude | initial | 1,274 | 135 |
| 2 | pod_longitude | initial | 1,219 | 94 |
| 2 | population_served_millions | initial | 1,942 | 161 |
| 2 | references | initial | 3,505 | 1,296 |
| 2 | reservoir_names | initial | 3,525 | 242 |
| 2 | seasonal_uses | initial | 2,266 | 248 |
| 2 | source_withdrawal_contribution_year | initial | 4,273 | 244 |
| 2 | total_city_water_withdrawals_m3_day | initial | 3,986 | 344 |
| 2 | total_city_water_withdrawals_year | initial | 4,274 | 188 |
| 2 | utility_reported_city_population_millions | initial | 2,202 | 282 |
| 2 | utility_reported_city_population_reference_year | initial | 1,632 | 116 |
| 2 | water_quality_year | initial | 1,722 | 121 |
| 2 | water_transfer_system | initial | 2,539 | 276 |
| 3 | past_total_city_water_withdrawals | initial | 4,343 | 299 |
| 3 | population_served_millions | initial | 1,685 | 200 |
| 3 | references | initial | 3,779 | 206 |
| 3 | reservoir_names | initial | 2,556 | 322 |
| 3 | seasonal_uses | initial | 2,313 | 487 |
| 3 | source_withdrawal_contribution_year | initial | 4,398 | 269 |
| 3 | total_city_water_withdrawals_year | initial | 4,399 | 237 |
| 3 | utility_reported_city_population_millions | initial | 1,934 | 175 |
| 3 | utility_reported_city_population_reference_year | initial | 1,360 | 219 |
| 3 | water_quality_year | initial | 1,351 | 99 |
| 3 | water_transfer_system | initial | 1,597 | 90 |

## Vienna — 10cfd3de (REPLAY, SUCCEEDED)

Events from run_events; ledger rows 0 (not all matched to slots: event token counts used there).

- Calls 57 (model 57, app-cache hits 0), zero-claim 37, productive 20
- Input 100,934 (provider-cached Unknown), output 21,434; avoided by app cache 0
- Claims 28 (VALID 28, selected 11); repeated identical contexts Unknown

### By round

| round | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 21 | 8 | 13 | 26,875 | 7,892 | Unknown | 0 | 0 | 17 | NO_SUFFICIENT_EVIDENCE 9 |
| 2 | 20 | 15 | 5 | 34,672 | 8,944 | Unknown | 0 | 0 | 6 | — |
| 3 | 16 | 14 | 2 | 39,387 | 4,598 | Unknown | 0 | 0 | 5 | — |

### Initial vs re-extraction

| stage | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| initial | 51 | 34 | 17 | 79,477 | 16,993 | Unknown | 0 | 0 | 22 | NO_SUFFICIENT_EVIDENCE 9 |
| reextraction | 6 | 3 | 3 | 21,457 | 4,441 | Unknown | 0 | 0 | 6 | — |

### By field

| field | calls | zero-claim | productive | input tok | output tok | provider-cached | app-cache hits | tok avoided | valid claims | skipped (no call) |
|---|---|---|---|---|---|---|---|---|---|---|
| city_latitude | 2 | 2 | 0 | 1,916 | 180 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| city_longitude | 2 | 2 | 0 | 1,834 | 184 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| city_name | 1 | 0 | 1 | 1,291 | 516 | Unknown | 0 | 0 | 1 | — |
| city_population_millions | 2 | 1 | 1 | 2,320 | 506 | Unknown | 0 | 0 | 1 | — |
| city_population_reference_year | 3 | 3 | 0 | 3,591 | 402 | Unknown | 0 | 0 | 0 | — |
| country | 1 | 0 | 1 | 932 | 226 | Unknown | 0 | 0 | 1 | — |
| infrastructure_notes | 1 | 0 | 1 | 1,185 | 281 | Unknown | 0 | 0 | 1 | — |
| jurisdiction | 3 | 3 | 0 | 6,008 | 796 | Unknown | 0 | 0 | 0 | — |
| past_total_city_water_withdrawals | 3 | 3 | 0 | 6,473 | 415 | Unknown | 0 | 0 | 0 | — |
| pod_coordinate_method | 2 | 2 | 0 | 2,081 | 200 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_latitude | 2 | 2 | 0 | 1,945 | 247 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| pod_longitude | 2 | 2 | 0 | 1,945 | 206 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| population_served_millions | 1 | 0 | 1 | 962 | 257 | Unknown | 0 | 0 | 1 | — |
| references | 1 | 0 | 1 | 1,914 | 1,344 | Unknown | 0 | 0 | 1 | NO_SUFFICIENT_EVIDENCE 1 |
| reservoir_names | 3 | 1 | 2 | 7,870 | 2,444 | Unknown | 0 | 0 | 6 | — |
| seasonal_uses | 2 | 2 | 0 | 3,260 | 916 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| source_withdrawal_contribution_m3_day | 1 | 0 | 1 | 1,146 | 580 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_method | 1 | 0 | 1 | 951 | 304 | Unknown | 0 | 0 | 1 | — |
| source_withdrawal_contribution_year | 3 | 3 | 0 | 6,065 | 669 | Unknown | 0 | 0 | 0 | — |
| total_city_water_withdrawals_m3_day | 1 | 0 | 1 | 1,154 | 338 | Unknown | 0 | 0 | 1 | — |
| total_city_water_withdrawals_year | 3 | 3 | 0 | 6,068 | 684 | Unknown | 0 | 0 | 0 | — |
| utility_name | 3 | 2 | 1 | 6,953 | 975 | Unknown | 0 | 0 | 1 | — |
| utility_reported_city_population_millions | 1 | 0 | 1 | 969 | 263 | Unknown | 0 | 0 | 1 | — |
| utility_reported_city_population_reference_year | 2 | 2 | 0 | 3,761 | 315 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| water_quality_notes | 2 | 1 | 1 | 4,139 | 2,171 | Unknown | 0 | 0 | 2 | — |
| water_quality_year | 2 | 2 | 0 | 4,988 | 815 | Unknown | 0 | 0 | 0 | NO_SUFFICIENT_EVIDENCE 1 |
| water_source | 3 | 0 | 3 | 12,793 | 3,091 | Unknown | 0 | 0 | 6 | — |
| water_system | 1 | 0 | 1 | 1,454 | 628 | Unknown | 0 | 0 | 1 | — |
| water_transfer_system | 2 | 1 | 1 | 3,068 | 701 | Unknown | 0 | 0 | 1 | — |
| water_type | 1 | 0 | 1 | 1,898 | 780 | Unknown | 0 | 0 | 1 | — |

### Calls that returned no claims

| round | field | stage | input tok | output tok |
|---|---|---|---|---|
| 1 | city_population_millions | initial | 1,045 | 116 |
| 1 | city_population_reference_year | initial | 1,028 | 132 |
| 1 | jurisdiction | initial | 1,056 | 160 |
| 1 | past_total_city_water_withdrawals | initial | 1,100 | 161 |
| 1 | source_withdrawal_contribution_year | initial | 1,041 | 111 |
| 1 | total_city_water_withdrawals_year | initial | 1,042 | 74 |
| 1 | water_quality_notes | initial | 1,280 | 772 |
| 1 | water_transfer_system | initial | 1,182 | 124 |
| 2 | city_latitude | initial | 870 | 87 |
| 2 | city_longitude | initial | 870 | 115 |
| 2 | city_population_reference_year | initial | 1,082 | 118 |
| 2 | jurisdiction | initial | 1,731 | 309 |
| 2 | past_total_city_water_withdrawals | initial | 1,716 | 130 |
| 2 | pod_coordinate_method | initial | 946 | 90 |
| 2 | pod_latitude | initial | 878 | 119 |
| 2 | pod_longitude | initial | 878 | 86 |
| 2 | reservoir_names | reextraction | 2,599 | 772 |
| 2 | seasonal_uses | initial | 1,420 | 510 |
| 2 | source_withdrawal_contribution_year | initial | 1,812 | 236 |
| 2 | total_city_water_withdrawals_year | initial | 1,813 | 415 |
| 2 | utility_name | reextraction | 2,549 | 272 |
| 2 | utility_reported_city_population_reference_year | initial | 1,338 | 117 |
| 2 | water_quality_year | initial | 1,806 | 359 |
| 3 | city_latitude | initial | 1,046 | 93 |
| 3 | city_longitude | initial | 964 | 69 |
| 3 | city_population_reference_year | initial | 1,481 | 152 |
| 3 | jurisdiction | initial | 3,221 | 327 |
| 3 | past_total_city_water_withdrawals | initial | 3,657 | 124 |
| 3 | pod_coordinate_method | initial | 1,135 | 110 |
| 3 | pod_latitude | initial | 1,067 | 128 |
| 3 | pod_longitude | initial | 1,067 | 120 |
| 3 | seasonal_uses | initial | 1,840 | 406 |
| 3 | source_withdrawal_contribution_year | initial | 3,212 | 322 |
| 3 | total_city_water_withdrawals_year | initial | 3,213 | 195 |
| 3 | utility_name | reextraction | 2,790 | 388 |
| 3 | utility_reported_city_population_reference_year | initial | 2,423 | 198 |
| 3 | water_quality_year | initial | 3,182 | 456 |
