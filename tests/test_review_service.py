"""Review items, decision checks and decision application (services/review.py), without the
graph: multi-source attribution, partial reviewer decisions, and what may not be approved."""

from resolution_helpers import FIELDS, World, claim, resolve
from water_source_research.schemas.common import FieldResolutionStatus as S
from water_source_research.schemas.review import ReviewDecisionIn
from water_source_research.services.resolution import UNATTRIBUTED, resolve_research
from water_source_research.services.review import (
    apply_decisions, decision_basis, known_sources, open_review_items, validate_decision,
)

C = "source_withdrawal_contribution_m3_day"
UTIL = "https://www.toronto.ca/services/water"
BOTH = "https://www.toronto.ca/water/both"

def two_sources_and_an_ambiguous_volume():
    world = World()
    world.serving_utility(UTIL)
    world.doc(UTIL, "Toronto draws its drinking water from Lake Ontario and the Oak Ridges "
                    "Aquifer.")
    world.doc(BOTH, "Lake Ontario and the Oak Ridges Aquifer: the intakes withdrew "
                    "950,000 m3/d in 2025.", source_type="GOVERNMENT")
    state, _ = resolve(world,
                       claim("water_source", "Lake Ontario", (UTIL, "water from Lake Ontario")),
                       claim("water_source", "Oak Ridges Aquifer", (UTIL, "the Oak Ridges Aquifer")),
                       claim(C, 950000, (BOTH, "the intakes withdrew 950,000 m3/d in 2025")))
    return world, state

def items_for(state, world, round_no=1):
    return open_review_items("run-1", round_no, state, FIELDS, None, [], world.index)

def as_item(spec):
    return {"id": spec.item_id, "round": spec.round, "field_key": spec.field_key,
            "entity_key": spec.entity_key, "label": spec.label,
            "candidate_claim_uids": spec.candidate_claim_uids, "payload": spec.payload}

def dec(spec, kind, n=0, **kw):
    return ReviewDecisionIn(decision_key=f"key-{kind}-{n:04d}", item_id=spec.item_id, round=1,
                            decision=kind, **kw)

def stored(d: ReviewDecisionIn, n: int) -> dict:
    return {"id": n, "item_id": d.item_id, "decision": d.decision.value,
            "claim_uids": d.claim_uids, "reviewed_value": d.value,
            "value_origin": d.value_origin.value, "reason": d.reason,
            "correction_category": d.correction_category,
            "evidence": d.evidence.model_dump() if d.evidence else None,
            "target_entity_key": d.entity_key, "rejected_claim_uids": d.rejected_claim_uids,
            "validation_override": d.override_validation}

def test_ambiguous_volume_is_assigned_to_a_source_then_resolved_by_the_rules():
    world, state = two_sources_and_an_ambiguous_volume()
    [spec] = [s for s in items_for(state, world) if s.field_key == C]
    assert spec.entity_key == UNATTRIBUTED
    assert "ASSIGN_SOURCE" in spec.payload["allowed_decisions"]
    assert {s["key"] for s in spec.payload["sources"]} == {"lake ontario", "oak ridges aquifer"}
    uid = spec.candidate_claim_uids[0]

    approve = dec(spec, "APPROVE", claim_uids=[uid])
    assert any("ASSIGN_SOURCE" in p for p in validate_decision(
        approve, as_item(spec), FIELDS[C], lambda _: None))
    assign = dec(spec, "ASSIGN_SOURCE", claim_uids=[uid], entity_key="lake ontario")
    assert validate_decision(assign, as_item(spec), FIELDS[C], lambda _: None) == []
    bad_target = dec(spec, "ASSIGN_SOURCE", 1, claim_uids=[uid], entity_key="atlantis")
    assert validate_decision(bad_target, as_item(spec), FIELDS[C], lambda _: None)

    applied = apply_decisions(state, FIELDS, [as_item(spec)], [stored(assign, 7)], world.index,
                              lambda _: None)
    assert applied.log[0]["to_status"] == "SENT_BACK_TO_RULES"
    again, _ = resolve_research(applied.research, FIELDS, world.index)
    entities = {e.entity_key: e for e in again.fields[C].resolution.entities}
    assert set(entities) == {"lake ontario"}
    assert entities["lake ontario"].status is S.RESOLVED  # the rules accept it now
    assert entities["lake ontario"].decided_by == "RULES"

