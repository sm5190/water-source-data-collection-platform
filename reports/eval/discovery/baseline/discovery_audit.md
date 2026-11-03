# Discovery audit

Read-only audit of discovery. *Utility hosts at start* re-derives the rule the run used (any OK document classed UTILITY in earlier rounds). *Calls fed* counts extraction calls whose context included the document; documents share calls, so these numbers are not additive across documents.

## Santiago, Chile — 0d7d4af8 (RESEARCH, AWAITING_REVIEW)

- Rounds 3; searches 32; documents attempted 18 (OK 14); events from job result
- Site-restricted searches: almarwater.com ×3
- Extraction calls 49 (linked to documents 49, no stored bundle 0, by time_window); input 62,476, output 12,386; VALID claims 7

| round | searches | site: targets | hits (retained) | docs (OK) | utility hosts at start | TLDs |
|---|---|---|---|---|---|---|
| 1 | 19 | — | 177 (72) | 6 (4) | — | cl 1, com 2, org 3 |
| 2 | 11 | almarwater.com | 97 (55) | 6 (4) | almarwater.com, riotimesonline.com, aguashorizonte.cl | cl 1, com 4, mx 1 |
| 3 | 2 | — | 20 (20) | 6 (6) | almarwater.com, riotimesonline.com, aguashorizonte.cl | cl 2, co 2, com 2 |

### Documents

| round | host | type | status | found by | calls fed (sole) | tokens in calls fed | fields |
|---|---|---|---|---|---|---|---|
| 1 | aguashorizonte.cl | UTILITY | OK | Santiago Chile water supply aqueduct pipeline transfer | 4 (0) | 8,172 | infrastructure_notes, reservoir_names, water_transfer_system, water_type |
| 1 | almarwater.com | UTILITY | OK | Santiago Chile water supply aqueduct pipeline transfer | 7 (1) | 14,151 | infrastructure_notes, jurisdiction, source_withdrawal_contribution_m3_day, total_city_water_withdrawals_m3_day, utility_name, water_transfer_system, water_type |
| 1 | documents1.worldbank.org | TECHNICAL | OK | Santiago Chile water supply aqueduct pipeline transfer | 13 (4) | 22,294 | city_name, city_population_millions, city_population_reference_year, country, infrastructure_notes, jurisdiction, reservoir_names, source_withdrawal_contribution_m3_day, utility_name, water_quality_notes, water_source, water_system, water_type |
| 1 | doi.org | ACADEMIC | FAILED | Santiago drinking water supply sources | 0 (0) | 0 | — |
| 1 | riotimesonline.com | UTILITY | OK | Santiago Chile water supply aqueduct pipeline transfer | 8 (1) | 15,032 | city_population_reference_year, country, jurisdiction, reservoir_names, seasonal_uses, utility_name, water_source, water_transfer_system |
| 1 | semanticscholar.org | ACADEMIC | EMPTY | Santiago groundwater surface water supply | 0 (0) | 0 | — |
| 2 | almarwater.com | UTILITY | OK | site:almarwater.com water quality report | 9 (1) | 13,493 | city_population_millions, references, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, utility_name, utility_reported_city_population_millions, water_quality_notes, water_transfer_system |
| 2 | almarwater.com | UTILITY | OK | site:almarwater.com water sources | 17 (8) | 24,597 | jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, references, reservoir_names, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, water_quality_notes, water_source, water_system, water_transfer_system |
| 2 | bpmext.almarwater.com | UTILITY | EMPTY | site:almarwater.com water quality report | 0 (0) | 0 | — |
| 2 | dga.mop.gob.cl | GOVERNMENT | OK | Santiago Chile drinking water quality report | 0 (0) | 0 | — |
| 2 | riotimesonline.com | OTHER | OK | Santiago Chile drinking water utility | 7 (1) | 11,204 | city_population_millions, jurisdiction, population_served_millions, reservoir_names, utility_name, utility_reported_city_population_millions, water_source |
| 2 | sinav30.conagua.gob.mx | GOVERNMENT | FAILED | Santiago abastecimiento de agua potable fuentes río captació | 0 (0) | 0 | — |
| 3 | almarwater.com | UTILITY | OK | site:almarwater.com water quality report | 7 (0) | 12,717 | city_population_millions, city_population_reference_year, references, reservoir_names, utility_name, water_quality_notes, water_source |
| 3 | almarwater.com | UTILITY | OK | site:almarwater.com water sources | 9 (6) | 14,027 | past_total_city_water_withdrawals, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, water_quality_notes, water_source, water_transfer_system |
| 3 | dipres.gob.cl | GOVERNMENT | OK | Santiago producción de agua m3 por día informe anual | 0 (0) | 0 | — |
| 3 | ine.gob.cl | GOVERNMENT | OK | Santiago producción de agua m3 por día informe anual | 5 (0) | 9,449 | city_population_millions, city_population_reference_year, references, reservoir_names, water_source |
| 3 | ins.gov.co | GOVERNMENT | OK | Santiago calidad del agua cruda informe | 0 (0) | 0 | — |
| 3 | minvivienda.gov.co | GOVERNMENT | OK | Santiago calidad del agua cruda informe | 1 (0) | 2,008 | water_quality_notes |

