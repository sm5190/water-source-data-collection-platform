"""Counterfactual replay helpers: recorded claims re-grounded in the passages now shown, and
the slot comparison (lost vs replaced, token estimates)."""

import asyncio
from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
from water_source_research.observability.llm_telemetry import llm_call_context
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.counterfactual import RecordedGateway, compare
from water_source_research.services.field_state import initialize_city_state

CHUNK = EvidenceChunk(chunk_id="c1", url="https://nairobiwater.example/dams", title="Dams",
                      text="Nairobi draws water from the Thika River and the Chania River.",
                      source_type="UTILITY")

def recorded(value, quote, rnd=2, field="water_source", review=None):
    return {"field_key": field, "value": value, "original_value": None,
            "value_status": "KNOWN", "value_origin": "REPORTED", "confidence": 0.8,
            "review_status": review, "qualifiers": {"extraction_round": rnd,
                                                    "source_kind": "river"},
            "citations": [{"quote": quote}]}

def test_recorded_claims_are_regrounded_or_listed_as_unreproduced():
    gw = RecordedGateway([recorded("Thika River", "the Thika River"),
                          recorded("Sasumua Dam", "the Sasumua Dam"),
                          recorded("Typed in", "the Thika River", review="CORRECTED"),
                          recorded("Round one", "the Chania River", rnd=1)])
    state = initialize_city_state("Nairobi", "Kenya")
    bundle = ContextManager().build(ContextTask(ContextPurpose.EXTRACT, ["water_source"]),
                                    state, [CHUNK])

    async def call():
        with llm_call_context(discovery_round=2):
            return await gw.extract(bundle, "r", "water_source")

    [claim] = asyncio.run(call())
    assert claim.value == "Thika River" and claim.evidence[0].url == CHUNK.url
    assert claim.qualifiers.source_kind == "river"
    assert gw.unreproduced == [{"field": "water_source", "round": 2, "value": "Sasumua Dam"}]
    assert gw.calls == [("water_source", 2)]

def slot(field, rnd, status="READY", called=True, tokens=(1000, 100), reason=None):
    return {"field": field, "round": rnd, "stage": "initial", "bundle_status": status,
            "called": called, "input_tokens": tokens[0], "output_tokens": tokens[1],
            "skip_reason": reason}

def test_compare_separates_lost_from_replaced_and_estimates_tokens():
    parent = {"slots": [slot("city_name", 1), slot("water_source", 2),
                        slot("water_quality_notes", 3), slot("jurisdiction", 2),
                        slot("city_name", 2)]}
    cf = [slot("city_name", 1, "NOT_NEEDED", reason="from the approved city catalog"),
          slot("water_source", 2, "NO_RELEVANT_EVIDENCE"),
          slot("water_quality_notes", 3, "NO_RELEVANT_EVIDENCE"),
          slot("jurisdiction", 2), slot("jurisdiction", 3)]
    claims = [{"field_key": "city_name", "value": "Nairobi", "validation_outcome": "VALID",
               "is_selected": True, "qualifiers": {"extraction_round": 1}},
              {"field_key": "water_source", "value": "Thika River",
               "validation_outcome": "VALID", "is_selected": False,
               "qualifiers": {"extraction_round": 2}}]
    r = compare(parent, cf, claims, [])
    assert (r["parent_calls"], r["counterfactual_calls"]) == (5, 2)
    replaced = {(s["field"], s["round"]): s["replaced"] for s in r["skipped_slots"]}
    assert replaced == {("city_name", 1): True, ("city_name", 2): True,  # never rescheduled
                        ("water_source", 2): False, ("water_quality_notes", 3): False}
    assert [x["field"] for x in r["skipped_with_usable_claims"]] == ["water_source"]
    assert r["added_slots"] == [{"field": "jurisdiction", "round": 3,
                                 "tokens_estimate": 1100}]
    assert (r["parent_tokens"], r["counterfactual_tokens_estimate"]) == (5500, 2200)
