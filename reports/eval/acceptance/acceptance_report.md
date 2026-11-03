# Automatic acceptance: before / after Milestone 1

Before = today's rules before the change (normalize-3 / resolve-3); after = normalize-4 / resolve-4. Both are revalidations of the same stored runs (claims as extracted, the same evidence; no discovery, fetching or model calls) evaluated against the same reference dataset and comparison policy with the semantic judge in cache-only mode (no new model calls; judged cells stay provisional). AUTO = the values the system accepted on its own, before any person.

## Per city

| City | Coverage before | Coverage after | Strict precision before | Strict after | Lenient before | Lenient after |
|---|---|---|---|---|---|---|
| Houston | 6/49 | 1/49 | 1/6 (17%) | 1/1 (100%) | 17% | 100% |
| Nairobi | 7/58 | 6/58 | 7/7 (100%) | 6/6 (100%) | 100% | 100% |
| Vienna | 5/58 | 2/58 | 4/5 (80%) | 2/2 (100%) | 80% | 100% |
| Luanda | 0/31 | 0/31 | 0/0 (n/a) | 0/0 (n/a) | n/a | n/a |
| Toronto | 4/27 | 4/27 | 4/4 (100%) | 4/4 (100%) | 100% | 100% |
| Madrid | 2/109 | 0/109 | 2/2 (100%) | 0/0 (n/a) | 100% | n/a |
| Lisbon | 0/83 | 0/83 | 0/0 (n/a) | 0/0 (n/a) | n/a | n/a |
| Hyderabad | 1/94 | 1/94 | 1/1 (100%) | 1/1 (100%) | 100% | 100% |
| **All evaluated cities** | 25/509 | 14/509 | 19/25 (76%) | 14/14 (100%) | 76% | 100% |

Coverage = AUTO answers / comparable cells; precision = correct / answered (lenient counts a partial match as half).

## Per field (all evaluated cities)

| Field | Coverage before | Strict before | Lenient before | Coverage after | Strict after | Lenient after |
|---|---|---|---|---|---|---|
| City Name | 5/8 (62%) | 5/5 (100%) | 5/5 (100%) | 4/8 (50%) | 4/4 (100%) | 4/4 (100%) |
| State/Province | 2/8 (25%) | 1/2 (50%) | 1/2 (50%) | 1/8 (12%) | 1/1 (100%) | 1/1 (100%) |
| Country | 3/8 (38%) | 3/3 (100%) | 3/3 (100%) | 3/8 (38%) | 3/3 (100%) | 3/3 (100%) |
| City Population (millions) | 2/8 (25%) | 1/2 (50%) | 1/2 (50%) | 0/8 (0%) | 0/0 (n/a) | 0/0 (n/a) |
| City Population Reference Date | 1/8 (12%) | 0/1 (0%) | 0/1 (0%) | 0/8 (0%) | 0/0 (n/a) | 0/0 (n/a) |
| Utility Reported Reference Date | 1/6 (17%) | 0/1 (0%) | 0/1 (0%) | 0/6 (0%) | 0/0 (n/a) | 0/0 (n/a) |
| Utility Name | 5/13 (38%) | 4/5 (80%) | 4/5 (80%) | 1/13 (8%) | 1/1 (100%) | 1/1 (100%) |
| Water Source | 3/34 (9%) | 3/3 (100%) | 3/3 (100%) | 3/34 (9%) | 3/3 (100%) | 3/3 (100%) |
| Water Type | 2/30 (7%) | 2/2 (100%) | 2/2 (100%) | 2/30 (7%) | 2/2 (100%) | 2/2 (100%) |
| Total City Water Withdrawals (m3/d) | 1/8 (12%) | 0/1 (0%) | 0/1 (0%) | 0/8 (0%) | 0/0 (n/a) | 0/0 (n/a) |

Fields with no automatic answer before or after are omitted.

## Previously correct automatic values that were lost (5)

Each needs a rule from the Standardization Rules; one without is a bug.

