"""Semantic validation and safer automatic acceptance (services/semantics.py, the high-risk and
cross-field rules in services/resolution.py, services/structure.py).

Regression: the five values the Houston replay (run 759d7681) accepted automatically although
the evidence doesn't answer the field's question, rebuilt from the stored evidence text and
stored source types. Positive controls: legitimate values that must still be accepted. Negative
controls: what must go to a person instead.
"""

import pytest
from resolution_helpers import FIELDS, YEAR, World, claim
from water_source_research.schemas.common import FieldResolutionStatus as S
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.normalization import normalize_research
from water_source_research.services.resolution import resolve_research
from water_source_research.services.semantics import jurisdiction_level
from water_source_research.services.structure import is_date_line, structural_year

def resolve_city(world, city, country, *claims, fields=FIELDS):
    research = initialize_city_state(city, country)
    for c in claims:
        research.fields[c.field_key].candidate_claims.append(c)
    research = normalize_research(research, fields, world.index, current_year=YEAR)
    return resolve_research(research, fields, world.index)[0]

def reasons(state, key):
    return {r for e in state.fields[key].resolution.entities for r in e.reason_codes}

def check_codes(state, key):
    return {c.code: c.result.value for c in state.fields[key].candidate_claims[0].validation.checks}

# --- Houston: the five false automatic acceptances ----------------------------------------------

HOUSTON = ("Houston", "United States of America")
TWDB = "https://www.twdb.texas.gov/surfacewater/rivers/reservoirs/houston/"
FLIER = "https://www.houstontx.gov/planning/Demographics/demograph_docs/FactsFlier.pdf"
MEMBER = "https://www.leadingutilities.org/members/houston-water"
MEMBER_TEXT = ("Houston Water\nUnited States of America\nJuly 2018\nPopulation served:\n"
               "2,100,000\nInnovation areas\nAbout\nHouston Water’s wastewater system has 39 "
               "WWTP. As a regional service of drinking water, Houston Water’s drinking water "
               "system includes 6 public water systems, 3 surface water plants, 49 ground water "
               "plants and 130 groundwater wells.")

def houston_world() -> World:
    world = World()
    world.doc(TWDB, "Lake Houston (San Jacinto River Basin)\nLake Houston is located four miles "
                    "north of Sheldon and eighteen miles northeast of Houston in Harris County, "
                    "on the San Jacinto River. The lake is owned by the City of Houston.",
              source_type="GOVERNMENT", source_date="1997-01-01")
    world.doc(FLIER, "If the City of Houston were a state, it would\nrank 36th in population—its "
                     "2,016,582\nresidents in 2005 placing it behind Nevada\n(2,414,807) and "
                     "ahead of New Mexico\n(1,928,384).", source_type="GOVERNMENT", page=1,
              source_date="2006-10-19")
    # stored as UTILITY when it was fetched (the replay reused that label)
    world.doc(MEMBER, MEMBER_TEXT, source_type="UTILITY", source_date="2018-07-10")
    return world

def test_harris_county_is_not_accepted_as_the_state():
    quote = ("Lake Houston is located four miles north of Sheldon and eighteen miles northeast "
             "of Houston in Harris County")
    state = resolve_city(houston_world(), *HOUSTON,
                         claim("jurisdiction", "Harris County", (TWDB, quote)))
    assert state.fields["jurisdiction"].status is S.HITL_REQUIRED
    assert "WARN_JURISDICTION_LEVEL" in reasons(state, "jurisdiction")

def test_a_2005_flyer_figure_is_not_accepted_as_city_population():
    state = resolve_city(houston_world(), *HOUSTON, claim("city_population_millions", 2.016582, (
        FLIER, "its 2,016,582 residents in 2005"), page=1))
    assert state.fields["city_population_millions"].status is S.HITL_REQUIRED
    assert "WARN_DESIGNATED_SOURCE_MISSING" in reasons(state, "city_population_millions")
    # the value itself is kept, with its stated year, for the reviewer
    c = state.fields["city_population_millions"].candidate_claims[0]
    assert c.qualifiers.reference_year == 2005 and c.qualifiers.year_basis == "quote"

