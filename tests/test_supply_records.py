"""SupplyRecord assembly, the methodology's derived fields and the final quality checks
(services/supply_records.py), from claims through normalization and resolution."""

import pytest
from resolution_helpers import FIELDS as ALL_FIELDS
from resolution_helpers import YEAR, World, claim, state_with
from water_source_research.schemas.common import FieldResolutionStatus as S
from water_source_research.schemas.common import ValueStatus
from water_source_research.schemas.research import EntityResolution, FieldResolution
from water_source_research.services.normalization import normalize_research
from water_source_research.services.resolution import resolve_research
from water_source_research.services.supply_records import build_record_set, typed_row

UTIL = "https://www.toronto.ca/services/water"
REPORT = "https://www.toronto.ca/water-report-2025.pdf"
CENSUS = "https://www12.statcan.gc.ca/census/toronto"
T = "total_city_water_withdrawals_m3_day"
C = "source_withdrawal_contribution_m3_day"
# The derivations need an accepted city population: these tests designate the census as the
# city population dataset (none is configured in production, so there it is always reviewed).
FIELDS = dict(ALL_FIELDS)
for _key in ("city_population_millions", "city_population_reference_year"):
    FIELDS[_key] = ALL_FIELDS[_key].model_copy(update={"designated_dataset": {
        "name": "Statistics Canada census", "hosts": ["statcan.gc.ca"]}})

def records(world, *claims, researched=None, edit=None):
    research = normalize_research(state_with(*claims), FIELDS, world.index, current_year=YEAR)
    research, _ = resolve_research(research, FIELDS, world.index)
    if edit:
        edit(research)
    keys = researched if researched is not None else sorted({c.field_key for c in claims})
    return build_record_set(research, FIELDS, keys)

def rows(rs):
    return [typed_row(rs, r.record_key) for r in sorted(rs.records, key=lambda r: r.position)]

def toronto() -> World:
    world = World()
    world.serving_utility(UTIL)
    world.doc(UTIL, "Toronto draws its drinking water from Lake Ontario.")
    world.doc(REPORT, "In 2025 Toronto's intakes withdrew an average of 980,000 cubic metres of "
                      "raw water per day.", source_type="GOVERNMENT")
    world.doc(CENSUS, "The 2021 census counted 2,794,356 residents in Toronto.",
              source_type="GOVERNMENT")
    return world

SOURCE = claim("water_source", "Lake Ontario", (UTIL, "drinking water from Lake Ontario"))
TOTAL_QUOTE = "In 2025 Toronto's intakes withdrew an average of 980,000 cubic metres of raw water " \
              "per day"
TOTAL = claim(T, 980000, (REPORT, TOTAL_QUOTE))
POP = claim("city_population_millions", 2.794356, (CENSUS, "counted 2,794,356 residents"))

# --- one source ---------------------------------------------------------------------------------

def test_single_source_city_gets_one_row_with_allocated_contribution():
    rs = records(toronto(), SOURCE, TOTAL, POP, researched=["water_source", T, C,
                                                            "city_population_millions"])
    [row] = rows(rs)
    assert row["water_source"] == "Lake Ontario"
    assert row[C] == 980000 and row["source_withdrawal_contribution_method"] == "Reported Daily"
    assert row["source_withdrawal_contribution_year"] == 2025
    assert row["total_city_water_withdrawals_year"] == 2025
    assert row["source_withdrawal_percentage"] == 100.0
    assert row["population_served_millions"] == pytest.approx(2.794)
    assert row["percentage_total_population"] == 100.0
    [rec] = rs.records
    contrib = rs.record_values[rec.record_key][C]
    assert contrib.value_origin.value == "ALLOCATED"
    assert contrib.calculation.rule == "SINGLE_SOURCE_TOTAL"
    assert contrib.calculation.inputs[0].claim_uids == rs.city_values[T].claim_uids
    served = rs.record_values[rec.record_key]["population_served_millions"]
    assert served.value_origin.value == "ESTIMATED"
    assert {i.field_key for i in served.calculation.inputs} == {
        "city_population_millions", "source_withdrawal_percentage"}
    assert rs.completion == "FINALIZED_WITH_GAPS"  # fields not researched are explicit Unknowns
    assert {c.code for c in rs.quality if c.result == "FAIL"} == set()

