"""Deterministic claim normalization and validation (services/normalization.py)."""

import pytest
from resolution_helpers import FIELDS, YEAR, World, checks, claim, normalize, normalized_claim
from water_source_research.schemas.common import ValueStatus
from water_source_research.services.normalization import (
    EvidenceIndex, claim_uid, normalize_research, quantities,
)
from water_source_research.services.rules_engine import days_in_year

URL = "https://www.toronto.ca/water/report"
W = "total_city_water_withdrawals_m3_day"

def one(world: World, text: str, field_key: str, value, source_type="UTILITY", **kw):
    url = world.doc(f"{URL}/{abs(hash(text))}", text, source_type=source_type,
                    source_date=kw.pop("source_date", None))
    return normalized_claim(world, claim(field_key, value, (url, text), **kw))

# --- units and value-in-quote -------------------------------------------------------------

@pytest.mark.parametrize("text, value, expected", [
    ("produced an average of 980,000 cubic metres of drinking water per day", 980000, 980000),
    ("the plants treat 1,159 ML/d on average", 1159000, 1159000),
    ("average demand is 1159 megalitres per day", 1159000, 1159000),
    ("deliveries averaged 1,010 million litres per day", 1010000, 1010000),
    ("the system delivers 300 MGD", 1135623.5, 1135623.5352),
    ("about 1.2 million cubic metres a day", 1200000, 1200000),
])
def test_daily_units_are_converted_and_verified(text, value, expected):
    c = one(World(), text, W, value)
    assert checks(c)["VALUE_IN_QUOTE"] == "PASS"
    assert c.normalized_value == pytest.approx(expected, rel=1e-6)
    assert c.original_value == value  # what the model returned is kept

def test_annual_volume_uses_days_of_the_reference_year():
    text = "In 2024 the utility withdrew 366,000,000 m3/year from Lake Ontario."
    c = one(World(), text, W, 1_000_000)  # 2024 is a leap year: 366 days
    assert checks(c)["VALUE_IN_QUOTE"] == "PASS"
    assert c.normalized_value == pytest.approx(1_000_000)
    assert c.qualifiers.reported_value == 366_000_000
    assert c.qualifiers.reported_unit == "m3/yr" and c.qualifiers.measurement_basis == "annual"
    assert c.qualifiers.reference_year == 2024

def test_annual_volume_without_year_uses_365_25():
    text = "Annual production is 365.25 million cubic metres per year."
    c = one(World(), text, W, 1_000_000)
    assert checks(c)["VALUE_IN_QUOTE"] == "PASS"
    assert c.normalized_value == pytest.approx(1_000_000)
    assert days_in_year(None) == 365.25 and days_in_year(2023) == 365
    assert days_in_year(2000) == 366 and days_in_year(1900) == 365

def test_value_not_in_quote_is_invalid_but_kept():
    world = World()
    c = one(world, "The plants produced 980,000 cubic metres per day.", W, 450000)
    assert checks(c)["VALUE_IN_QUOTE"] == "FAIL"
    assert c.validation.outcome.value == "INVALID"
    assert "980000" in next(k.detail for k in c.validation.checks if k.code == "VALUE_IN_QUOTE")

def test_quote_without_numbers_is_unverifiable_not_invalid():
    c = one(World(), "Toronto Water supplies the whole city.", W, 980000)
    assert checks(c)["VALUE_IN_QUOTE"] == "WARN"
    assert c.validation.outcome.value == "VALID_WITH_WARNINGS"

def test_population_in_millions_and_head_counts():
    world = World()
    ok = one(world, "Toronto had a population of 2,794,356 in 2021.",
             "city_population_millions", 2.794)
    assert checks(ok)["VALUE_IN_QUOTE"] == "PASS" and ok.qualifiers.reference_year == 2021
    raw = one(world, "Toronto had a population of 2,794,356 in 2021.",
              "city_population_millions", 2794356)
    assert checks(raw)["RANGE"] == "FAIL"
    assert "2.794 million" in next(k.detail for k in raw.validation.checks if k.code == "RANGE")

def test_percent_requires_a_percent_sign():
    c = one(World(), "Lake Ontario provides 85% of the supply.",
            "source_withdrawal_percentage", 85)
    # derived fields are never extracted, whatever the quote says
    assert checks(c)["DERIVED_FIELD"] == "FAIL" and c.validation.outcome.value == "INVALID"

def test_capacity_is_flagged_as_not_a_withdrawal():
    c = one(World(), "The plant has a rated capacity of 950,000 m3/d.", W, 950000)
    assert checks(c)["MEASUREMENT_BASIS"] == "WARN"
    assert c.qualifiers.measurement_basis == "capacity"