def test_partial_reviewer_decisions_survive_re_resolution():
    world, state = two_sources_and_an_ambiguous_volume()
    ws_items = [s for s in items_for(state, world) if s.field_key == "water_source"]
    assert ws_items == []  # both sources were accepted by the rules
    # A reviewer decides the aquifer isn't a source of this city's supply.
    item = {"id": "x", "field_key": "water_source", "entity_key": "oak ridges aquifer",
            "label": "Oak Ridges Aquifer", "candidate_claim_uids": []}
    applied = apply_decisions(state, FIELDS, [item], [{
        "id": 3, "item_id": "x", "decision": "MARK_NA", "claim_uids": [],
        "reason": "Island wells serve only the islands"}], world.index, lambda _: None)
    ws = applied.research.fields["water_source"]
    assert ws.status is S.RESOLVED  # one source left
    again, _ = resolve_research(applied.research, FIELDS, world.index)
    entities = {e.entity_key: e for e in again.fields["water_source"].resolution.entities}
    assert entities["oak ridges aquifer"].status is S.NOT_APPLICABLE
    assert entities["oak ridges aquifer"].decided_by == "REVIEWER"
    assert entities["oak ridges aquifer"].review_decision_id == 3
    assert [s["key"] for s in known_sources(again)] == ["lake ontario"]

def test_one_value_fields_take_one_approved_claim():
    world = World()
    world.doc(UTIL, "Toronto Water and the City of Toronto Water Division supply drinking water.",
              source_type="OTHER")
    state, _ = resolve(world,
                       claim("jurisdiction", "Ontario", (UTIL, "Toronto Water")),
                       claim("jurisdiction", "Ontario Province", (UTIL, "City of Toronto")))
    [spec] = [s for s in items_for(state, world) if s.field_key == "jurisdiction"]
    uids = spec.candidate_claim_uids
    two = dec(spec, "APPROVE", claim_uids=uids)
    assert any("exactly one" in p for p in validate_decision(two, as_item(spec),
                                                              FIELDS["jurisdiction"],
                                                              lambda _: None))
    one = dec(spec, "APPROVE", 1, claim_uids=uids[:1])
    applied = apply_decisions(state, FIELDS, [as_item(spec)], [stored(one, 1)], world.index,
                              lambda _: None)
    fs = applied.research.fields["jurisdiction"]
    assert fs.status is S.RESOLVED and fs.selected_claim.claim_uid == uids[0]
    assert fs.resolution.decided_by == "REVIEWER"
    assert applied.hitl[0]["result"] == "APPROVED"

def test_fields_not_researched_or_generated_are_not_review_items():
    world = World()
    state, _ = resolve(world)
    specs = open_review_items("run-1", 1, state, FIELDS, ["utility_name"], ["utility_name"],
                              world.index)
    assert [(s.field_key, s.reason_codes) for s in specs] == [("utility_name",
                                                               ["NO_EVIDENCE"])]
    everything = open_review_items("run-1", 1, state, FIELDS, None, [], world.index)
    keys = {s.field_key for s in everything}
    assert "references" not in keys and "percentage_total_population" not in keys
    # Nothing found, but the catalog or the methodology's derivations fill these gaps.
    assert not keys & {"country", "city_name", "source_withdrawal_contribution_m3_day",
                       "total_city_water_withdrawals_year", "population_served_millions"}
    assert {"pod_latitude", "reservoir_names", "water_type"} <= keys

# --- simple review: reject, pick another, no reasons --------------------------------------------