@pytest.mark.parametrize("year_field, parent", [
    ("city_population_reference_year", "city_population_millions"),
    ("utility_reported_city_population_reference_year",
     "utility_reported_city_population_millions"),
])
def test_a_page_date_is_not_accepted_as_a_population_year(year_field, parent):
    state = resolve_city(houston_world(), *HOUSTON, claim(year_field, 2018, (MEMBER, "July 2018")))
    assert state.fields[year_field].status is S.HITL_REQUIRED
    got = reasons(state, year_field)
    assert "WARN_YEAR_NOT_LINKED" in got
    assert check_codes(state, year_field)["YEAR_NOT_LINKED"] == "WARN"

def test_houston_water_from_a_member_page_is_not_accepted():
    state = resolve_city(houston_world(), *HOUSTON, claim("utility_name", "Houston Water", (
        MEMBER, "Houston Water")))
    assert state.fields["utility_name"].status is S.HITL_REQUIRED
    assert "WARN_UTILITY_SERVICE_NOT_STATED" in reasons(state, "utility_name")

def test_a_utility_accepted_for_one_source_waits_for_its_unsettled_sibling():
    world = World()
    hpw = "https://www.houstonpublicworks.org/drinking-water-operations"
    world.doc(hpw, "Houston Public Works provides drinking water to Houston from the Trinity "
                   "River and from groundwater wells.", source_type="GOVERNMENT")
    world.doc(MEMBER, "Houston Water provides drinking water to Houston from groundwater wells.",
              source_type="UTILITY")
    world.doc(FLIER, "The Water Utility System delivers water to Houston.",
              source_type="GOVERNMENT")
    state = resolve_city(
        world, *HOUSTON,
        claim("water_source", "Trinity River", (hpw, "the Trinity River")),
        claim("water_source", "Groundwater wells", (hpw, "groundwater wells")),
        claim("utility_name", "Houston Water",
              (MEMBER, "Houston Water provides drinking water to Houston from groundwater")),
        claim("utility_name", "Water Utility System",
              (FLIER, "The Water Utility System delivers water to Houston")))
    entities = {e.entity_key: e for e in state.fields["utility_name"].resolution.entities}
    assert all(e.status is not S.RESOLVED for e in entities.values())
    assert any("SIBLING_UNSETTLED" in e.reason_codes for e in entities.values())

# --- positive controls: still accepted ---------------------------------------------------------

def test_jurisdictions_at_the_first_level_are_accepted():
    world = World()
    ontario = world.doc("https://www.ontario.ca/toronto", "Toronto, Ontario is the largest city "
                        "in Canada.", source_type="GOVERNMENT")
    state = resolve_city(world, "Toronto", "Canada", claim("jurisdiction", "Ontario", (
        ontario, "Toronto, Ontario is the largest city")))
    assert state.fields["jurisdiction"].status is S.RESOLVED
    texas = resolve_city(houston_world(), *HOUSTON, claim("jurisdiction", "Texas", (
        FLIER, "If the City of Houston were a state")))
    assert texas.fields["jurisdiction"].status is not S.RESOLVED  # "Texas" isn't in that quote
    world.doc(FLIER + "#p0", "As the 4th largest city in the U.S., Houston, Texas is a leader in "
                             "business.", source_type="GOVERNMENT")
    texas = resolve_city(world, *HOUSTON, claim("jurisdiction", "Texas", (
        FLIER + "#p0", "Houston, Texas is a leader in business")))
    assert texas.fields["jurisdiction"].status is S.RESOLVED

def test_a_county_is_first_level_in_kenya():
    world = World()
    url = world.doc("https://nairobi.go.ke/about", "Nairobi is the capital of Kenya and lies in "
                    "Nairobi County.", source_type="GOVERNMENT")
    state = resolve_city(world, "Nairobi", "Kenya", claim("jurisdiction", "Nairobi County", (
        url, "Nairobi is the capital of Kenya and lies in Nairobi County")))
    assert state.fields["jurisdiction"].status is S.RESOLVED

def test_a_utility_stated_to_serve_the_city_is_accepted():
    world = World()
    url = world.doc("https://www.toronto.ca/services/water", "Toronto Water provides drinking "
                    "water to Toronto.", source_type="GOVERNMENT")
    state = resolve_city(world, "Toronto", "Canada", claim("utility_name", "Toronto Water", (
        url, "Toronto Water provides drinking water")))
    assert state.fields["utility_name"].status is S.RESOLVED
    assert check_codes(state, "utility_name")["UTILITY_SERVES_CITY"] == "PASS"

