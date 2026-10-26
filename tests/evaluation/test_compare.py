"""Field-specific comparison of predictions with reference values (evaluation/compare.py)."""

import pytest
from water_source_research.evaluation.compare import (
    Prediction, compare, compare_sets, haversine_km,
)
from water_source_research.evaluation.policy import ComparePolicy
from water_source_research.evaluation.reference import ReferenceValue
from water_source_research.services.field_state import load_field_definitions

FIELDS = load_field_definitions()
POLICY = ComparePolicy(field_tolerance={"total_city_water_withdrawals_m3_day": 0.05},
                       aliases={"Hyderabad Metropolitan Water Supply and Sewerage Board":
                                ["HMWSSB"]})
W = "total_city_water_withdrawals_m3_day"

def ref(field, normalized, status="KNOWN", year=None, raw=None, issues=()):
    return ReferenceValue(field_key=field, scope="CITY", raw=raw if raw is not None
                          else normalized, normalized=normalized, value_status=status,
                          reference_year=year, issues=list(issues))

def cmp(field, reference, value, **kw):
    pred = None if value is None else Prediction(value=value, **kw)
    return compare(FIELDS[field], reference, pred, POLICY)

# --- numbers ----------------------------------------------------------------------------------

def test_numbers_within_tolerance_match():
    c = cmp(W, ref(W, 1159745.0, year=2024), 1180000.0)
    assert c.status == "MATCH" and c.relative_error == pytest.approx(0.0175, abs=1e-4)

def test_an_older_observation_is_a_temporal_mismatch_not_a_wrong_number():
    c = cmp(W, ref(W, 1159745.0, year=2024), 1420000.0, candidate_years=[2001, 2010, 2011],
            flags=["APPROXIMATE_VALUE", "MEASUREMENT_BASIS"])
    assert c.status == "TEMPORAL_MISMATCH"
    assert "TEMPORAL" in c.tags and "SCOPE:MEASUREMENT_BASIS" in c.tags
    assert c.ratio == pytest.approx(1.2244, abs=1e-3)

def test_wider_area_figure_is_a_scope_mismatch():
    p = "city_population_millions"
    c = cmp(p, ref(p, 3.273, year=2024), 3.6, flags=["GEOGRAPHIC_SCOPE", "APPROXIMATE_VALUE"])
    assert c.status == "SCOPE_MISMATCH" and c.tags == ["SCOPE:APPROXIMATE_VALUE",
                                                       "SCOPE:GEOGRAPHIC_SCOPE"]

def test_plain_wrong_number_and_same_year():
    c = cmp(W, ref(W, 1159745.0, year=2024), 450000.0, reference_year=2024)
    assert c.status == "MISMATCH"

def test_zero_is_a_value():
    c = "source_withdrawal_contribution_m3_day"
    assert cmp(c, ref(c, 0.0), 0).status == "MATCH"
    assert cmp(c, ref(c, 0.0), 10.0).status == "MISMATCH"

def test_percentages_compare_on_the_normalized_scale():
    p = "percentage_total_population"
    fraction = ref(p, 100.0, raw=1.0, issues=["FRACTION_PERCENT"])
    assert cmp(p, fraction, 100.0).status == "MATCH"

def test_years_exact():
    y = "total_city_water_withdrawals_year"
    assert cmp(y, ref(y, 2024), 2024).status == "MATCH"
    assert cmp(y, ref(y, 2024), 2023).status == "MISMATCH"

# --- missing values ---------------------------------------------------------------------------

def test_missing_reference_and_placeholders_are_not_scored():
    s = "water_system"
    assert cmp(s, ref(s, None, status="UNKNOWN"), "X").status == "MISSING_REFERENCE"
    assert cmp(s, ref(s, None, status="PLACEHOLDER", raw="HYBAS ID"),
               "six pressure zones").status == "NOT_COMPARABLE"

def test_not_applicable_and_unknown_are_distinct():
    t = "water_transfer_system"
    na = ref(t, "N/A", status="NOT_APPLICABLE")
    assert cmp(t, na, None).status == "MATCH"
    assert cmp(t, na, None, value_status="NOT_APPLICABLE").status == "MATCH"
    sewer = cmp(t, na, "Coxwell Bypass Tunnel")
    assert sewer.status == "MISMATCH" and sewer.tags == ["VALUE_WHERE_NOT_APPLICABLE"]
    unknown = compare(FIELDS[t], na, Prediction(value=None, value_status="UNKNOWN"), POLICY)
    assert unknown.status == "MISMATCH" and unknown.tags == ["UNKNOWN_WHERE_NOT_APPLICABLE"]
    assert cmp(W, ref(W, 1.0), None).status == "MISSING_PREDICTION"

# --- categories and entities ------------------------------------------------------------------