def test_references_list_every_cited_source_except_common_ones():
    rs = records(toronto(), SOURCE, TOTAL, POP)
    [row] = rows(rs)
    assert UTIL in row["references"] and REPORT in row["references"]
    assert CENSUS not in row["references"]  # city population is common to every row
    assert "Water Source: " in row["references"]
    assert "Total City Water Withdrawals (m3/d)" in row["references"]
    check = next(c for c in rs.quality if c.code == "CITED_URLS_IN_REFERENCES")
    assert check.result == "PASS"

def test_unknown_source_still_produces_a_row():
    rs = records(toronto(), TOTAL, researched=[T, "water_source"])
    [row] = rows(rs)
    assert row["water_source"] == "Unknown" and row[T] == 980000
    # Researched, nothing found: an explicit gap (with review, a reviewer is asked first).
    assert rs.completion == "FINALIZED_WITH_GAPS"
    rv = rs.record_values[rs.records[0].record_key]["water_source"]
    assert rv.resolution_status == "NO_EVIDENCE"

# --- several sources ----------------------------------------------------------------------------

def two_sources(world: World):
    world.doc(UTIL + "/both", "Toronto draws its water from Lake Ontario and the Oak Ridges "
                              "Aquifer.")
    world.doc(REPORT + "/split", "In 2025 the Lake Ontario intakes withdrew 950,000 m3/d. In "
                                 "2025 the Oak Ridges Aquifer wells withdrew 30,000 m3/d.",
              source_type="GOVERNMENT")
    return [claim("water_source", "Lake Ontario", (UTIL + "/both", "water from Lake Ontario")),
            claim("water_source", "Oak Ridges Aquifer", (UTIL + "/both",
                                                         "the Oak Ridges Aquifer"))]

LAKE = claim(C, 950000, (REPORT + "/split",
                         "In 2025 the Lake Ontario intakes withdrew 950,000 m3/d"))

def test_multiple_sources_keep_city_values_once():
    world = toronto()
    sources = two_sources(world)
    aquifer = "2025 the Oak Ridges Aquifer wells withdrew 30,000 m3/d"
    rs = records(world, *sources, TOTAL, LAKE, claim(C, 30000, (REPORT + "/split", aquifer)))
    by_source = {r["water_source"]: r for r in rows(rs)}
    assert set(by_source) == {"Lake Ontario", "Oak Ridges Aquifer"}
    assert by_source["Lake Ontario"]["source_withdrawal_percentage"] == pytest.approx(96.939)
    assert by_source["Oak Ridges Aquifer"]["source_withdrawal_percentage"] == pytest.approx(
        3.061)
    # The city total appears on both rows but is stored once (no double counting).
    assert by_source["Lake Ontario"][T] == by_source["Oak Ridges Aquifer"][T] == 980000
    assert T in rs.city_values and all(T not in v for v in rs.record_values.values())
    assert next(c for c in rs.quality if c.code == "CONTRIBUTIONS_WITHIN_TOTAL").result == "PASS"
    assert next(c for c in rs.quality if c.code == "ONE_SOURCE_PER_ROW").result == "PASS"

def test_remainder_is_allocated_to_the_one_unknown_source():
    world = toronto()
    sources = two_sources(world)
    rs = records(world, *sources, TOTAL, LAKE)
    by_source = {r["water_source"]: r for r in rows(rs)}
    aquifer = by_source["Oak Ridges Aquifer"]
    assert aquifer[C] == 30000
    assert aquifer["source_withdrawal_contribution_method"] == "Remainder Allocated"
    assert aquifer["source_withdrawal_contribution_year"] == 2025
    assert by_source["Lake Ontario"]["source_withdrawal_contribution_method"] == "Reported Daily"

def test_contribution_that_cannot_be_estimated_is_zero_with_unknown_method():
    world = toronto()
    sources = two_sources(world)
    rs = records(world, *sources, researched=["water_source", C])
    for row in rows(rs):
        assert row[C] == 0 and row["source_withdrawal_contribution_method"] == "Unknown"
        assert row["source_withdrawal_percentage"] == "Unknown"  # 0 is a rule, not a share
    rec = rs.records[0]
    assert rs.record_values[rec.record_key][C].calculation.rule == "NOT_ESTIMABLE_ZERO"