def test_a_current_withdrawal_with_its_year_in_a_table_header_is_accepted():
    world = World()
    url = world.doc("https://www.toronto.ca/water-report-2025.pdf",
                    "Toronto Water Annual Report\nTable 3: Raw water withdrawn by Toronto, 2025\n"
                    "Plant      Volume (m3/d)\nTotal      980,000", source_type="GOVERNMENT",
                    page=4)
    state = resolve_city(world, "Toronto", "Canada", claim(
        "total_city_water_withdrawals_m3_day", 980000, (url, "Total      980,000"), page=4),
        claim("total_city_water_withdrawals_year", 2025,
              (url, "Table 3: Raw water withdrawn by Toronto, 2025"), page=4))
    fs = state.fields["total_city_water_withdrawals_m3_day"]
    assert fs.status is S.RESOLVED, reasons(state, "total_city_water_withdrawals_m3_day")
    q = fs.selected_claim.qualifiers
    assert q.reference_year == 2025 and q.year_basis in ("heading", "passage")
    year = state.fields["total_city_water_withdrawals_year"]
    assert year.status is S.RESOLVED, reasons(state, "total_city_water_withdrawals_year")

def test_a_utility_reported_population_and_its_year_are_accepted():
    world = World()
    url = world.doc("https://www.torontowater.example/about", "Toronto Water serves Toronto. "
                    "In 2025 Toronto Water reported a population of 2,900,000 residents in "
                    "Toronto.", source_type="UTILITY")
    state = resolve_city(
        world, "Toronto", "Canada",
        claim("utility_name", "Toronto Water", (url, "Toronto Water serves Toronto")),
        claim("utility_reported_city_population_millions", 2.9, (
            url, "In 2025 Toronto Water reported a population of 2,900,000 residents")),
        claim("utility_reported_city_population_reference_year", 2025, (
            url, "In 2025 Toronto Water reported a population of 2,900,000 residents")))
    for key in ("utility_reported_city_population_millions",
                "utility_reported_city_population_reference_year"):
        assert state.fields[key].status is S.RESOLVED, (key, reasons(state, key))

# --- negative controls: go to a person ---------------------------------------------------------

@pytest.mark.parametrize("text, code", [
    ("In 2025 Toronto's plants produced 980,000 m3/d of drinking water.",
     "MEASUREMENT_TYPE_MISMATCH"),
    ("In 2025 Toronto's plants had a treatment capacity of 980,000 m3/d.",
     "MEASUREMENT_TYPE_MISMATCH"),
    ("In 2025 Toronto used 980,000 m3/d.", "MEASUREMENT_TYPE_UNKNOWN"),
])
def test_production_capacity_or_unstated_volumes_are_not_withdrawals(text, code):
    world = World()
    url = world.doc("https://www.toronto.ca/report", text, source_type="GOVERNMENT")
    quote = text.rstrip(".")
    state = resolve_city(world, "Toronto", "Canada",
                         claim("total_city_water_withdrawals_m3_day", 980000, (url, quote)))
    assert state.fields["total_city_water_withdrawals_m3_day"].status is S.HITL_REQUIRED
    assert f"WARN_{code}" in reasons(state, "total_city_water_withdrawals_m3_day")

def test_service_area_population_is_flagged_for_city_population():
    world = World()
    url = world.doc("https://www.toronto.ca/water", "In 2025 Toronto Water served 3,600,000 "
                    "customers in Toronto and parts of York Region.", source_type="GOVERNMENT")
    state = resolve_city(world, "Toronto", "Canada", claim("city_population_millions", 3.6, (
        url, "In 2025 Toronto Water served 3,600,000 customers in Toronto")))
    assert check_codes(state, "city_population_millions")["SERVICE_AREA_POPULATION"] == "WARN"
    assert state.fields["city_population_millions"].status is S.HITL_REQUIRED

def test_a_utility_website_that_never_says_it_serves_the_city():
    world = World()
    url = world.doc("https://almarwater.example/about", "Almar Water Solutions builds "
                    "desalination plants worldwide.", source_type="UTILITY")
    state = resolve_city(world, "Santiago", "Chile", claim("utility_name", "Almar Water Solutions",
                                                           (url, "Almar Water Solutions builds")))
    assert "WARN_UTILITY_SERVICE_NOT_STATED" in reasons(state, "utility_name")