## Houston, United States of America — 993b3415 (RESEARCH, AWAITING_REVIEW)

- Rounds 3; searches 26; documents attempted 18 (OK 16); events from job result
- Site-restricted searches: none
- Extraction calls 43 (linked to documents 43, no stored bundle 0, by time_window); input 62,211, output 16,205; VALID claims 28

| round | searches | site: targets | hits (retained) | docs (OK) | utility hosts at start | TLDs |
|---|---|---|---|---|---|---|
| 1 | 19 | — | 0 (0) | 6 (6) | — | ai 1, gov 4, org 1 |
| 2 | 7 | — | 0 (0) | 6 (5) | waterutility.ai, leadingutilities.org | com 2, gov 4 |
| 3 | 0 | — | 0 (0) | 6 (5) | waterutility.ai, leadingutilities.org, greaterhoustonwater.com | gov 2, org 4 |

### Documents

| round | host | type | status | found by | calls fed (sole) | tokens in calls fed | fields |
|---|---|---|---|---|---|---|---|
| 1 | houstontx.gov | GOVERNMENT | OK | Houston United States of America drinking water source surfa | 17 (0) | 34,113 | city_name, city_population_millions, city_population_reference_year, jurisdiction, population_served_millions, references, reservoir_names, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, total_city_water_withdrawals_m3_day, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_quality_notes, water_source, water_type |
| 1 | houstonwaterbills.houstontx.gov | GOVERNMENT | OK | Houston United States of America drinking water utility | 0 (0) | 0 | — |
| 1 | leadingutilities.org | UTILITY | OK | Houston United States of America population served drinking  | 8 (0) | 15,518 | city_population_millions, city_population_reference_year, population_served_millions, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_system, water_type |
| 1 | msdh.ms.gov | GOVERNMENT | OK | Houston United States of America drinking water utility | 19 (4) | 34,436 | city_name, city_population_millions, city_population_reference_year, jurisdiction, past_total_city_water_withdrawals, population_served_millions, references, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, water_quality_notes, water_quality_year, water_source, water_system, water_type |
| 1 | twdb.texas.gov | GOVERNMENT | OK | Houston United States of America drinking water sources rese | 7 (0) | 15,706 | city_name, jurisdiction, reservoir_names, source_withdrawal_contribution_m3_day, utility_name, water_source, water_type |
| 1 | waterutility.ai | UTILITY | OK | Houston United States of America population served drinking  | 1 (0) | 2,560 | utility_name |
| 2 | geopub.epa.gov | REGULATORY | EMPTY | Houston United States of America drinking water intake locat | 0 (0) | 0 | — |
| 2 | greaterhoustonwater.com | UTILITY | OK | Houston United States of America water treatment plant intak | 5 (0) | 9,564 | infrastructure_notes, pod_coordinate_method, pod_latitude, pod_longitude, water_transfer_system |
| 2 | houstontx.gov | GOVERNMENT | OK | Houston United States of America population served drinking  | 2 (0) | 4,870 | reservoir_names, source_withdrawal_contribution_method |
| 2 | houstontx.gov | GOVERNMENT | OK | Houston United States of America water supply aqueduct pipel | 8 (0) | 18,697 | infrastructure_notes, past_total_city_water_withdrawals, reservoir_names, source_withdrawal_contribution_method, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year |
| 2 | sec.gov | GOVERNMENT | OK | Houston United States of America water production annual rep | 18 (3) | 34,873 | city_latitude, city_longitude, infrastructure_notes, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, reservoir_names, seasonal_uses, source_withdrawal_contribution_method, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, water_transfer_system |
| 2 | waterworld.com | REGULATORY | OK | Houston United States of America water treatment plant intak | 4 (0) | 7,922 | infrastructure_notes, pod_coordinate_method, pod_latitude, pod_longitude |
| 3 | houstonpublicworks.org | TECHNICAL | OK | Houston United States of America drinking water source surfa | 4 (0) | 9,777 | past_total_city_water_withdrawals, source_withdrawal_contribution_method, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year |
| 3 | houstonpublicworks.org | TECHNICAL | OK | Houston United States of America population served drinking  | 4 (0) | 9,777 | past_total_city_water_withdrawals, source_withdrawal_contribution_method, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year |
| 3 | houstontx.gov | GOVERNMENT | OK | Houston United States of America water treatment plant intak | 3 (3) | 4,459 | pod_coordinate_method, pod_latitude, pod_longitude |
| 3 | houstontx.gov | GOVERNMENT | OK | Houston United States of America drinking water intake locat | 2 (1) | 4,805 | country, source_withdrawal_contribution_method |
| 3 | msrwa.org | TECHNICAL | FAILED | Houston United States of America water production annual rep | 0 (0) | 0 | — |
| 3 | tapwatersafety.org | TECHNICAL | OK | Houston United States of America drinking water utility | 1 (0) | 3,805 | source_withdrawal_contribution_method |

