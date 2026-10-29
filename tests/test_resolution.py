"""Deterministic evidence-based resolution (services/resolution.py)."""

from resolution_helpers import FIELDS, World, claim, normalize, resolve
from water_source_research.schemas.common import FieldResolutionStatus as S
from water_source_research.schemas.common import ValueStatus
from water_source_research.schemas.events import NON_PLANNING_KINDS, EventKind, append_event
from water_source_research.services.resolution import (
    ALL_RECORDS, CITY, UNATTRIBUTED, name_key, resolve_research,
)

W = "total_city_water_withdrawals_m3_day"
UTIL = "https://www.toronto.ca/services/water"
REPORT = "https://www.toronto.ca/water-report-2023.pdf"
NEWS = "https://news.example.com/toronto-water"

def entities(state, key):
    return {e.entity_key: e for e in state.fields[key].resolution.entities}

# --- single and equivalent claims -----------------------------------------------------------

def test_single_official_verified_claim_is_resolved_automatically():
    world = World()
    world.doc(UTIL, "Toronto Water provides drinking water to Toronto.")
    state, log = resolve(world, claim("utility_name", "Toronto Water",
                                      (UTIL, "Toronto Water provides drinking water")))
    fs = state.fields["utility_name"]
    assert fs.status is S.RESOLVED
    assert fs.selected_claim.value == "Toronto Water"
    [entity] = fs.resolution.entities
    assert entity.entity_key == ALL_RECORDS and entity.rule == "AUTO_STRICT"
    assert log[0]["from_status"] == "UNRESOLVED" and log[0]["to_status"] == "RESOLVED"
    assert log[0]["selected_claim_uids"] == [fs.selected_claim.claim_uid]

def test_equivalent_claims_from_two_documents_form_one_group():
    world = World()
    world.doc(UTIL, "In 2025 Toronto withdrew 980,000 m3/d of raw water.")
    world.doc(REPORT, "Average withdrawal in 2025: 980 ML/d.", source_type="GOVERNMENT")
    state, _ = resolve(world,
                       claim(W, 980000, (UTIL, "In 2025 Toronto withdrew 980,000 m3/d")),
                       claim(W, 980000, (REPORT, "Average withdrawal in 2025: 980 ML/d")))
    fs = state.fields[W]
    assert fs.status is S.RESOLVED
    [e] = fs.resolution.entities
    assert e.entity_key == CITY and len(e.candidate_claim_uids) == 2
    assert len(e.selected_claim_uids) == 1

def test_confidence_never_decides_acceptance():
    world = World()
    world.doc(UTIL, "Toronto Water provides drinking water to Toronto.")
    world.doc(NEWS, "Toronto Water provides drinking water, a blogger says.", source_type="OTHER")
    low_official, _ = resolve(world, claim("utility_name", "Toronto Water", (
        UTIL, "Toronto Water provides drinking water"), confidence=0.2))
    assert low_official.fields["utility_name"].status is S.RESOLVED
    high_unofficial, _ = resolve(world, claim("utility_name", "Toronto Water", (
        NEWS, "Toronto Water provides drinking water"), confidence=0.99))
    fs = high_unofficial.fields["utility_name"]
    assert fs.status is S.HITL_REQUIRED and fs.selected_claim is None
    assert "NO_OFFICIAL_SOURCE" in fs.resolution.entities[0].reason_codes

def test_unverified_value_needs_review():
    world = World()
    world.doc(UTIL, "The city utility serves 2.8 million people.")
    state, _ = resolve(world, claim("utility_name", "Toronto Water",
                                    (UTIL, "The city utility serves 2.8 million people")))
    fs = state.fields["utility_name"]
    assert fs.status is S.HITL_REQUIRED
    assert "VALUE_NOT_VERIFIED" in fs.resolution.entities[0].reason_codes

# --- conflicts and time ---------------------------------------------------------------------

def test_contradictory_values_for_the_same_year_conflict():
    world = World()
    world.doc(UTIL, "In 2025 Toronto withdrew 980,000 m3/d.")
    world.doc(REPORT, "In 2025 Toronto withdrew 1,200,000 m3/d.", source_type="GOVERNMENT")
    state, log = resolve(world, claim(W, 980000, (UTIL, "In 2025 Toronto withdrew 980,000 m3/d")),
                         claim(W, 1200000, (REPORT, "In 2025 Toronto withdrew 1,200,000 m3/d")))
    fs = state.fields[W]
    assert fs.status is S.CONFLICT and fs.selected_claim is None
    assert fs.resolution.entities[0].rule == "VALUES_DISAGREE"
    assert log[0]["to_status"] == "CONFLICT"