def test_a_publication_date_never_establishes_how_current_a_withdrawal_is():
    world = World()
    url = world.doc("https://www.toronto.ca/water", "Toronto's intakes withdraw 980,000 m3/d of "
                    "raw water.", source_type="GOVERNMENT", source_date="2025-05-01")
    state = resolve_city(world, "Toronto", "Canada", claim(
        "total_city_water_withdrawals_m3_day", 980000,
        (url, "Toronto's intakes withdraw 980,000 m3/d of raw water")))
    got = reasons(state, "total_city_water_withdrawals_m3_day")
    assert {"WARN_MEASUREMENT_YEAR", "WARN_FRESHNESS_UNKNOWN"} <= got
    c = state.fields["total_city_water_withdrawals_m3_day"].candidate_claims[0]
    assert c.qualifiers.year_basis == "document"  # kept, but never a measurement year

def test_an_old_withdrawal_is_kept_but_flagged_as_possibly_superseded():
    world = World()
    url = world.doc("https://www.toronto.ca/water", "In 2015 Toronto's intakes withdrew "
                    "1,100,000 m3/d of raw water.", source_type="GOVERNMENT")
    state = resolve_city(world, "Toronto", "Canada", claim(
        "total_city_water_withdrawals_m3_day", 1100000,
        (url, "In 2015 Toronto's intakes withdrew 1,100,000 m3/d of raw water")))
    fs = state.fields["total_city_water_withdrawals_m3_day"]
    assert fs.status is S.HITL_REQUIRED and fs.candidate_claims
    assert "WARN_POSSIBLY_SUPERSEDED" in reasons(state, "total_city_water_withdrawals_m3_day")

def test_a_year_without_its_measurement_is_not_accepted():
    world = World()
    url = world.doc("https://www.toronto.ca/water", "In 2025 Toronto's intakes withdrew "
                    "980,000 m3/d of raw water. The 2015 master plan is online.",
                    source_type="GOVERNMENT")
    state = resolve_city(world, "Toronto", "Canada",
                         claim("total_city_water_withdrawals_year", 2015,
                               (url, "The 2015 master plan is online")))
    assert state.fields["total_city_water_withdrawals_year"].status is S.HITL_REQUIRED
    got = reasons(state, "total_city_water_withdrawals_year")
    assert "WARN_YEAR_NOT_LINKED" in got

def test_a_country_without_a_configured_hierarchy_goes_to_review():
    code, result, _ = jurisdiction_level("Atlantic Province", "Atlantis")
    assert (code, result.value) == ("JURISDICTION_LEVEL_UNKNOWN", "WARN")

# --- structure: tables, headings, captions, records -------------------------------------------

@pytest.mark.parametrize("text, quote, hint, expected", [
    ("Year  Withdrawal (m3/d)\n2023  950,000", "2023  950,000", "950,000", (2023, "table")),
    ("Raw water withdrawals\nPlant  2024  2025\nTotal  950,000  980,000",
     "Total  950,000  980,000", "980,000", (2025, "table")),
    ("Raw water withdrawals\nPlant  2024  2025\nTotal  950,000  980,000",
     "Total  950,000  980,000", "950,000", (2024, "table")),
    ("Figure 2. Withdrawals by source, 2022\nLake Ontario 950,000 m3/d", "Lake Ontario 950,000",
     "950,000", (2022, "heading")),
    ("Source: Lake Ontario\nYear: 2023\nVolume: 950,000 m3/d", "Volume: 950,000 m3/d",
     "950,000", (2023, "record")),
    ("Reporting period: 2022-2023\nVolume: 950,000 m3/d", "Volume: 950,000", "950,000",
     (2023, "record")),
])
def test_structural_years(text, quote, hint, expected):
    link = structural_year(text, quote, hint)
    assert (link.year, link.basis) == expected

def test_an_unaligned_multi_year_header_is_ambiguous():
    link = structural_year("Withdrawals 2024 2025\nTotal 950,000 980,000",
                           "Total 950,000 980,000", "950,000")
    assert link.year is None and link.ambiguous

def test_publication_dates_are_never_measurement_years():
    for line in ("July 2018", "10 July 2018", "Updated: 12 March 2024", "© 2021 Houston Water",
                 "Published 2019-04-02", "15/03/2024", "marzo de 2023"):
        assert is_date_line(line), line
    for line in ("Table 3: Raw water withdrawals, 2025", "Year: 2023", "2023  950,000"):
        assert not is_date_line(line), line
    assert structural_year("July 2018\nPopulation served:\n2,100,000", "2,100,000").year is None
