# Counterfactual replay (recorded answers, no model calls)

Each run's saved documents replayed in their original rounds with the current code; extraction answered with the claims the run got in the same (field, round). Counterfactual tokens are estimates from the run's own slots.

## Santiago — 0d7d4af8

- Model calls: 49 → 32
- Tokens (input+output): 74,862 → ~52,848 (-29.4%)
- Skipped slots that had usable claims: **0**
- Claims not reproducible from the passages now shown: 0
- water_source candidates: groundwater wells → groundwater wells

| round | field | stage | now | tokens | claims the run got there |
|---|---|---|---|---|---|
| 3 | references | initial | NOT_SCHEDULED | 2,218 | — |

## Nairobi — 293e896f

- Model calls: 57 → 52
- Tokens (input+output): 133,713 → ~122,255 (-8.6%)
- Skipped slots that had usable claims: **1**
- Claims not reproducible from the passages now shown: 0
- water_source candidates: Chania River, Gikigie River, Irati River, Maragua River, Ruiru River, Sagana River, Tana River, Thika River, groundwater → Chania River, Gikigie River, Irati River, Maragua River, Ruiru River, Sagana River, Tana River, Thika River, groundwater

| round | field | stage | now | tokens | claims the run got there |
|---|---|---|---|---|---|
| 3 | references | initial | NOT_SCHEDULED | 2,948 | — |

## Houston — 759d7681

- Model calls: 58 → 53
- Tokens (input+output): 131,616 → ~118,777 (-9.8%)
- Skipped slots that had usable claims: **0**
- Claims not reproducible from the passages now shown: 0
- water_source candidates: Eutaw Aquifer, Lake Conroe, Lake Houston, Lake Livingston, San Jacinto River, Trinity River, groundwater → Eutaw Aquifer, Lake Conroe, Lake Houston, Lake Livingston, San Jacinto River, Trinity River, groundwater

| round | field | stage | now | tokens | claims the run got there |
|---|---|---|---|---|---|
| 3 | references | initial | NOT_SCHEDULED | 4,082 | — |

## Luanda — 90368a4d

- Model calls: 66 → 51
- Tokens (input+output): 193,282 → ~162,225 (-16.1%)
- Skipped slots that had usable claims: **0**
- Claims not reproducible from the passages now shown: 0
- water_source candidates: Bengo River, Cuanza River, Quelo sedimentary aquifer → Bengo River, Cuanza River, Quelo sedimentary aquifer

| round | field | stage | now | tokens | claims the run got there |
|---|---|---|---|---|---|
| 3 | water_quality_year | initial | NO_RELEVANT_EVIDENCE | 1,450 | — |

## Vienna — 10cfd3de

- Model calls: 57 → 45
- Tokens (input+output): 122,368 → ~106,461 (-13.0%)
- Skipped slots that had usable claims: **1**
- Claims not reproducible from the passages now shown: 0
- water_source candidates: Hochschwab, Lobau, Moosbrunn, Danube Island and Nussdorf wellfield, Rax, Schneeberg, mountain spring water, wells alongside the Danube → Hochschwab, Lobau, Moosbrunn, Danube Island and Nussdorf wellfield, Rax, Schneeberg, mountain spring water, wells alongside the Danube

| round | field | stage | now | tokens | claims the run got there |
|---|---|---|---|---|---|
| 2 | references | initial | NOT_SCHEDULED | 3,258 | https://www.viennava.gov/files/assets/town/v/2/finance/water-and-sewer/town-of-vienna-water-quality-reports/2020-water-quality-report.pdf; https://www.wien.gv.at/en/environment/results-drinking-water-sampling (VALID_WITH_WARNINGS) |