def test_annual_contribution_is_reported_annually_and_years_must_match():
    world = toronto()
    world.doc(REPORT + "/annual", "Lake Ontario intakes withdrew 346,750,000 m3/year in 2024.",
              source_type="GOVERNMENT")
    rs = records(world, SOURCE, TOTAL, claim(C, 950000, (
        REPORT + "/annual", "Lake Ontario intakes withdrew 346,750,000 m3/year in 2024")))
    [row] = rows(rs)
    assert row["source_withdrawal_contribution_method"] == "Reported Annually"
    assert row[C] == pytest.approx(346_750_000 / 366)  # 2024 is a leap year
    assert row["source_withdrawal_contribution_year"] == 2024
    # 2024 contribution vs 2025 total: not divided
    assert row["source_withdrawal_percentage"] == "Unknown"
    rec = rs.records[0]
    assert "years differ" in rs.record_values[rec.record_key][
        "source_withdrawal_percentage"].note

# --- missing values and completion --------------------------------------------------------------

def test_not_applicable_is_exported_as_na_and_never_derived_over():
    def reviewer_na(research):
        fs = research.fields["water_transfer_system"]
        fs.status = S.NOT_APPLICABLE
        fs.resolution = FieldResolution(scope="record", decided_by="REVIEWER",
                                        policy_version="x", entities=[EntityResolution(
                                            entity_key="*", status=S.NOT_APPLICABLE,
                                            rule="REVIEW_MARK_NA", decided_by="REVIEWER",
                                            review_decision_id=4, note="no transfers")])
    rs = records(toronto(), SOURCE, TOTAL, researched=["water_source", T,
                                                       "water_transfer_system"],
                 edit=reviewer_na)
    [row] = rows(rs)
    assert row["water_transfer_system"] == "N/A"
    rv = rs.record_values[rs.records[0].record_key]["water_transfer_system"]
    assert rv.value_status is ValueStatus.NOT_APPLICABLE and rv.review_decision_ids == [4]

def test_conflicting_field_makes_the_run_incomplete():
    world = toronto()
    world.doc(REPORT + "/other", "In 2025 Toronto's intakes withdrew 1,200,000 m3/d.",
              source_type="GOVERNMENT")
    rs = records(world, SOURCE, TOTAL, claim(T, 1200000, (REPORT + "/other",
                                                          "In 2025 Toronto's intakes withdrew 1,200,000 m3/d")))
    assert rs.completion == "INCOMPLETE"
    fail = next(c for c in rs.quality if c.code == "OPEN_FIELD" and c.result == "FAIL")
    assert fail.field_key == T and "CONFLICT" in fail.detail
    assert rows(rs)[0][T] == "Unknown"  # never a guessed value

def test_every_row_validates_against_the_standardized_schema():
    rs = records(toronto(), SOURCE, TOTAL, POP)
    assert all(c.result == "PASS" for c in rs.quality if c.code == "SCHEMA")

# --- catalog identity (Milestone 2: city/country are not extracted for catalog cities) ------

def test_unextracted_catalog_identity_is_filled_and_labelled():
    rs = records(toronto(), SOURCE, researched=["water_source", "city_name", "country"])
    for key, name in (("city_name", "Toronto"), ("country", "Canada")):
        rv = rs.city_values[key]
        assert rv.value == name and rv.resolution_status == "FROM_CITY_CATALOG"
        assert not rv.claim_uids  # catalog identity, never presented as a reported claim

def test_reported_identity_is_kept_apart_from_the_catalog():
    world = toronto()
    world.doc(UTIL + "/about", "The City of Toronto operates its water utility.")
    named = claim("city_name", "City of Toronto", (UTIL + "/about", "The City of Toronto"))
    rs = records(world, SOURCE, named, researched=["water_source", "city_name"])
    rv = rs.city_values["city_name"]  # the extracted value, never overwritten by the catalog
    assert (rv.value, rv.resolution_status) == ("City of Toronto", "RESOLVED")
    assert rv.claim_uids
