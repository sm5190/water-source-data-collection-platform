# Saved-document replay: before / after

Baseline = the original run, re-scored now with the current (corrected) evaluation. Replay = the same saved documents, introduced in their original acquisition rounds, through the current retrieval, re-extraction and source-entity code. **No new discovery or fetching**: gains here are from the pipeline on identical evidence; gains from better discovery can only come from fresh runs.

Judge-made source pairs are provisional: the semantic judge is not validated yet.

## Luanda

Workbook sources (2): Bengo River, Kwanza River

| | Baseline | Replay |
|---|---|---|
| RAW source recall | 0/2 (0%) | 2/2 (100%) |
| RAW source precision | 0/1 (0%) | 2/3 (67%) |
| PROPOSED source recall | 0/2 (0%) | 2/2 (100%) |
| PROPOSED source precision | 0/1 (0%) | 2/3 (67%) |
| AUTO source recall | 0/2 (0%) | 0/2 (0%) |
| AUTO source precision | n/a | n/a |
| Missing (RAW) | Bengo River, Kwanza River | — |
| Extra (RAW) | Groundwater (aquifer) | Quelo sedimentary aquifer |
| Reservoir/region/category named as a source (RAW) | 0 | 0 |
| Out-of-area sources (RAW) | 0 | 0 |
| Judge-made (provisional) matches (RAW) | 0 | 1 |
| Workbook sources in acquired text | 2/2 | 2/2 |
| Workbook sources in passages shown for water_source | 1/2 | 2/2 |
| Extraction LLM requests (cache hits) | 59 (0) | 66 (0) |
| Prompt / completion tokens | 137,824 / 22,243 | 169,623 / 23,659 |
| Re-extractions by round | — | {2: 2, 3: 2} |
| water_source candidates | Groundwater (aquifer) | Bengo River, Cuanza River, Quelo sedimentary aquifer |

**Where the change came from**

- Retrieval: workbook sources newly shown to the model: Bengo River (no longer shown: —).
- Later-round re-extraction: sources first proposed in a round > 1: Bengo River, Cuanza River; 2 of the newly matched workbook sources (Bengo River, Kwanza River) came from a later round.
- Entity handling: reservoirs/regions/categories proposed as sources 0 -> 0; PROPOSED source precision 0/1 (0%) -> 2/3 (67%).

## Nairobi

Workbook sources (4): Thika River, Chania River, Ruiru River, Kikuyu Springs

| | Baseline | Replay |
|---|---|---|
| RAW source recall | 0/4 (0%) | 3/4 (75%) |
| RAW source precision | 0/3 (0%) | 3/9 (33%) |
| PROPOSED source recall | 0/4 (0%) | 3/4 (75%) |
| PROPOSED source precision | 0/3 (0%) | 3/9 (33%) |
| AUTO source recall | 0/4 (0%) | 2/4 (50%) |
| AUTO source precision | 0/1 (0%) | 2/3 (67%) |
| Missing (RAW) | Thika River, Chania River, Ruiru River, Kikuyu Springs | Kikuyu Springs |
| Extra (RAW) | Surface water from the Tana Basin, Reservoirs/Dams (Ndakaini, Njambini, Githunguri), Groundwater (pumped by private operators, domestic wells) | Groundwater, Tana River, Irati River, Gikigie River, Maragua River, Sagana River |
| Reservoir/region/category named as a source (RAW) | 2 | 0 |
| Out-of-area sources (RAW) | 0 | 0 |
| Judge-made (provisional) matches (RAW) | 0 | 0 |
| Workbook sources in acquired text | 4/4 | 4/4 |
| Workbook sources in passages shown for water_source | 1/4 | 3/4 |
| Extraction LLM requests (cache hits) | 54 (0) | 57 (0) |
| Prompt / completion tokens | 86,111 / 23,532 | 107,525 / 26,188 |
| Re-extractions by round | — | {2: 2, 3: 2} |
| water_source candidates | Groundwater (pumped by private operators, domestic wells), Reservoirs/Dams (Ndakaini, Njambini, Githunguri), Surface water from the Tana Basin | Chania River, Gikigie River, Groundwater, Irati River, Maragua River, Ruiru River, Sagana River, Tana River, Thika River |

**Where the change came from**

- Retrieval: workbook sources newly shown to the model: Chania River, Thika River (no longer shown: —).
- Later-round re-extraction: sources first proposed in a round > 1: Chania River, Gikigie River, Irati River, Maragua River, Sagana River, Tana River, Thika River; 2 of the newly matched workbook sources (Chania River, Ruiru River, Thika River) came from a later round.
- Entity handling: reservoirs/regions/categories proposed as sources 2 -> 0; PROPOSED source precision 0/3 (0%) -> 3/9 (33%).

## Vienna

Workbook sources (4): Rax-Schneeberg-Schneealpe Karst Springs, Hochschwab Karst Springs, Lobau Groundwater, Mitterndorfer Senke Aquifer

