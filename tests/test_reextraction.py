"""Later-round re-extraction of entity-defining fields: triggered by materially new relevant
evidence (even weaker-scoring), bounded, never for other places, LLM usage recorded, new
claims merged with their extraction round."""

import asyncio
from water_source_research.agents.nodes import (
    MAX_REEXTRACTIONS_PER_FIELD, make_extract_node, reextraction_targets,
)
from water_source_research.context_manager import ContextService
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import EvidenceRef, FieldClaim
from water_source_research.services.field_state import initialize_city_state

def chunk(cid, text, url, source_type="UTILITY"):
    return {"chunk_id": cid, "url": url, "title": "t", "text": text, "page_number": None,
            "source_type": source_type, "retrieved_at": None}

ROUND1 = chunk("r1", "Nairobi Water supplies the city from the Ndakaini Dam reservoir.",
               "https://utility.example/dams")
LATER = chunk("r3", "The Upper Tana basin supplies Nairobi through the Sasumua and Ndakaini "
                    "dams drawing water from the Chania and Thika rivers respectively.",
              "https://ngo.example/report.pdf", source_type="OTHER")
ELSEWHERE = chunk("x9", "Mombasa draws water from the Mzima Springs and the Baricho wells.",
                  "https://elsewhere.example/coast")

class Gateway:
    def __init__(self, values):
        self.values, self.calls = values, []

    async def extract(self, bundle, run_id, field_key):
        self.calls.append((field_key, sorted(i.chunk_id for i in bundle.items
                                             if i.kind == "evidence")))
        out = []
        for v in self.values.get(field_key, []):
            sid = next(iter(bundle.sources))
            passage = bundle.sources[sid].passages[0]
            out.append(FieldClaim(field_key=field_key, value=v, confidence=0.8,
                                  evidence=[bundle.evidence_ref(sid, passage=passage[:60])]))
        return out

def state(extra_chunks, reextractions=None, seen_r1=True):
    research = initialize_city_state("Nairobi", "Kenya")
    ws = research.fields["water_source"]
    ws.candidate_claims.append(FieldClaim(
        field_key="water_source", value="Ndakaini Dam", confidence=0.8,
        evidence=[EvidenceRef(url=ROUND1["url"], passage="Ndakaini Dam")]))
    ws.status = FieldResolutionStatus.CANDIDATE
    return {"run_id": "run-1", "research_state": research.model_dump(mode="json"),
            "unresolved_fields": ["water_source", "water_type"],
            "fields_needing_evidence": ["water_type"], "discovery_rounds": 3,
            "evidence_chunks": [ROUND1, *extra_chunks],
            "seen_evidence": [["r1", "water_source"]] if seen_r1 else [],
            "reextractions": reextractions or {}, "events": [], "claims": []}

def run(node, s):
    return asyncio.run(node(s))

def test_new_relevant_evidence_triggers_a_bounded_reextraction():
    service = ContextService()
    gateway = Gateway({"water_source": ["Chania River", "Thika River"]})
    out = run(make_extract_node(service, gateway), state([LATER]))
    assert [f for f, _ in gateway.calls].count("water_source") == 1
    _, shown = next(c for c in gateway.calls if c[0] == "water_source")
    assert shown == ["r3"]  # only the new evidence, not what the model already saw
    claims = out["research_state"]["fields"]["water_source"]["candidate_claims"]
    assert [c["value"] for c in claims] == ["Ndakaini Dam", "Chania River", "Thika River"]
    assert [c["qualifiers"]["extraction_round"] for c in claims[1:]] == [3, 3]
    assert out["reextractions"] == {"water_source": 1}
    assert "water_source" not in out["fields_needing_evidence"]
    event = next(e for e in out["events"] if e["field_key"] == "water_source")
    assert event["strategy"].startswith("reextract:")
    assert event["attrs"]["reextraction"] == 1 and event["attrs"]["new_chunks"] == 1
    assert event["attrs"]["discovery_round"] == 3

def test_weaker_scoring_new_evidence_still_counts():
    # LATER is from a low-weight OTHER source and mentions no field keyword strongly; it still
    # names water bodies in a sentence about Nairobi.
    service = ContextService()
    assert reextraction_targets(service, state([LATER]), ["water_type"]) == {"water_source": 1}

def test_evidence_about_another_place_does_not_trigger():
    service = ContextService()
    assert reextraction_targets(service, state([ELSEWHERE]), ["water_type"]) == {}

def test_nothing_new_means_no_model_call():
    service = ContextService()
    gateway = Gateway({"water_source": ["X"]})
    run(make_extract_node(service, gateway), state([]))
    assert "water_source" not in [f for f, _ in gateway.calls]

def test_reextraction_is_bounded_per_run():
    service = ContextService()
    done = {"water_source": MAX_REEXTRACTIONS_PER_FIELD}
    assert reextraction_targets(service, state([LATER], reextractions=done),
                                ["water_type"]) == {}

def test_first_pass_is_not_a_reextraction():
    service = ContextService()
    s = state([LATER])
    del s["fields_needing_evidence"]
    assert reextraction_targets(service, s, []) == {}

def test_a_reextraction_without_claims_keeps_the_candidates():
    service = ContextService()
    gateway = Gateway({})
    out = run(make_extract_node(service, gateway), state([LATER]))
    claims = out["research_state"]["fields"]["water_source"]["candidate_claims"]
    assert [c["value"] for c in claims] == ["Ndakaini Dam"]
    assert "water_source" not in out["fields_needing_evidence"]
    assert out["reextractions"] == {"water_source": 1}