def two_jurisdiction_candidates():
    world = World()
    world.doc(UTIL, "Toronto Water and the City of Toronto Water Division supply drinking water.",
              source_type="OTHER")
    state, _ = resolve(world,
                       claim("jurisdiction", "Ontario", (UTIL, "Toronto Water")),
                       claim("jurisdiction", "Quebec", (UTIL, "City of Toronto")))
    [spec] = [s for s in items_for(state, world) if s.field_key == "jurisdiction"]
    return world, state, spec

def test_decisions_need_no_reason():
    _, _, spec = two_jurisdiction_candidates()
    for n, kind in enumerate(["MARK_UNKNOWN", "MARK_NA", "DEFER", "REJECT", "APPROVE"]):
        uids = spec.candidate_claim_uids[:1] if kind in ("REJECT", "APPROVE") else []
        d = dec(spec, kind, n, claim_uids=uids)
        assert validate_decision(d, as_item(spec), FIELDS["jurisdiction"], lambda _: None) == []
        assert decision_basis(d)  # a plain description is stored instead of a typed reason

def test_rejecting_one_candidate_leaves_the_others_to_the_rules():
    world, state, spec = two_jurisdiction_candidates()
    ontario, quebec = spec.candidate_claim_uids
    reject = dec(spec, "REJECT", claim_uids=[quebec])
    applied = apply_decisions(state, FIELDS, [as_item(spec)], [stored(reject, 1)], world.index,
                              lambda _: None)
    assert applied.log[0]["to_status"] == "SENT_BACK_TO_RULES"
    fs = applied.research.fields["jurisdiction"]
    assert fs.claim(quebec).review_status == "REJECTED"
    assert fs.claim(ontario).review_status != "REJECTED"
    assert fs.status is not S.NEEDS_RESEARCH  # a usable candidate is left

def test_rejecting_every_candidate_means_needs_research_not_unknown():
    world, state, spec = two_jurisdiction_candidates()
    reject = dec(spec, "REJECT", claim_uids=spec.candidate_claim_uids)
    applied = apply_decisions(state, FIELDS, [as_item(spec)], [stored(reject, 4)], world.index,
                              lambda _: None)
    fs = applied.research.fields["jurisdiction"]
    assert fs.status is S.NEEDS_RESEARCH and fs.selected_claim is None
    [entity] = fs.resolution.entities
    assert (entity.status, entity.rule, entity.decided_by, entity.review_decision_id) == (
        S.NEEDS_RESEARCH, "REVIEW_REJECT", "REVIEWER", 4)
    assert applied.hitl[0]["result"] == "REJECTED"
    # Settled for this run: no further review item, and the rules don't reopen it.
    again, _ = resolve_research(applied.research, FIELDS, world.index)
    assert again.fields["jurisdiction"].status is S.NEEDS_RESEARCH
    assert not [s for s in items_for(again, world, 2) if s.field_key == "jurisdiction"]

def test_picking_another_value_records_the_rejected_one():
    world, state, spec = two_jurisdiction_candidates()
    ontario, quebec = spec.candidate_claim_uids
    pick = dec(spec, "APPROVE", claim_uids=[ontario], rejected_claim_uids=[quebec])
    assert validate_decision(pick, as_item(spec), FIELDS["jurisdiction"], lambda _: None) == []
    assert decision_basis(pick).startswith("Accepted another value found")
    both = dec(spec, "APPROVE", 1, claim_uids=[ontario], rejected_claim_uids=[ontario])
    assert validate_decision(both, as_item(spec), FIELDS["jurisdiction"], lambda _: None)
    applied = apply_decisions(state, FIELDS, [as_item(spec)], [stored(pick, 2)], world.index,
                              lambda _: None)
    fs = applied.research.fields["jurisdiction"]
    assert fs.status is S.RESOLVED and fs.selected_claim.claim_uid == ontario
    assert fs.claim(quebec).review_status == "REJECTED"