def test_undated_disagreement_is_a_conflict():
    world = World()
    world.doc(UTIL, "Toronto withdraws 980,000 m3/d.")
    world.doc(REPORT, "Toronto withdraws 1,200,000 m3/d.", source_type="GOVERNMENT")
    state, _ = resolve(world, claim(W, 980000, (UTIL, "Toronto withdraws 980,000 m3/d")),
                       claim(W, 1200000, (REPORT, "Toronto withdraws 1,200,000 m3/d")))
    assert state.fields[W].resolution.entities[0].rule == "VALUES_DISAGREE_UNDATED"

def test_different_years_are_history_not_conflict():
    world = World()
    world.doc(UTIL, "In 2025 Toronto withdrew 980,000 m3/d.")
    world.doc(REPORT, "In 2019 Toronto withdrew 1,050,000 m3/d.", source_type="GOVERNMENT")
    old = claim(W, 1050000, (REPORT, "In 2019 Toronto withdrew 1,050,000 m3/d"))
    new = claim(W, 980000, (UTIL, "In 2025 Toronto withdrew 980,000 m3/d"))
    state, _ = resolve(world, old, new)
    fs = state.fields[W]
    assert fs.status is S.RESOLVED and fs.selected_claim.value == 980000
    [e] = fs.resolution.entities
    assert len(e.historical_claim_uids) == 1
    assert "OLDER_VALUES_KEPT_AS_HISTORY" in e.reason_codes

def test_descriptive_text_differences_go_to_review_not_conflict():
    world = World()
    world.doc(UTIL, "Lake water has low turbidity.")
    world.doc(REPORT, "Algal blooms occur in late summer.", source_type="GOVERNMENT")
    world.doc(UTIL + "/src", "Toronto draws its water from Lake Ontario.")
    state, _ = resolve(world,
                       claim("water_source", "Lake Ontario",
                             (UTIL + "/src", "Toronto draws its water from Lake Ontario")),
                       claim("water_quality_notes", "low turbidity",
                             (UTIL, "Lake water has low turbidity")),
                       claim("water_quality_notes", "algal blooms in late summer",
                             (REPORT, "Algal blooms occur in late summer")))
    fs = state.fields["water_quality_notes"]
    assert fs.status is S.HITL_REQUIRED
    assert fs.resolution.entities[0].rule == "MULTIPLE_DESCRIPTIONS"

def test_names_that_may_be_the_same_need_review():
    world = World()
    world.doc(UTIL, "Toronto Water provides drinking water.")
    world.doc(REPORT, "City of Toronto Water provides drinking water.", source_type="GOVERNMENT")
    state, _ = resolve(world,
                       claim("jurisdiction", "Toronto Water", (UTIL, "Toronto Water provides")),
                       claim("jurisdiction", "City of Toronto Water",
                             (REPORT, "City of Toronto Water provides")))
    assert state.fields["jurisdiction"].resolution.entities[0].rule == "POSSIBLE_EQUIVALENCE"
    assert state.fields["jurisdiction"].status is S.HITL_REQUIRED

# --- multiple sources and attribution ---------------------------------------------------------

def two_source_world() -> tuple[World, list]:
    world = World()
    world.doc(UTIL, "Toronto draws its drinking water from Lake Ontario, and the Toronto "
                    "Island community is also served by the Oak Ridges Aquifer.")
    world.doc(REPORT, "The Lake Ontario intakes withdrew 950,000 m3/d in 2025. The Oak Ridges "
                      "Aquifer wells withdrew 30,000 m3/d in 2025.", source_type="GOVERNMENT")
    claims = [
        claim("water_source", "Lake Ontario", (UTIL, "drinking water from Lake Ontario")),
        claim("water_source", "Oak Ridges Aquifer",
              (UTIL, "also served by the Oak Ridges Aquifer")),
    ]
    return world, claims

def test_multiple_water_sources_coexist():
    world, claims = two_source_world()
    state, _ = resolve(world, *claims)
    fs = state.fields["water_source"]
    assert fs.status is S.RESOLVED  # two accepted sources, not a conflict
    assert set(entities(state, "water_source")) == {"lake ontario", "oak ridges aquifer"}
    assert fs.selected_claim is None  # several values: see the entities
    assert {c.entity_key for c in fs.candidate_claims} == {"lake ontario", "oak ridges aquifer"}