def test_categories_are_canonical():
    t = "water_type"
    assert cmp(t, ref(t, "Surface"), "surface").status == "MATCH"
    assert cmp(t, ref(t, "Surface"), "Groundwater").status == "MISMATCH"
    bad = cmp(t, ref(t, "Surface"), "Lake")
    assert bad.status == "MISMATCH" and bad.tags == ["INVALID_CATEGORY"]

def test_entities_use_names_and_aliases_never_similarity():
    u = "utility_name"
    assert cmp(u, ref(u, "Toronto Water"), "toronto water").status == "MATCH"
    alias = cmp(u, ref(u, "Hyderabad Metropolitan Water Supply and Sewerage Board"), "HMWSSB")
    assert alias.status == "MATCH" and alias.tags == ["ALIAS"]
    part = cmp(u, ref(u, "Toronto Water"), "City of Toronto Toronto Water")
    assert part.status == "PARTIAL_MATCH"
    s = "water_source"
    assert cmp(s, ref(s, "Lake Ontario"), "Lake Erie").status == "MISMATCH"  # similar, unrelated
    assert cmp(s, ref(s, "Lake Ontario"), "Lake Ontario").status == "MATCH"

# --- descriptions -----------------------------------------------------------------------------

def test_descriptions_never_match_automatically():
    q = "water_quality_notes"
    reference = ref(q, "freshwater lake source, offshore intakes, turbidity monitoring")
    partial = cmp(q, reference, "Offshore intakes draw freshwater; turbidity monitoring daily.")
    assert partial.status == "PARTIAL_MATCH" and partial.coverage == pytest.approx(0.667, 0.01)
    off = cmp(q, reference, "Bacteriological testing up to 6,000 times a year")
    assert off.status == "NEEDS_REVIEW" and off.coverage == 0

def test_seasonal_description_without_any_reference_concept_needs_review():
    s = "seasonal_uses"
    c = cmp(s, ref(s, "Peak Demand Periods"),
            "Earthy or musty smell or taste can occur in late summer")
    assert c.status == "NEEDS_REVIEW"
    assert cmp(s, ref(s, "None Reported", status="NONE_REPORTED"), None).status == "MATCH"

# --- lists and coordinates --------------------------------------------------------------------

def test_lists_report_matched_missing_and_extra():
    c = compare_sets(["Lake Conroe", "Lake Houston"], ["lake houston", "Lake Livingston"], POLICY)
    assert (c.status, c.matched, c.missing, c.extra) == (
        "PARTIAL_MATCH", ["Lake Houston"], ["Lake Conroe"], ["Lake Livingston"])
    r = "reservoir_names"
    reference = ref(r, ["Lake Conroe", "Lake Houston"])
    assert cmp(r, reference, "Lake Houston, Lake Conroe").status == "MATCH"

def test_coordinates_by_distance():
    lat = "pod_latitude"
    assert cmp(lat, ref(lat, 43.6678), 43.6700).status == "MATCH"  # ~0.24 km
    far = cmp(lat, ref(lat, 43.6678), 43.70)
    assert far.status == "MISMATCH" and far.distance_km == pytest.approx(3.58, 0.01)
    city = "city_latitude"
    assert cmp(city, ref(city, 43.5632), 43.6532).status == "MATCH"  # 10 km, city threshold
    lon = compare(FIELDS["pod_longitude"], ref("pod_longitude", -79.274),
                  Prediction(value=79.274), POLICY, other_lat=43.67)
    assert lon.status == "MISMATCH" and lon.tags == ["SIGN"]
    assert haversine_km(43.6532, -79.3832, 43.6678, -79.274) == pytest.approx(8.9, 0.05)

def test_a_listed_description_is_compared_by_concepts_not_as_a_set():
    f = "infrastructure_notes"
    reference = ref(f, ["lake intake", "intake pipe", "treatment plant", "pumping station"])
    c = cmp(f, reference, "Intake pipes 1-5 km offshore feed a multi-barrier treatment system "
                          "and pumping stations.")
    assert c.status == "PARTIAL_MATCH"  # a paragraph is never a set MISMATCH
    assert "intake pipe" in c.matched and "pumping station" in c.matched
    assert "lake intake" in c.missing
    assert cmp(f, reference, "Chlorine residual is tested daily.").status == "NEEDS_REVIEW"

def test_identical_description_matches_without_judgment():
    from water_source_research.evaluation.compare import _description
    from water_source_research.services.field_state import load_field_definitions
    d = load_field_definitions()["water_quality_notes"]
    c = _description(d, "freshwater lake source,  offshore intakes",
                     "Freshwater lake source, offshore intakes")
    assert c.status == "MATCH" and "IDENTICAL_TEXT" in c.tags
    assert _description(d, "freshwater lake source", "treated water testing").status != "MATCH"
