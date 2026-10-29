"""NO_NEW_ENTITY (off by default): a water_source / reservoir_names re-extraction may be skipped
only when every new passage names nothing but known water bodies and none comes from a more
authoritative source. Anything that could carry new information still triggers the call."""

import asyncio
from water_source_research.agents.nodes import make_extract_node, reextraction_targets
from water_source_research.context_manager import ContextService
from water_source_research.core.config import Settings
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import EvidenceRef, FieldClaim
from water_source_research.services.field_state import initialize_city_state

def chunk(cid, text, url, source_type="OTHER"):
    return {"chunk_id": cid, "url": url, "title": "t", "text": text, "page_number": None,
            "source_type": source_type, "retrieved_at": None}

FIRST = chunk("r1", "Nairobi draws water from the Thika River and the Chania River.",
              "https://ngo.example/a", "OTHER")
SAME_RIVERS = chunk("r2", "Nairobi's supply depends on the Thika River and the Chania River "
                          "in the dry season.", "https://news.example/b", "OTHER")
NEW_RIVER = chunk("r3", "Nairobi also draws water from the Ruiru River.",
                  "https://news.example/c", "OTHER")
OFFICIAL = chunk("r4", "Nairobi Water abstracts from the Thika River for the city.",
                 "https://nairobiwater.co.ke/sources", "UTILITY")
NO_NAMES = chunk("r5", "Nairobi's raw water sources deliver 600,000 m3 per day to the city.",
                 "https://news.example/d", "OTHER")

def state(extra):
    research = initialize_city_state("Nairobi", "Kenya")
    fs = research.fields["water_source"]
    for v in ("Thika River", "Chania River"):
        fs.candidate_claims.append(FieldClaim(field_key="water_source", value=v, confidence=0.8,
                                              evidence=[EvidenceRef(url=FIRST["url"],
                                                                    passage=v)]))
    fs.status = FieldResolutionStatus.CANDIDATE
    return {"run_id": "run-1", "research_state": research.model_dump(mode="json"),
            "unresolved_fields": ["water_source", "water_type"],
            "fields_needing_evidence": ["water_type"], "discovery_rounds": 2,
            "evidence_chunks": [FIRST, *extra], "seen_evidence": [["r1", "water_source"]],
            "reextractions": {}, "events": [], "claims": []}

def targets(extra, on=True):
    skipped: dict[str, int] = {}
    out = reextraction_targets(ContextService(), state(extra), ["water_type"],
                               skip_no_new_entity=on, skipped=skipped)
    return out, skipped

def test_off_by_default():
    assert Settings(_env_file=None).extraction.skip_no_new_entity is False
    assert targets([SAME_RIVERS], on=False) == ({"water_source": 1}, {})

def test_only_known_rivers_from_no_better_source_is_skipped_when_on():
    assert targets([SAME_RIVERS]) == ({}, {"water_source": 1})

def test_a_new_river_still_triggers():
    assert targets([SAME_RIVERS, NEW_RIVER])[0] == {"water_source": 2}

def test_a_more_authoritative_source_still_triggers():
    assert targets([OFFICIAL])[0] == {"water_source": 1}

def test_a_passage_naming_no_water_body_still_triggers():
    # it may carry quantities or relations for the known sources
    assert targets([NO_NAMES])[0] == {"water_source": 1}

class Gateway:
    def __init__(self):
        self.calls = []

    async def extract(self, bundle, run_id, field_key):
        self.calls.append(field_key)
        return []

def test_skip_is_recorded_and_makes_no_call():
    gw = Gateway()
    out = asyncio.run(make_extract_node(ContextService(), gw, skip_no_new_entity=True)(
        state([SAME_RIVERS])))
    assert "water_source" not in gw.calls
    [event] = [e for e in out["events"] if e["field_key"] == "water_source"]
    assert event["strategy"] == "reextract:NO_NEW_ENTITY" and event["attrs"]["skip_reason"]
    assert out["reextractions"] == {}