def test_source_level_values_are_attributed_by_name():
    world, claims = two_source_world()
    state, _ = resolve(world, *claims,
                       claim("source_withdrawal_contribution_m3_day", 950000,
                             (REPORT, "The Lake Ontario intakes withdrew 950,000 m3/d in 2025")),
                       claim("source_withdrawal_contribution_m3_day", 30000,
                             (REPORT, "Aquifer wells withdrew 30,000 m3/d in 2025")))
    by_entity = entities(state, "source_withdrawal_contribution_m3_day")
    assert set(by_entity) == {"lake ontario", "oak ridges aquifer"}
    assert all(e.status is S.RESOLVED for e in by_entity.values())
    fs = state.fields["source_withdrawal_contribution_m3_day"]
    assert fs.status is S.RESOLVED and fs.selected_claim is None  # one value per source

def test_ambiguous_attribution_goes_to_review():
    world, claims = two_source_world()
    world.doc(NEWS + "/both", "Lake Ontario and the Oak Ridges Aquifer together supply "
                              "980,000 m3/d.", source_type="GOVERNMENT")
    state, _ = resolve(world, *claims, claim(
        "source_withdrawal_contribution_m3_day", 980000,
        (NEWS + "/both", "Lake Ontario and the Oak Ridges Aquifer together supply 980,000 m3/d")))
    [e] = state.fields["source_withdrawal_contribution_m3_day"].resolution.entities
    assert e.entity_key == UNATTRIBUTED and e.status is S.HITL_REQUIRED
    assert "ATTRIBUTION_AMBIGUOUS" in e.reason_codes

def test_single_source_city_attributes_everything_to_it():
    world = World()
    world.doc(UTIL, "Toronto draws its drinking water from Lake Ontario. The intake is at "
                    "43.6678 N.")
    state, _ = resolve(world,
                       claim("water_source", "Lake Ontario", (UTIL, "water from Lake Ontario")),
                       claim("pod_latitude", 43.6678, (UTIL, "The intake is at 43.6678 N")))
    fs = state.fields["pod_latitude"]
    assert fs.status is S.RESOLVED and fs.selected_claim.entity_key == "lake ontario"

def test_source_level_claim_without_any_source_needs_review():
    world = World()
    world.doc(UTIL, "The intake is at 43.6678 N.")
    state, _ = resolve(world, claim("pod_latitude", 43.6678, (UTIL, "The intake is at 43.6678 N")))
    [e] = state.fields["pod_latitude"].resolution.entities
    assert e.entity_key == UNATTRIBUTED and e.reason_codes[0] == "NO_WATER_SOURCE"

def test_several_utilities_are_attributed_per_source():
    world, claims = two_source_world()
    world.doc(NEWS + "/u", "Lake Ontario water is treated by Toronto Water, while York Region "
                           "operates the Oak Ridges Aquifer wells.", source_type="GOVERNMENT")
    state, _ = resolve(world, *claims,
                       claim("utility_name", "Toronto Water",
                             (NEWS + "/u", "Lake Ontario water is treated by Toronto Water")),
                       claim("utility_name", "York Region",
                             (NEWS + "/u", "York Region operates the Oak Ridges Aquifer wells")))
    assert set(entities(state, "utility_name")) == {"lake ontario", "oak ridges aquifer"}

# --- never resolved without valid evidence --------------------------------------------------

def test_field_without_valid_evidence_is_never_resolved():
    world = World()
    world.doc(UTIL, "Toronto Water provides drinking water.")
    state, _ = resolve(world, claim("utility_name", "Toronto Water",
                                    (UTIL, "a sentence that is not in the document")))
    fs = state.fields["utility_name"]
    assert fs.status is S.HITL_REQUIRED and fs.selected_claim is None
    assert fs.resolution.entities[0].rule == "INVALID_ONLY"
    assert "CITATION_LINKED" in fs.resolution.entities[0].reason_codes
    untouched, log = resolve(world)
    assert untouched.fields["utility_name"].status is S.UNRESOLVED and log == []

def test_not_applicable_and_review_always_fields_need_a_person():
    world = World()
    world.doc(UTIL, "No water quality report is published. Basin 7020000010.")
    state, _ = resolve(world,
                       claim("water_quality_year", None, (UTIL, "No water quality report"),
                             status=ValueStatus.NOT_APPLICABLE),
                       claim("water_system", "7020000010", (UTIL, "Basin 7020000010")))
    assert "NOT_APPLICABLE_CLAIM" in state.fields["water_quality_year"] \
        .resolution.entities[0].reason_codes
    assert "REVIEW_ALWAYS" in state.fields["water_system"].resolution.entities[0].reason_codes