| City | Field | Record | Value | Reference | Before → after | Why |
|---|---|---|---|---|---|---|
| Nairobi | Utility Name | — | Nairobi Water Company | Nairobi City Water And Sewerage Company | MATCH → MISSING_PREDICTION | UTILITY_SERVES_CITY: Col. 12 is the utility 'that operates the water supply system for the city'; the evidence must show it serves the city.<br>UTILITY_SERVICE_NOT_STATED: Col. 12: no evidence in the run states the utility serves the city (an official site or the UTILITY label alone isn't enough). |
| Vienna | City Population (millions) | — | 2.0 | 2.028289 | MATCH → MISSING_PREDICTION | DESIGNATED_SOURCE: City Population (col. 6-7) comes from the designated dataset.<br>MEASUREMENT_YEAR: Col. 7/9/25/27: the year the measurement refers to; a publication date doesn't establish it.<br>DESIGNATED_SOURCE_MISSING: City Population (col. 6-7) is 'based on a list provided in the Appendix'; no designated population dataset is configured, so a person checks it. |
| Vienna | Utility Name | — | Vienna Water | Vienna Water   | MATCH → MISSING_PREDICTION | NO_OFFICIAL_SOURCE: Not cited from a source type authoritative for the field (after re-checking stored source types under current rules). |
| Madrid | City Name | — | Madrid | Madrid | MATCH → MISSING_PREDICTION | NO_OFFICIAL_SOURCE: Not cited from a source type authoritative for the field (after re-checking stored source types under current rules). |
| Madrid | Utility Name | — | Canal de Isabel II | Canal de Isabel II | MATCH → MISSING_PREDICTION | NO_OFFICIAL_SOURCE: Not cited from a source type authoritative for the field (after re-checking stored source types under current rules). |

## Wrong automatic values no longer accepted (6)

| City | Field | Record | Value | Reference | Before → after | Why |
|---|---|---|---|---|---|---|
| Houston | City Population (millions) | — | 2.016582 | 2.39 | TEMPORAL_MISMATCH → MISSING_PREDICTION | DESIGNATED_SOURCE: City Population (col. 6-7) comes from the designated dataset.<br>DESIGNATED_SOURCE_MISSING: City Population (col. 6-7) is 'based on a list provided in the Appendix'; no designated population dataset is configured, so a person checks it. |
| Houston | City Population Reference Date | — | 2018 | 2024.0 | MISMATCH → MISSING_PREDICTION | DESIGNATED_SOURCE: City Population (col. 6-7) comes from the designated dataset.<br>YEAR_LINKED: Col. 7/9/25/27/31: the year must be the measurement's, tied to it in the evidence.<br>NO_OFFICIAL_SOURCE: Not cited from a source type authoritative for the field (after re-checking stored source types under current rules).<br>DESIGNATED_SOURCE_MISSING: City Population (col. 6-7) is 'based on a list provided in the Appendix'; no designated population dataset is configured, so a person checks it.<br>YEAR_NOT_LINKED: Col. 7/9/25/27/31: the year isn't tied to the measurement (a page or publication date never is). |
| Houston | State/Province | — | Harris County | Texas | MISMATCH → MISSING_PREDICTION | JURISDICTION_LEVEL: Col. 2 is 'the state the city is located' in; a lower-level unit (per the country's hierarchy) is not. |
| Houston | Utility Name | — | Houston Water | Houston Public Works | MISMATCH → MISSING_PREDICTION | UTILITY_SERVES_CITY: Col. 12 is the utility 'that operates the water supply system for the city'; the evidence must show it serves the city.<br>NO_OFFICIAL_SOURCE: Not cited from a source type authoritative for the field (after re-checking stored source types under current rules).<br>UTILITY_SERVICE_NOT_STATED: Col. 12: no evidence in the run states the utility serves the city (an official site or the UTILITY label alone isn't enough). |
| Houston | Utility Reported Reference Date | — | 2018 | 2025.0 | MISMATCH → MISSING_PREDICTION | YEAR_LINKED: Col. 7/9/25/27/31: the year must be the measurement's, tied to it in the evidence.<br>NO_OFFICIAL_SOURCE: Not cited from a source type authoritative for the field (after re-checking stored source types under current rules).<br>YEAR_NOT_LINKED: Col. 7/9/25/27/31: the year isn't tied to the measurement (a page or publication date never is). |
| Vienna | Total City Water Withdrawals (m3/d) | — | 390000.0 | 217000.0 | MISMATCH → MISSING_PREDICTION | FRESHNESS: Col. 25/27/31: 'Report the most current ... data'.<br>MEASUREMENT_TYPE: Col. 24/26 are 'water withdrawals'; the figure must be stated as water withdrawn.<br>MEASUREMENT_YEAR: Col. 7/9/25/27: the year the measurement refers to; a publication date doesn't establish it.<br>FRESHNESS_UNKNOWN: Col. 25/27/31: 'Report the most current ... data'; how current the value is can't be established.<br>MEASUREMENT_TYPE_MISMATCH: Col. 24/26 are withdrawals; production, treatment, delivery, consumption or capacity figures are not. |

## Positive controls: correct values still accepted (14)

| City | Field | Record | Value | Reference | Before → after | Why |
|---|---|---|---|---|---|---|
| Houston | City Name | — | Houston | Houston | MATCH → MATCH | — |
| Nairobi | City Name | — | Nairobi | Nairobi | MATCH → MATCH | — |
| Nairobi | Country | — | Kenya | Kenya | MATCH → MATCH | — |
| Nairobi | State/Province | — | Nairobi County | Nairobi City County | MATCH → MATCH | — |
| Nairobi | Water Source | ruiru river | Ruiru River | Ruiru River | MATCH → MATCH | — |
| Nairobi | Water Source | thika river | Thika River | Thika River | MATCH → MATCH | — |
| Nairobi | Water Type | — | Surface | Surface | MATCH → MATCH | — |
| Vienna | City Name | — | Vienna | Vienna | MATCH → MATCH | — |
| Vienna | Country | — | Austria | Austria | MATCH → MATCH | — |
| Toronto | City Name | — | Toronto | Toronto | MATCH → MATCH | — |
| Toronto | Utility Name | lake ontario | Toronto Water | Toronto Water | MATCH → MATCH | — |
| Toronto | Water Source | lake ontario | Lake Ontario | Lake Ontario | MATCH → MATCH | — |
| Toronto | Water Type | lake ontario | Surface | Surface | MATCH → MATCH | — |
| Hyderabad | Country | — | India | India | MATCH → MATCH | — |

## Wrong or partial values still accepted (0)

These pass every rule; the reason they still pass is in the evidence shown in the evaluation's field view.

None.

## Newly accepted (0)

None.

## Automatic decisions that moved (15)

Per field and entity in the revalidation runs (also for cities without a reference).

| City | Field | Entity | Move | Reasons |
|---|---|---|---|---|
| Houston | City Population (millions) | city | accepted->review | NOT_ESTABLISHED_DESIGNATED_SOURCE, WARN_DESIGNATED_SOURCE_MISSING |
| Houston | City Population Reference Date | city | accepted->review | NOT_ESTABLISHED_DESIGNATED_SOURCE, NOT_ESTABLISHED_YEAR_LINKED, NO_OFFICIAL_SOURCE, WARN_DESIGNATED_SOURCE_MISSING, WARN_YEAR_NOT_LINKED |
| Houston | State/Province | city | accepted->review | NOT_ESTABLISHED_JURISDICTION_LEVEL, WARN_JURISDICTION_LEVEL |
| Houston | Population Served (millions) | groundwater | accepted->review | NO_OFFICIAL_SOURCE |
| Houston | Utility Name | groundwater | accepted->review | NOT_ESTABLISHED_UTILITY_SERVES_CITY, NO_OFFICIAL_SOURCE, WARN_UTILITY_SERVICE_NOT_STATED |
| Houston | Utility Reported Reference Date | city | accepted->review | NOT_ESTABLISHED_YEAR_LINKED, NO_OFFICIAL_SOURCE, WARN_YEAR_NOT_LINKED |
| Houston | Water Quality Date | eutaw aquifer | accepted->review | PARENT_NOT_ACCEPTED |
| Nairobi | Source Water Withdrawal Contribution Date | groundwater | accepted->review | PARENT_NOT_ACCEPTED |
| Nairobi | Utility Name | * | accepted->review | NOT_ESTABLISHED_UTILITY_SERVES_CITY, WARN_UTILITY_SERVICE_NOT_STATED |
| Vienna | City Population (millions) | city | accepted->review | NOT_ESTABLISHED_DESIGNATED_SOURCE, NOT_ESTABLISHED_MEASUREMENT_YEAR, WARN_DESIGNATED_SOURCE_MISSING, WARN_MEASUREMENT_YEAR |
| Vienna | Total City Water Withdrawals (m3/d) | city | accepted->review | NOT_ESTABLISHED_FRESHNESS, NOT_ESTABLISHED_MEASUREMENT_TYPE, NOT_ESTABLISHED_MEASUREMENT_YEAR, WARN_FRESHNESS_UNKNOWN, WARN_MEASUREMENT_TYPE_MISMATCH, WARN_MEASUREMENT_YEAR |
| Vienna | Utility Name | * | accepted->review | NO_OFFICIAL_SOURCE |
| Vienna | Utility Reported City Population | city | accepted->review | NOT_ESTABLISHED_MEASUREMENT_TERM, NOT_ESTABLISHED_MEASUREMENT_YEAR, NO_OFFICIAL_SOURCE, WARN_MEASUREMENT_TERM, WARN_MEASUREMENT_YEAR |
| Madrid | City Name | city | accepted->review | NO_OFFICIAL_SOURCE |
| Madrid | Utility Name | * | accepted->review | NO_OFFICIAL_SOURCE |

## Stored documents re-checked under current source-type rules (23)

The original runs keep their stored labels; only the revalidation copies change. A value whose only official-looking source was relabelled goes to review (NO_OFFICIAL_SOURCE).

| City | From → to | Document |
|---|---|---|
| Houston | UTILITY → OTHER | https://www.waterutility.ai/utilities/texas |
| Houston | UTILITY → OTHER | https://www.leadingutilities.org/members/houston-water |
| Houston | REGULATORY → OTHER | https://www.waterworld.com/drinking-water-treatment/distribution/press-release/14279752/mccarthy-completes-houston-intake-pump-station |
| Houston | REGULATORY → GOVERNMENT | https://geopub.epa.gov/DWWidgetApp/ |
| Vienna | UTILITY → OTHER | https://www.leadingutilities.org/members/vienna-water |
| Vienna | TECHNICAL → OTHER | https://www.borealisgroup.com/storage/Company/About-Borealis/Annual-Report/Borealis-Group-Annual-Report-2025-EN.pdf |
| Vienna | TECHNICAL → OTHER | https://www.icpdr.org/sites/default/files/2024-01/ICPDR_Annual-Report-2022_accessible_LNF.pdf |
| Vienna | TECHNICAL → OTHER | https://www.oe-eb.at/dam/jcr:79df32b5-9322-47be-8226-44ca50dcef1c/OeEB-Annual-Report-2025.pdf |
| Vienna | TECHNICAL → OTHER | https://openknowledge.worldbank.org/entities/publication/c5786509-9fb9-48ca-bea2-d1a8c9134816 |
| Vienna | TECHNICAL → OTHER | https://www.verbund.com/media/hq2nhfvp/verbund-annual-financial-report-2025-englisch.pdf |
| Luanda | UTILITY → OTHER | https://www.worldwater.org/conflict/list/ |
| Luanda | TECHNICAL → OTHER | https://wp-afentra-2025.s3.eu-west-2.amazonaws.com/media/2026/05/Afentra-Annual-Report-2025.pdf |
| Luanda | TECHNICAL → OTHER | https://www.eni.com/content/dam/enicom/documents/eng/reports/2025/ar-2025/Annual-Report-2025.pdf |
| Madrid | UTILITY → OTHER | https://www.leadingutilities.org/members/canal-de-isabel-ii |
| Lisbon | GOVERNMENT → REGULATORY | https://economy-finance.ec.europa.eu/economic-surveillance-eu-member-states/country-pages-including-country-reports/country-report-portugal_en |
| Lisbon | TECHNICAL → GOVERNMENT | https://cityoflisbon.net/wp-content/uploads/2025/06/Lisbon-2024-CCR.pdf |
| Lisbon | TECHNICAL → GOVERNMENT | https://cityoflisbon.net/wp-content/uploads/2024/05/CCREPORT23.pdf |
| Lisbon | TECHNICAL → OTHER | https://www.epal.pt/EPAL/en/menu/epal/publications/water-quality-report |
| Lisbon | TECHNICAL → OTHER | https://edp.com/sites/default/files/document/2025-06/IntegratedAnnualReport2024_Unofficial%20Version_Unaudited_0.pdf |
| Lisbon | TECHNICAL → OTHER | https://www.typsa.com/wp-content/uploads/InformeAnual_2026_EN.pdf |
| Lisbon | TECHNICAL → OTHER | https://mowi.com/wp-content/uploads/2025/05/Mowi-Annual-Report-2025.pdf |
| Hyderabad | TECHNICAL → OTHER | https://www.wabag.com/wp-content/uploads/2026/07/Annual-Report-2026.pdf |
| Hyderabad | TECHNICAL → OTHER | https://www.varunbeverages.com/Annual-Report-FY-2025.pdf |

## Reproducibility

- Houston: evaluations 27c2f913 → b80ac97a, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 2
- Nairobi: evaluations 8f8b0c20 → f61c64e5, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Vienna: evaluations 9430cfa8 → 6dbac814, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Luanda: evaluations c69522de → 8713fb77, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Toronto: evaluations 645eeefb → 8e11bd77, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Madrid: evaluations 57ee659f → f7b76d49, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Lisbon: evaluations d74fdd47 → 9e05b934, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Hyderabad: evaluations 935957bd → c675ae24, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 15 / 15
- Santiago: no reference in the dataset (decision moves only)
