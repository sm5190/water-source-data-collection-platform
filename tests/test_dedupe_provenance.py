"""Repeated content is shown to the model once, without losing provenance: the most
authoritative copy is shown, other documents' copies are kept (also_found_in) and become
verified corroborating evidence of a claim quoting them; content already sent for a field
doesn't by itself make the field worth another call, unless it now comes from a more
authoritative source."""

import asyncio
from llm_helpers import Server, chat_json, make_llm, vt_settings
from water_source_research.context_manager import (
    ContextManager, ContextPurpose, ContextStatus, ContextTask,
)
from water_source_research.core.versions import EXTRACTION_PROMPT_VERSION
from water_source_research.llm.extraction import StructuredExtractionGateway
from water_source_research.llm.router import _LockedCache
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)

FIELDS = load_field_definitions()
BASE = ("Nairobi draws its drinking water from the Thika River through the Ndakaini Dam, and "
        "the Chania River supplies the Sasumua treatment works. The city's utility operates "
        "intakes, transmission mains and treatment plants that serve residents across the "
        "county, with storage at several sites and pumping stations along the main pipelines "
        "to the city centre and its suburbs. Water quality is tested daily at the laboratories, "
        "and results are published monthly for every treatment plant and reservoir in the "
        "network, together with the volumes delivered to each zone of the distribution system.")

def chunk(cid, url, source_type, text=BASE, page=None):
    return EvidenceChunk(chunk_id=cid, url=url, title=cid, text=text, source_type=source_type,
                         page_number=page, retrieved_at="2026-10-01T00:00:00Z")

# The news copy repeats the passage (a near-duplicate) with extra keywords, so it ranks first.
NEWS = chunk("news", "https://news.example/nairobi", "OTHER",
             BASE + " Sources: rivers, springs, aquifer, groundwater, wells.")
GOV = chunk("gov", "https://water.go.ke/report", "GOVERNMENT")
GOV_AGAIN = chunk("gov2", "https://water.go.ke/report", "GOVERNMENT", BASE + " Rivers.", page=2)

def build(chunks, seen=()):
    state = initialize_city_state("Nairobi", "Kenya")
    return ContextManager().build(ContextTask(ContextPurpose.EXTRACT, ["water_source"]), state,
                                  chunks, set(seen))

def evidence(bundle):
    return [i for i in bundle.items if i.kind == "evidence"]

def test_more_authoritative_copy_is_shown_and_the_other_location_kept():
    b = build([NEWS, GOV])
    [item] = evidence(b)
    assert item.chunk_id == "gov"
    assert [loc["url"] for loc in item.also_found_in] == [NEWS.url]
    assert item.also_found_in[0]["source_type"] == "OTHER"
    assert b.stats["duplicate_promoted"] == 1 and b.stats["duplicate_locations_kept"] == 1

def test_less_authoritative_copy_is_recorded_on_the_shown_one():
    near = chunk("blog", "https://blog.example/n", "OTHER", BASE)
    [item] = evidence(build([GOV, near]))
    assert item.chunk_id == "gov" and item.also_found_in[0]["chunk_id"] == "blog"

def test_overlapping_windows_of_one_document_keep_no_extra_location():
    [item] = evidence(build([GOV, GOV_AGAIN]))
    assert item.also_found_in is None

ANSWER = {"claims": [{"value": "Thika River", "value_status": "KNOWN",
                      "value_origin": "REPORTED", "confidence": 0.9, "source_kind": "river",
                      "citations": [{"source_id": "S1",
                                     "quote": "drinking water from the Thika River"}]}]}

def extract(bundle, answer=ANSWER):
    gw = StructuredExtractionGateway(make_llm(
        Server(chat_json(answer)), vt_settings(), task="extract",
        prompt_version=EXTRACTION_PROMPT_VERSION, cache=_LockedCache(60, 10)), FIELDS)
    return asyncio.run(gw.extract(bundle, run_id="r", field_key="water_source"))

def test_claim_quoting_a_collapsed_passage_gets_verified_corroborating_evidence():
    [claim] = extract(build([NEWS, GOV]))
    assert [e.url for e in claim.evidence] == [GOV.url, NEWS.url]
    assert all(e.passage == "drinking water from the Thika River" for e in claim.evidence)
    assert claim.qualifiers.corroborating_urls == [NEWS.url]
    assert claim.qualifiers.source_kind == "river"

def test_no_corroboration_where_the_copy_lacks_the_quote():
    other = chunk("other", "https://other.example/n", "OTHER",
                  BASE.replace("drinking water", "potable water"))
    b = build([GOV, other])
    assert evidence(b)[0].also_found_in[0]["chunk_id"] == "other"  # collapsed near-duplicate
    [claim] = extract(b)
    assert [e.url for e in claim.evidence] == [GOV.url]
    assert claim.qualifiers.corroborating_urls == []

def test_content_already_sent_does_not_alone_justify_a_call():
    mirror = chunk("mirror", "https://mirror.example/report", "GOVERNMENT")
    b = build([GOV, mirror], seen=[("gov", "water_source")])
    [item] = evidence(b)
    assert item.chunk_id == "mirror" and item.repeat_of == {"water_source": "gov"}
    assert b.status is ContextStatus.NO_RELEVANT_EVIDENCE

def test_repeat_from_a_more_authoritative_source_is_sent():
    utility = chunk("util", "https://nairobiwater.co.ke/sources", "UTILITY")
    other = chunk("ngo", "https://ngo.example/n", "OTHER")
    b = build([other, utility], seen=[("ngo", "water_source")])
    [item] = evidence(b)
    assert item.chunk_id == "util" and item.repeat_of is None
    assert b.status is ContextStatus.READY