def test_reviewer_final_states_are_preserved():
    world = World()
    world.doc(UTIL, "Toronto Water provides drinking water.")
    research = normalize(world, claim("utility_name", "Toronto Water",
                                      (UTIL, "Toronto Water provides drinking water")))
    research.fields["utility_name"].status = S.NOT_APPLICABLE  # set by a person
    state, log = resolve_research(research, FIELDS, world.index)
    assert state.fields["utility_name"].status is S.NOT_APPLICABLE and log == []

def test_resolution_is_deterministic():
    world, claims = two_source_world()
    a, log_a = resolve(world, *claims)
    b, log_b = resolve(world, *reversed(claims))
    # Input order doesn't matter: same entities, selections and log.
    assert a.fields["water_source"].resolution == b.fields["water_source"].resolution
    assert log_a == log_b

def test_name_keys():
    assert name_key("The Lake Ontario") == "lake ontario"
    assert name_key("Lozoya  River,") == "lozoya river"
    assert name_key("Río Bogotá") == "rio bogota"

def test_outcome_events_are_not_trajectory_steps():
    from water_source_research.procedures.trajectory import build_trajectory
    from water_source_research.schemas.events import WorkflowEvent
    events = append_event([], kind=EventKind.RESOLUTION, node="resolve", field_key="utility_name",
                          strategy="AUTO_STRICT", attrs={"rule": "AUTO_STRICT",
                                                         "entity_key": "*"})
    events = append_event(events, kind=EventKind.VALIDATION, node="validate",
                          field_key="utility_name", strategy="VALID")
    assert {EventKind.RESOLUTION, EventKind.VALIDATION} <= NON_PLANNING_KINDS
    t = build_trajectory({"run_id": "r", "city_name": "Toronto", "workflow_version": 3,
                          "outcome": "COMPLETED"},
                         [WorkflowEvent.model_validate(e) for e in events], FIELDS, "utility")
    assert t.steps == []

def test_field_specific_authoritative_sources():
    world = World()
    world.doc(UTIL, "The 2021 census counted 2,794,356 residents in Toronto.")
    world.doc(REPORT, "The 2021 census counted 2,794,356 residents in Toronto.",
              source_type="GOVERNMENT")
    from_utility, _ = resolve(world, claim("city_population_millions", 2.794356, (
        UTIL, "The 2021 census counted 2,794,356 residents")))
    assert "NO_OFFICIAL_SOURCE" in from_utility.fields["city_population_millions"] \
        .resolution.entities[0].reason_codes  # city population: statistics, not the utility
    # Standardization Rules 6-7: city population comes from the designated dataset. None is
    # configured, so even a government census figure goes to a person.
    from_census, _ = resolve(world, claim("city_population_millions", 2.794356, (
        REPORT, "The 2021 census counted 2,794,356 residents")))
    fs = from_census.fields["city_population_millions"]
    assert fs.status is S.HITL_REQUIRED
    reasons = fs.resolution.entities[0].reason_codes
    assert "WARN_DESIGNATED_SOURCE_MISSING" in reasons and "NO_OFFICIAL_SOURCE" not in reasons

def test_a_designated_population_dataset_is_accepted():
    fields = dict(FIELDS)
    fields["city_population_millions"] = FIELDS["city_population_millions"].model_copy(
        update={"designated_dataset": {"name": "City statistics", "hosts": ["toronto.ca"]}})
    world = World()
    world.doc(REPORT, "The 2021 census counted 2,794,356 residents in Toronto.",
              source_type="GOVERNMENT")
    from water_source_research.services.normalization import normalize_research
    from resolution_helpers import YEAR, state_with
    research = normalize_research(state_with(claim("city_population_millions", 2.794356, (
        REPORT, "The 2021 census counted 2,794,356 residents"))), fields, world.index,
        current_year=YEAR)
    state, _ = resolve_research(research, fields, world.index)
    assert state.fields["city_population_millions"].status is S.RESOLVED

def test_judgement_fields_are_never_accepted_automatically():
    world = World()
    world.doc(UTIL, "Toronto draws its water from Lake Ontario. Flows are diverted through the "
                    "Coxwell Bypass Tunnel of the Don River.")
    state, _ = resolve(world,
                       claim("water_source", "Lake Ontario", (UTIL, "water from Lake Ontario")),
                       claim("water_transfer_system", "Coxwell Bypass Tunnel",
                             (UTIL, "Coxwell Bypass Tunnel of the Don River")))
    fs = state.fields["water_transfer_system"]
    assert fs.status is S.HITL_REQUIRED
    assert fs.resolution.entities[0].reason_codes == ["REVIEW_ALWAYS"]