## Luanda, Angola — 80aafe20 (RESEARCH, AWAITING_REVIEW)

- Rounds 3; searches 23; documents attempted 18 (OK 10); events from job result
- Site-restricted searches: none
- Extraction calls 59 (linked to documents 59, no stored bundle 0, by time_window); input 137,824, output 22,243; VALID claims 18

| round | searches | site: targets | hits (retained) | docs (OK) | utility hosts at start | TLDs |
|---|---|---|---|---|---|---|
| 1 | 19 | — | 146 (61) | 6 (5) | — | cn 1, org 5 |
| 2 | 4 | — | 40 (33) | 6 (4) | worldwater.org | com 2, gov 1, org 3 |
| 3 | 0 | — | 0 (0) | 6 (1) | worldwater.org | com 1, edu 1, fi 1, org 3 |

### Documents

| round | host | type | status | found by | calls fed (sole) | tokens in calls fed | fields |
|---|---|---|---|---|---|---|---|
| 1 | afdb.org | TECHNICAL | FAILED | Luanda Angola drinking water intake location | 0 (0) | 0 | — |
| 1 | documents1.worldbank.org | TECHNICAL | OK | Luanda Angola population served drinking water utility | 43 (0) | 132,460 | city_name, city_population_millions, city_population_reference_year, country, infrastructure_notes, jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, population_served_millions, references, reservoir_names, seasonal_uses, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_quality_notes, water_source, water_system, water_type |
| 1 | documents1.worldbank.org | TECHNICAL | OK | Luanda Angola water supply aqueduct pipeline transfer | 47 (1) | 141,090 | city_latitude, city_longitude, city_name, city_population_millions, city_population_reference_year, country, infrastructure_notes, jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, population_served_millions, references, reservoir_names, seasonal_uses, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, water_quality_notes, water_source, water_system, water_transfer_system, water_type |
| 1 | english.www.gov.cn | GOVERNMENT | OK | Luanda Angola drinking water sources reservoirs | 16 (0) | 41,935 | city_population_millions, country, infrastructure_notes, jurisdiction, pod_coordinate_method, pod_latitude, pod_longitude, reservoir_names, seasonal_uses, source_withdrawal_contribution_m3_day, utility_name, utility_reported_city_population_millions, water_quality_notes, water_source, water_transfer_system, water_type |
| 1 | sadc-gmi.org | TECHNICAL | OK | Luanda Angola drinking water source surface water groundwate | 51 (1) | 149,073 | city_latitude, city_name, city_population_millions, city_population_reference_year, country, infrastructure_notes, jurisdiction, past_total_city_water_withdrawals, pod_latitude, population_served_millions, references, reservoir_names, seasonal_uses, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_quality_notes, water_quality_year, water_source, water_system, water_transfer_system, water_type |
| 1 | worldwater.org | UTILITY | OK | Luanda Angola water supply aqueduct pipeline transfer | 0 (0) | 0 | — |
| 2 | doi.org | ACADEMIC | FAILED | Luanda urban water supply population | 0 (0) | 0 | — |
| 2 | doi.org | ACADEMIC | FAILED | Luanda urban water withdrawals | 0 (0) | 0 | — |
| 2 | eni.com | TECHNICAL | OK | Luanda Angola water production annual report | 29 (2) | 88,573 | city_latitude, city_longitude, city_name, city_population_millions, city_population_reference_year, jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, population_served_millions, reservoir_names, seasonal_uses, source_withdrawal_contribution_m3_day, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_system, water_transfer_system |
| 2 | environmentalpeacebuilding.org | TECHNICAL | OK | Luanda Angola water production annual report | 18 (0) | 63,011 | city_name, city_population_millions, city_population_reference_year, jurisdiction, past_total_city_water_withdrawals, pod_latitude, pod_longitude, population_served_millions, reservoir_names, source_withdrawal_contribution_m3_day, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_reported_city_population_millions, water_system, water_transfer_system |
| 2 | trade.gov | GOVERNMENT | OK | Luanda Angola water production annual report | 13 (0) | 53,382 | city_name, city_population_millions, city_population_reference_year, jurisdiction, past_total_city_water_withdrawals, reservoir_names, source_withdrawal_contribution_m3_day, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, water_transfer_system |
| 2 | wp-afentra-2025.s3.eu-west-2.amazonaws.com | TECHNICAL | OK | Luanda Angola water production annual report | 19 (0) | 69,500 | city_population_millions, city_population_reference_year, jurisdiction, past_total_city_water_withdrawals, population_served_millions, reservoir_names, source_withdrawal_contribution_m3_day, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_system, water_transfer_system |
| 3 | doi.org | ACADEMIC | FAILED | Luanda groundwater surface water supply | 0 (0) | 0 | — |
| 3 | doi.org | ACADEMIC | FAILED | Luanda water intake | 0 (0) | 0 | — |
| 3 | research.fit.edu | ACADEMIC | OK | Luanda Angola drinking water intake location | 8 (0) | 27,769 | jurisdiction, past_total_city_water_withdrawals, population_served_millions, reservoir_names, seasonal_uses, total_city_water_withdrawals_year, utility_reported_city_population_millions, water_system |
| 3 | sciencedirect.com | ACADEMIC | FAILED | Luanda Angola drinking water intake location | 0 (0) | 0 | — |
| 3 | semanticscholar.org | ACADEMIC | EMPTY | Luanda urban water supply population | 0 (0) | 0 | — |
| 3 | theseus.fi | ACADEMIC | FAILED | Luanda drinking water supply sources | 0 (0) | 0 | — |