def test_coordinates_decimal_and_dms():
    world = World()
    dec = one(world, "The intake is at 43.6678° N, 79.2740° W.", "pod_latitude", 43.66781)
    assert checks(dec)["VALUE_IN_QUOTE"] == "PASS" and dec.normalized_value == 43.6678
    dms = one(world, "Intake location: 43°40'4\" N 79°16'26\" W", "pod_longitude", -79.2739)
    assert checks(dms)["VALUE_IN_QUOTE"] == "PASS"
    out = one(world, "Intake location 10.5 N", "pod_latitude", 95.0)
    assert checks(out)["RANGE"] == "FAIL"

# --- type, enum, text ---------------------------------------------------------------------

def test_enum_is_canonicalized_and_unknown_values_rejected():
    world = World()
    ok = one(world, "Toronto's water comes from surface water in Lake Ontario.",
             "water_type", "surface")
    assert ok.normalized_value == "Surface" and checks(ok)["ENUM"] == "PASS"
    bad = one(world, "Toronto's water comes from Lake Ontario.", "water_type", "Lake")
    assert checks(bad)["ENUM"] == "FAIL" and bad.validation.outcome.value == "INVALID"

def test_text_value_must_be_quoted_and_names_are_title_cased():
    world = World()
    c = one(world, "Toronto draws its drinking water from LAKE ONTARIO.", "water_source",
            "LAKE ONTARIO")
    assert c.normalized_value == "Lake Ontario" and checks(c)["VALUE_IN_QUOTE"] == "PASS"
    para = one(world, "Toronto draws its drinking water from a large lake.", "water_source",
               "Lake Ontario")
    assert checks(para)["VALUE_IN_QUOTE"] == "WARN"

def test_reservoir_named_as_source_is_flagged():
    c = one(World(), "Water comes from the Clinton Reservoir.", "water_source",
            "Clinton Reservoir")
    assert checks(c)["RESERVOIR_AS_SOURCE"] == "WARN"

def test_infrastructure_notes_become_a_lowercase_list():
    c = one(World(), "Intake, Pumping Station and Treatment Plant; pipeline",
            "infrastructure_notes", "Intake, Pumping Station and Treatment Plant; pipeline")
    assert c.normalized_value == "intake, pumping station, treatment plant, pipeline"

# --- evidence ------------------------------------------------------------------------------

def test_citation_must_link_to_acquired_evidence():
    world = World()
    world.doc(URL, "Toronto Water provides drinking water to Toronto.")
    linked = normalized_claim(world, claim("utility_name", "Toronto Water",
                                           (URL, "Toronto Water provides drinking water")))
    assert checks(linked)["CITATION_LINKED"] == "PASS"
    assert linked.qualifiers.source_types == ["UTILITY"]
    invented = normalized_claim(world, claim("utility_name", "Toronto Water",
                                             (URL, "Toronto Water runs everything")))
    assert checks(invented)["CITATION_LINKED"] == "FAIL"
    elsewhere = normalized_claim(world, claim("utility_name", "Toronto Water",
                                              ("https://other.example/x", "Toronto Water")))
    assert checks(elsewhere)["CITATION_LINKED"] == "FAIL"
    assert elsewhere.validation.outcome.value == "INVALID"

def test_failed_document_cannot_support_a_claim():
    world = World()
    world.doc(URL, "Toronto Water provides drinking water.", status="FAILED")
    c = normalized_claim(world, claim("utility_name", "Toronto Water",
                                      (URL, "Toronto Water provides drinking water")))
    assert checks(c)["CITATION_LINKED"] == "FAIL"

def test_claim_without_evidence_is_invalid():
    c = normalized_claim(World(), claim("utility_name", "Toronto Water"))
    assert checks(c)["EVIDENCE_PRESENT"] == "FAIL"
    assert c.validation.outcome.value == "INVALID"

def test_status_value_consistency():
    world = World()
    url = world.doc(URL, "Toronto has no interbasin transfer.")
    na = normalized_claim(world, claim("water_transfer_system", None,
                                       (url, "Toronto has no interbasin transfer"),
                                       status=ValueStatus.NOT_APPLICABLE))
    assert checks(na)["VALUE_STATUS"] == "PASS"
    unknown = normalized_claim(world, claim("water_transfer_system", None,
                                            (url, "Toronto has no interbasin transfer"),
                                            status=ValueStatus.UNKNOWN))
    assert checks(unknown)["VALUE_STATUS"] == "FAIL"

# --- years and dependencies ----------------------------------------------------------------

def test_reference_year_from_quote_sentence_or_document():
    world = World()
    url = world.doc(URL, "In 2023 the plants produced an average of 980,000 cubic metres per "
                         "day. Demand rose.", source_date="2024-03-15")
    quote = "produced an average of 980,000 cubic metres per day"
    c = normalized_claim(world, claim(W, 980000, (url, quote)))
    assert c.qualifiers.reference_year == 2023 and c.qualifiers.year_basis == "passage"
    url2 = world.doc(URL + "/2", "The plants produced 900,000 cubic metres per day.",
                     source_date="2019-05-01")
    d = normalized_claim(world, claim(W, 900000, (url2, "produced 900,000 cubic metres per day")))
    assert d.qualifiers.reference_year == 2019 and d.qualifiers.year_basis == "document"

