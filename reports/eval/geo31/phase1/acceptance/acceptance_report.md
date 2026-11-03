# Automatic acceptance: before / after Milestone 1

Before = today's rules before the change (normalize-3 / resolve-3); after = normalize-4 / resolve-4. Both are revalidations of the same stored runs (claims as extracted, the same evidence; no discovery, fetching or model calls) evaluated against the same reference dataset and comparison policy with the semantic judge in cache-only mode (no new model calls; judged cells stay provisional). AUTO = the values the system accepted on its own, before any person.

## Per city

| City | Coverage before | Coverage after | Strict precision before | Strict after | Lenient before | Lenient after |
|---|---|---|---|---|---|---|
| Houston | 1/49 | 1/49 | 1/1 (100%) | 1/1 (100%) | 100% | 100% |
| Nairobi | 7/58 | 7/58 | 7/7 (100%) | 7/7 (100%) | 100% | 100% |
| Vienna | 2/58 | 2/58 | 2/2 (100%) | 2/2 (100%) | 100% | 100% |
| Luanda | 0/31 | 0/31 | 0/0 (n/a) | 0/0 (n/a) | n/a | n/a |
| Toronto | 4/27 | 4/27 | 4/4 (100%) | 4/4 (100%) | 100% | 100% |
| Madrid | 0/109 | 0/109 | 0/0 (n/a) | 0/0 (n/a) | n/a | n/a |
| Lisbon | 0/83 | 0/83 | 0/0 (n/a) | 0/0 (n/a) | n/a | n/a |
| Hyderabad | 1/94 | 1/94 | 1/1 (100%) | 1/1 (100%) | 100% | 100% |
| **All evaluated cities** | 15/509 | 15/509 | 15/15 (100%) | 15/15 (100%) | 100% | 100% |

Coverage = AUTO answers / comparable cells; precision = correct / answered (lenient counts a partial match as half).

## Per field (all evaluated cities)

| Field | Coverage before | Strict before | Lenient before | Coverage after | Strict after | Lenient after |
|---|---|---|---|---|---|---|
| City Name | 4/8 (50%) | 4/4 (100%) | 4/4 (100%) | 4/8 (50%) | 4/4 (100%) | 4/4 (100%) |
| State/Province | 1/8 (12%) | 1/1 (100%) | 1/1 (100%) | 1/8 (12%) | 1/1 (100%) | 1/1 (100%) |
| Country | 3/8 (38%) | 3/3 (100%) | 3/3 (100%) | 3/8 (38%) | 3/3 (100%) | 3/3 (100%) |
| Utility Name | 2/13 (15%) | 2/2 (100%) | 2/2 (100%) | 2/13 (15%) | 2/2 (100%) | 2/2 (100%) |
| Water Source | 3/34 (9%) | 3/3 (100%) | 3/3 (100%) | 3/34 (9%) | 3/3 (100%) | 3/3 (100%) |
| Water Type | 2/30 (7%) | 2/2 (100%) | 2/2 (100%) | 2/30 (7%) | 2/2 (100%) | 2/2 (100%) |

Fields with no automatic answer before or after are omitted.

## Previously correct automatic values that were lost (0)

Each needs a rule from the Standardization Rules; one without is a bug.

None.

## Wrong automatic values no longer accepted (0)

None.

## Positive controls: correct values still accepted (15)

| City | Field | Record | Value | Reference | Before → after | Why |
|---|---|---|---|---|---|---|
| Houston | City Name | — | Houston | Houston | MATCH → MATCH | — |
| Nairobi | City Name | — | Nairobi | Nairobi | MATCH → MATCH | — |
| Nairobi | Country | — | Kenya | Kenya | MATCH → MATCH | — |
| Nairobi | State/Province | — | Nairobi County | Nairobi City County | MATCH → MATCH | — |
| Nairobi | Utility Name | — | Nairobi Water Company | Nairobi City Water And Sewerage Company | MATCH → MATCH | — |
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

## Automatic decisions that moved (0)

Per field and entity in the revalidation runs (also for cities without a reference).

| City | Field | Entity | Move | Reasons |
|---|---|---|---|---|

## Stored documents re-checked under current source-type rules (25)

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
| Hyderabad | UTILITY → OTHER | https://www.deccanchronicle.com/southern-states/telangana/water-board-prepares-master-plan-for-2047-1896895 |
| Hyderabad | TECHNICAL → OTHER | https://hydnews.net/hyderabad-water-quality-report-2026/ |
| Hyderabad | TECHNICAL → OTHER | https://www.wabag.com/wp-content/uploads/2026/07/Annual-Report-2026.pdf |
| Hyderabad | TECHNICAL → OTHER | https://www.varunbeverages.com/Annual-Report-FY-2025.pdf |

## Reproducibility

- Houston: evaluations 24c9656a → c8fc9ea2, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 2 / 2
- Nairobi: evaluations 682e8ed3 → 180978b1, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Vienna: evaluations ca1aaf51 → bb489821, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Luanda: evaluations fef46879 → 976f92aa, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Toronto: evaluations 21035b8a → 815cb826, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Madrid: evaluations 13a88cb2 → edd1e77e, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Lisbon: evaluations 1f44cda4 → 20e1aa1c, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 0 / 0
- Hyderabad: evaluations 4d3c1092 → b51ce99b, dataset a7f6cbde, compare compare-7/compare-7, judge new calls 0 / 0, cache misses 15 / 0