## Nairobi, Kenya — f622ab6a (RESEARCH, AWAITING_REVIEW)

- Rounds 3; searches 26; documents attempted 18 (OK 13); events from job result
- Site-restricted searches: none
- Extraction calls 53 (linked to documents 53, no stored bundle 0, by time_window); input 86,111, output 23,532; VALID claims 25

| round | searches | site: targets | hits (retained) | docs (OK) | utility hosts at start | TLDs |
|---|---|---|---|---|---|---|
| 1 | 19 | — | 168 (62) | 6 (4) | — | int 1, ke 5 |
| 2 | 7 | — | 70 (52) | 6 (4) | nairobiwater.co.ke | int 1, ke 5 |
| 3 | 0 | — | 0 (0) | 6 (5) | nairobiwater.co.ke | com 1, ke 3, org 2 |

### Documents

| round | host | type | status | found by | calls fed (sole) | tokens in calls fed | fields |
|---|---|---|---|---|---|---|---|
| 1 | kewi.go.ke | GOVERNMENT | OK | Nairobi Kenya drinking water intake location | 2 (1) | 2,599 | population_served_millions, water_quality_notes |
| 1 | nairobiwater.co.ke | UTILITY | OK | Nairobi Kenya drinking water utility | 1 (0) | 1,255 | jurisdiction |
| 1 | nairobiwater.co.ke | UTILITY | OK | Nairobi Kenya drinking water sources reservoirs | 9 (0) | 18,194 | jurisdiction, past_total_city_water_withdrawals, reservoir_names, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, water_source, water_type |
| 1 | water.go.ke | GOVERNMENT | FAILED | Nairobi Kenya water supply aqueduct pipeline transfer | 0 (0) | 0 | — |
| 1 | water.go.ke | GOVERNMENT | FAILED | Nairobi Kenya drinking water utility | 0 (0) | 0 | — |
| 1 | water.igad.int | GOVERNMENT | OK | Nairobi Kenya drinking water source surface water groundwate | 24 (9) | 49,842 | city_name, city_population_millions, city_population_reference_year, infrastructure_notes, jurisdiction, past_total_city_water_withdrawals, references, reservoir_names, seasonal_uses, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, water_quality_notes, water_source, water_system, water_type |
| 2 | nairobiwater.co.ke | UTILITY | FAILED | Nairobi Kenya water production annual report | 0 (0) | 0 | — |
| 2 | nairobiwater.co.ke | UTILITY | OK | Nairobi Kenya drinking water utility | 0 (0) | 0 | — |
| 2 | parliament.go.ke | GOVERNMENT | FAILED | Nairobi Kenya water production annual report | 0 (0) | 0 | — |
| 2 | wasreb.go.ke | GOVERNMENT | OK | Nairobi Kenya water production annual report | 26 (0) | 60,786 | city_name, city_population_millions, city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, population_served_millions, references, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_transfer_system, water_type |
| 2 | wasreb.go.ke | GOVERNMENT | OK | Nairobi Kenya drinking water quality report | 20 (0) | 52,268 | city_population_millions, city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, population_served_millions, references, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_quality_year, water_type |
| 2 | water.igad.int | GOVERNMENT | OK | Nairobi Kenya drinking water source surface water groundwate | 21 (2) | 46,863 | city_latitude, city_longitude, city_name, city_population_millions, city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, references, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, water_quality_year, water_transfer_system, water_type |
| 3 | documents1.worldbank.org | TECHNICAL | FAILED | Nairobi Kenya water production annual report | 0 (0) | 0 | — |
| 3 | nairobiwater.co.ke | UTILITY | OK | Nairobi Kenya drinking water intake location | 1 (0) | 3,500 | utility_name |
| 3 | nature.org | TECHNICAL | OK | Nairobi Kenya water production annual report | 12 (0) | 31,484 | country, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, population_served_millions, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_quality_year |
| 3 | oloolaiserwatercompany.co.ke | TECHNICAL | OK | Nairobi Kenya water production annual report | 11 (0) | 29,442 | past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, population_served_millions, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_quality_year |
| 3 | waterequity.com | TECHNICAL | OK | Nairobi Kenya water production annual report | 7 (0) | 19,000 | country, population_served_millions, total_city_water_withdrawals_year, utility_name, utility_reported_city_population_millions, utility_reported_city_population_reference_year, water_quality_year |
| 3 | wra.go.ke | REGULATORY | OK | Nairobi Kenya drinking water sources reservoirs | 0 (0) | 0 | — |