def test_year_field_needs_its_measurement():
    world = World()
    url = world.doc(URL, "Figures are for 2023.")
    alone = normalize(world, claim("total_city_water_withdrawals_year", 2023,
                                   (url, "Figures are for 2023")))
    c = alone.fields["total_city_water_withdrawals_year"].candidate_claims[0]
    assert checks(c)["DEPENDENCY"] == "WARN" and checks(c)["VALUE_IN_QUOTE"] == "PASS"
    future = normalize(world, claim("total_city_water_withdrawals_year", YEAR + 5,
                                    (url, "Figures are for 2023")))
    assert checks(future.fields["total_city_water_withdrawals_year"]
                  .candidate_claims[0])["RANGE"] == "FAIL"

# --- identity and idempotency ---------------------------------------------------------------

def test_claim_uid_is_stable_and_normalization_idempotent():
    world = World()
    url = world.doc(URL, "Toronto Water provides drinking water.")
    raw = claim("utility_name", "Toronto Water", (url, "Toronto Water provides drinking water"))
    once = normalize(world, raw)
    twice = normalize_research(once, FIELDS, world.index, current_year=YEAR)
    a = once.fields["utility_name"].candidate_claims[0]
    b = twice.fields["utility_name"].candidate_claims[0]
    assert a.claim_uid == b.claim_uid == claim_uid(raw)
    assert a.validation == b.validation and a.normalized_value == b.normalized_value
    # whitespace/case differences in the quote don't change identity
    same = claim("utility_name", "Toronto Water", (url, "toronto  water provides drinking water"))
    assert claim_uid(same) == claim_uid(raw)
    assert claim_uid(claim("utility_name", "Other", (url, "x"))) != claim_uid(raw)

def test_unknown_field_is_invalid():
    index = EvidenceIndex([], [])
    research = normalize(World())
    research.fields["utility_name"].candidate_claims.append(
        claim("utility_name", "x", (URL, "x")).model_copy(update={"field_key": "bogus"}))
    out = normalize_research(research, {k: v for k, v in FIELDS.items() if k != "utility_name"},
                             index, current_year=YEAR)
    c = out.fields["utility_name"].candidate_claims[0]
    assert checks(c)["FIELD_KNOWN"] == "FAIL"

def test_quantities_parser_ignores_years_as_units():
    found = quantities("In 2023, 1,159 ML/d and 3.2 million cubic metres a day", "m3/day")
    assert [(q.number, q.canonical) for q in found if q.canonical] == [
        (1159.0, 1159000.0), (3200000.0, 3200000.0)]

# --- findings from the real Toronto run -----------------------------------------------------

def test_elided_quote_links_when_its_parts_occur_in_order():
    world = World()
    world.doc(URL, "Demand has also steadily declined to an estimated 1420 MLD in 2010, down "
                   "an estimated 24% from the 1850 MLD peak in 2001.", page=9)
    elided = "… declined to an estimated 1420 MLD in 2010 … 1850 MLD peak in 2001 …"
    c = normalized_claim(world, claim(W, 1420000, (URL, elided), page=9))
    assert checks(c)["CITATION_LINKED"] == "PASS"
    out_of_order = normalized_claim(world, claim(W, 1420000, (
        URL, "1850 MLD peak in 2001 … declined to an estimated 1420 MLD"), page=9))
    assert checks(out_of_order)["CITATION_LINKED"] == "FAIL"

def test_quote_across_a_chunk_boundary_links_to_the_page():
    world = World()
    world.doc(URL, "Toronto Water treats water from Lake", page=2)
    world.doc(URL, "Ontario at four plants.", page=2)
    c = normalized_claim(world, claim("water_source", "Lake Ontario", (
        URL, "treats water from Lake Ontario at four plants"), page=2))
    assert checks(c)["CITATION_LINKED"] == "PASS"
    assert "chunk boundary" in next(k.detail for k in c.validation.checks
                                    if k.code == "CITATION_LINKED")

def test_hedged_and_wider_area_figures_are_flagged():
    world = World()
    c = one(world, "Ensures more than 3.6 million residents in Toronto, and portions of York "
                   "and Peel have access to safe drinking water", "city_population_millions", 3.6)
    assert checks(c)["APPROXIMATE_VALUE"] == "WARN"
    assert checks(c)["GEOGRAPHIC_SCOPE"] == "WARN"
    exact = one(world, "The 2021 census counted 2,794,356 residents in Toronto.",
                "city_population_millions", 2.794356)
    assert "APPROXIMATE_VALUE" not in checks(exact) and "GEOGRAPHIC_SCOPE" not in checks(exact)