| | Baseline | Replay |
|---|---|---|
| RAW source recall | 0/4 (0%) | 0/4 (0%) |
| RAW source precision | 0/4 (0%) | 0/6 (0%) |
| PROPOSED source recall | 0/4 (0%) | 0/4 (0%) |
| PROPOSED source precision | 0/4 (0%) | 0/6 (0%) |
| AUTO source recall | 0/4 (0%) | 0/4 (0%) |
| AUTO source precision | 0/2 (0%) | 0/5 (0%) |
| Missing (RAW) | Rax-Schneeberg-Schneealpe Karst Springs, Hochschwab Karst Springs, Lobau Groundwater, Mitterndorfer Senke Aquifer | Rax-Schneeberg-Schneealpe Karst Springs, Hochschwab Karst Springs, Lobau Groundwater, Mitterndorfer Senke Aquifer |
| Extra (RAW) | Limestone Alps of Lower Austria and Styria, springs of the Rax, Schneeberg and Hochschwab, Wienerberg reservoir, Dreimarkstein reservoir on the Kahlenberg | Rax, Schneeberg, Hochschwab, wells alongside the Danube, Lobau, Moosbrunn, Danube Island and Nussdorf wellfield, Mountain Spring Water |
| Reservoir/region/category named as a source (RAW) | 2 | 0 |
| Out-of-area sources (RAW) | 0 | 0 |
| Judge-made (provisional) matches (RAW) | 0 | 0 |
| Workbook sources in acquired text | 3/4 | 3/4 |
| Workbook sources in passages shown for water_source | 1/4 | 3/4 |
| Extraction LLM requests (cache hits) | 52 (0) | 57 (0) |
| Prompt / completion tokens | 74,437 / 16,106 | 100,934 / 21,434 |
| Re-extractions by round | — | {2: 3, 3: 3} |
| water_source candidates | Dreimarkstein reservoir on the Kahlenberg, Limestone Alps of Lower Austria and Styria, Wienerberg reservoir, springs of the Rax, Schneeberg and Hochschwab | Hochschwab, Lobau, Moosbrunn, Danube Island and Nussdorf wellfield, Mountain Spring Water, Rax, Schneeberg, wells alongside the Danube |

**Where the change came from**

- Retrieval: workbook sources newly shown to the model: Lobau Groundwater, Rax-Schneeberg-Schneealpe Karst Springs (no longer shown: —).
- Later-round re-extraction: sources first proposed in a round > 1: Lobau, Moosbrunn, Danube Island and Nussdorf wellfield, Mountain Spring Water; 0 of the newly matched workbook sources (—) came from a later round.
- Entity handling: reservoirs/regions/categories proposed as sources 2 -> 0; PROPOSED source precision 0/4 (0%) -> 0/6 (0%).

## Houston

Workbook sources (3): Trinity River, San Jacinto River, Chicot & Evangeline Aquifers

| | Baseline | Replay |
|---|---|---|
| RAW source recall | 0/3 (0%) | 2/3 (67%) |
| RAW source precision | 0/4 (0%) | 2/7 (29%) |
| PROPOSED source recall | 0/3 (0%) | 2/3 (67%) |
| PROPOSED source precision | 0/4 (0%) | 2/7 (29%) |
| AUTO source recall | 0/3 (0%) | 0/3 (0%) |
| AUTO source precision | 0/4 (0%) | 0/4 (0%) |
| Missing (RAW) | Trinity River, San Jacinto River, Chicot & Evangeline Aquifers | Chicot & Evangeline Aquifers |
| Extra (RAW) | Lake Conroe, Lake Houston, Lake Livingston, Eutaw Aquifer | Lake Conroe, Lake Houston, Lake Livingston, Eutaw Aquifer, Groundwater |
| Reservoir/region/category named as a source (RAW) | 0 | 0 |
| Out-of-area sources (RAW) | 0 | 0 |
| Judge-made (provisional) matches (RAW) | 0 | 0 |
| Workbook sources in acquired text | 3/3 | 3/3 |
| Workbook sources in passages shown for water_source | 2/3 | 3/3 |
| Extraction LLM requests (cache hits) | 43 (0) | 58 (0) |
| Prompt / completion tokens | 62,211 / 16,205 | 106,124 / 25,492 |
| Re-extractions by round | — | {2: 3, 3: 3} |
| water_source candidates | Eutaw Aquifer, Lake Conroe, Lake Houston, Lake Livingston | Eutaw Aquifer, Groundwater, Lake Conroe, Lake Houston, Lake Livingston, San Jacinto River, Trinity River |

**Where the change came from**

- Retrieval: workbook sources newly shown to the model: Chicot & Evangeline Aquifers (no longer shown: —).
- Later-round re-extraction: sources first proposed in a round > 1: Groundwater, Lake Conroe, Lake Houston, Lake Livingston, San Jacinto River, Trinity River; 2 of the newly matched workbook sources (San Jacinto River, Trinity River) came from a later round.
- Entity handling: reservoirs/regions/categories proposed as sources 0 -> 0; PROPOSED source precision 0/4 (0%) -> 2/7 (29%).