## Vienna, Austria — e800f614 (RESEARCH, AWAITING_REVIEW)

- Rounds 3; searches 26; documents attempted 18 (OK 16); events from job result
- Site-restricted searches: none
- Extraction calls 50 (linked to documents 50, no stored bundle 0, by time_window); input 74,437, output 16,106; VALID claims 22

| round | searches | site: targets | hits (retained) | docs (OK) | utility hosts at start | TLDs |
|---|---|---|---|---|---|---|
| 1 | 19 | — | 179 (71) | 6 (5) | — | at 4, org 2 |
| 2 | 7 | — | 70 (43) | 6 (5) | leadingutilities.org | at 2, com 1, eu 1, gov 1, org 1 |
| 3 | 0 | — | 0 (0) | 6 (6) | leadingutilities.org | at 2, com 2, info 1, org 1 |

### Documents

| round | host | type | status | found by | calls fed (sole) | tokens in calls fed | fields |
|---|---|---|---|---|---|---|---|
| 1 | bmluk.gv.at | GOVERNMENT | OK | Vienna Austria drinking water source surface water groundwat | 14 (5) | 23,123 | city_population_millions, city_population_reference_year, infrastructure_notes, past_total_city_water_withdrawals, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_year, total_city_water_withdrawals_m3_day, total_city_water_withdrawals_year, utility_name, water_quality_notes, water_source, water_system, water_transfer_system, water_type |
| 1 | bmluk.gv.at | GOVERNMENT | OK | Vienna Austria drinking water source surface water groundwat | 5 (2) | 9,449 | country, jurisdiction, utility_name, water_source, water_type |
| 1 | leadingutilities.org | UTILITY | OK | Vienna Austria population served drinking water utility | 5 (2) | 8,067 | city_name, population_served_millions, utility_name, utility_reported_city_population_millions, water_system |
| 1 | semanticscholar.org | ACADEMIC | EMPTY | Vienna urban water supply population | 0 (0) | 0 | — |
| 1 | wien.gv.at | GOVERNMENT | OK | Vienna Austria drinking water source surface water groundwat | 5 (0) | 8,854 | city_name, infrastructure_notes, utility_name, water_system, water_transfer_system |
| 1 | wien.gv.at | GOVERNMENT | OK | Vienna Austria drinking water sources reservoirs | 12 (2) | 21,815 | city_name, infrastructure_notes, reservoir_names, source_withdrawal_contribution_m3_day, source_withdrawal_contribution_method, total_city_water_withdrawals_m3_day, utility_name, water_quality_notes, water_source, water_system, water_transfer_system, water_type |
| 2 | borealisgroup.com | TECHNICAL | OK | Vienna Austria water production annual report | 22 (5) | 48,772 | city_latitude, city_longitude, city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, references, seasonal_uses, source_withdrawal_contribution_year, total_city_water_withdrawals_year, utility_reported_city_population_reference_year, water_quality_year, water_type |
| 2 | environment.ec.europa.eu | GOVERNMENT | OK | Vienna Austria drinking water quality report | 7 (0) | 17,842 | jurisdiction, past_total_city_water_withdrawals, references, source_withdrawal_contribution_year, total_city_water_withdrawals_year, water_quality_year, water_type |
| 2 | icpdr.org | TECHNICAL | FAILED | Vienna Austria water production annual report | 0 (0) | 0 | — |
| 2 | viennava.gov | GOVERNMENT | OK | Vienna Austria water production annual report | 14 (0) | 32,599 | city_population_millions, city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, references, seasonal_uses, source_withdrawal_contribution_year, total_city_water_withdrawals_year, utility_reported_city_population_reference_year, water_quality_year, water_type |
| 2 | wien.gv.at | GOVERNMENT | OK | Vienna Austria drinking water quality report | 1 (0) | 2,790 | references |
| 2 | wien.gv.at | GOVERNMENT | OK | Vienna Austria drinking water quality report | 4 (0) | 10,353 | city_population_millions, jurisdiction, seasonal_uses, water_type |
| 3 | oe-eb.at | TECHNICAL | OK | Vienna Austria water production annual report | 7 (0) | 21,090 | city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, references, total_city_water_withdrawals_year, utility_reported_city_population_reference_year |
| 3 | omv.com | TECHNICAL | OK | Vienna Austria water production annual report | 5 (0) | 16,608 | jurisdiction, past_total_city_water_withdrawals, references, total_city_water_withdrawals_year, utility_reported_city_population_reference_year |
| 3 | openknowledge.worldbank.org | TECHNICAL | OK | Vienna Austria water production annual report | 7 (0) | 21,090 | city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, references, total_city_water_withdrawals_year, utility_reported_city_population_reference_year |
| 3 | pan-europe.info | TECHNICAL | OK | Vienna Austria drinking water quality report | 5 (0) | 10,754 | past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, references |
| 3 | verbund.com | TECHNICAL | OK | Vienna Austria water production annual report | 12 (2) | 27,059 | city_latitude, city_longitude, city_population_reference_year, country, jurisdiction, past_total_city_water_withdrawals, pod_coordinate_method, pod_latitude, pod_longitude, references, total_city_water_withdrawals_year, utility_reported_city_population_reference_year |
| 3 | wien.gv.at | GOVERNMENT | OK | Vienna Austria population served drinking water utility | 0 (0) | 0 | — |
