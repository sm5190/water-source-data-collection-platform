"""The extraction gate (context_manager/relevance.py): no model call when no passage selected
for a field is about the city or its water system, without dropping weak-but-relevant,
upstream, city-website or verified-utility evidence. Cases follow the Santiago run: generic
company pages and other countries' reports in later rounds."""

import asyncio
import pytest
from water_source_research.agents.nodes import make_extract_node, reextraction_targets
from water_source_research.context_manager import (
    ContextManager, ContextPurpose, ContextService, ContextStatus, ContextTask,
)
from water_source_research.context_manager.relevance import (
    CITY_DOMAIN, CITY_NEARBY, DOC_CONTEXT, LOCAL_WATER_FEATURE, VERIFIED_UTILITY,
    build_relevance_context,
)
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import EvidenceChunk, EvidenceRef, FieldClaim
from water_source_research.services.field_state import initialize_city_state

# Milestone 2 regressions of the gate itself, under name-only geography (a city without an
# approved identity); the approved-identity gate is covered by test_geo_relevance.py.
pytestmark = pytest.mark.usefixtures("no_identity")

def chunk(cid, text, url, page=None, source_type="UTILITY"):
    return EvidenceChunk(chunk_id=cid, url=url, text=text, title=cid, page_number=page,
                         source_type=source_type)

# Later-round Santiago material: no city, matched only on generic words.
ALMAR = chunk("almar", "Today, access to water dictates where cities can expand; water quality "
                       "and treatment are core operating conditions for industry every year.",
              "https://almarwater.com/ten-years")
WORLD_POP = chunk("unpop", "The world population will reach 10.3 billion inhabitants by the "
                           "mid-2080s according to the census projections.",
                  "https://ine.example/stats.pdf", page=40, source_type="GOVERNMENT")
LOCAL_POP = chunk("localpop", "Santiago had 6.9 million residents in the 2024 census.",
                  "https://ine.example/stats.pdf", page=12, source_type="GOVERNMENT")
BOGOTA = chunk("bogota", "Lima reported water quality compliance of 92 percent after treatment.",
               "https://minvivienda.example/inca.pdf", page=3, source_type="GOVERNMENT")

def state(**claims):
    s = initialize_city_state("Santiago", "Chile")
    for key, values in claims.items():
        fs = s.fields[key]
        for v in values:
            fs.candidate_claims.append(FieldClaim(field_key=key, value=v, confidence=0.8,
                                                  evidence=[EvidenceRef(url="https://x.example",
                                                                        passage=str(v))]))
        fs.status = FieldResolutionStatus.CANDIDATE
    return s

def bundle(field, chunks, s=None):
    return ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field]),
                                  s or state(), chunks)

# --- bundle status ----------------------------------------------------------------------------

def test_passages_not_about_the_city_make_no_model_call():
    b = bundle("water_quality_notes", [ALMAR, BOGOTA])
    assert b.items and b.status is ContextStatus.NO_RELEVANT_EVIDENCE
    assert b.relevant_fields == []

def test_a_weak_city_passage_among_strong_irrelevant_ones_is_sent():
    weak = chunk("weak", "In Santiago the treatment of drinking water is monitored.",
                 "https://news.example/santiago", source_type="OTHER")
    b = bundle("water_quality_notes", [ALMAR, BOGOTA, weak])
    assert b.status is ContextStatus.READY
    item = next(i for i in b.items if i.chunk_id == "weak")
    assert CITY_NEARBY in item.relevance["water_quality_notes"]
    assert item.score < max(i.score for i in b.items if i.kind == "evidence")

def test_pdf_page_context_counts_only_nearby():
    near = chunk("near", "The census counted 7.1 million residents in the region.",
                 "https://ine.example/stats.pdf", page=13, source_type="GOVERNMENT")
    b = bundle("city_population_millions", [WORLD_POP, LOCAL_POP, near])
    rel = {i.chunk_id: i.relevance["city_population_millions"] for i in b.items
           if i.kind == "evidence"}
    assert DOC_CONTEXT in rel["near"]  # page 13, next to page 12 naming Santiago
    assert rel["unpop"] == []  # page 40 of the same report: not local

def test_web_page_context_counts():
    intro = chunk("intro", "Aguas Andinas serves Greater Santiago.", "https://news.example/a")
    body = chunk("body", "Supply cuts affect four communes with 120,000 customers.",
                 "https://news.example/a")
    b = bundle("population_served_millions", [intro, body])
    item = next(i for i in b.items if i.chunk_id == "body")
    assert DOC_CONTEXT in item.relevance["population_served_millions"]

def test_city_website_counts_without_naming_the_city():
    s = initialize_city_state("Toronto", "Canada")
    intake = chunk("intake", "The R.C. Harris intake is at latitude 43.68 north.",
                   "https://www.toronto.ca/intake")
    b = ContextManager().build(ContextTask(ContextPurpose.EXTRACT, ["pod_latitude"]), s,
                               [intake])
    assert b.status is ContextStatus.READY
    assert CITY_DOMAIN in b.items[-1].relevance["pod_latitude"]

# --- verified utility -------------------------------------------------------------------------

SERVICE = chunk("svc", "Aguas Andinas supplies drinking water to Santiago, Chile.",
                "https://www.aguasandinas.cl/about")
SOURCES = chunk("src", "Our main raw water source is the river, with storage at the reservoir.",
                "https://www.aguasandinas.cl/sources")

def test_utility_whose_service_is_stated_is_trusted_on_its_own_site():
    s = state(utility_name=["Aguas Andinas"])
    ctx = build_relevance_context(s, [SERVICE, SOURCES])
    assert "aguasandinas.cl" in ctx.verified_hosts
    b = bundle("water_source", [SOURCES, SERVICE], s)
    item = next(i for i in b.items if i.chunk_id == "src")
    assert VERIFIED_UTILITY in item.relevance["water_source"]

def test_utility_label_alone_is_not_trusted():
    # Almar is a utility-looking site and an extracted utility name, but nothing in the run
    # says it serves Santiago.
    s = state(utility_name=["Almar Water Solutions"])
    ctx = build_relevance_context(s, [ALMAR])
    assert ctx.verified_hosts == frozenset()
    assert bundle("water_quality_notes", [ALMAR], s).status is ContextStatus.NO_RELEVANT_EVIDENCE

def test_unstated_service_is_not_trusted_even_for_the_right_name():
    s = state(utility_name=["Aguas Andinas"])
    assert build_relevance_context(s, [SOURCES]).verified_hosts == frozenset()

# --- regional / upstream water features --------------------------------------------------------

UPSTREAM = chunk("up", "Snowmelt in the upper Maipo River basin fills the El Yeso reservoir "
                       "each spring.", "https://dga.example/basins", source_type="GOVERNMENT")

def test_upstream_evidence_naming_a_known_source_is_sent_for_water_system_fields():
    s = state(water_source=["Maipo River"])
    b = bundle("reservoir_names", [UPSTREAM], s)
    assert b.status is ContextStatus.READY
    assert LOCAL_WATER_FEATURE in b.items[-1].relevance["reservoir_names"]

def test_water_body_named_in_a_city_sentence_is_a_known_feature():
    city = chunk("city", "Santiago draws most of its water from the Maipo River.",
                 "https://news.example/s")
    ctx = build_relevance_context(state(), [city, UPSTREAM])
    assert "maipo river" in ctx.features
    b = bundle("reservoir_names", [UPSTREAM, city])
    assert LOCAL_WATER_FEATURE in next(i for i in b.items if i.chunk_id == "up"
                                       ).relevance["reservoir_names"]

def test_relation_targets_and_jurisdiction_are_features():
    s = state(jurisdiction=["Santiago Metropolitan Region"])
    s.fields["water_source"].candidate_claims.append(FieldClaim(
        field_key="water_source", value="El Yeso", confidence=0.8,
        evidence=[EvidenceRef(url="https://x.example", passage="El Yeso")]))
    s.fields["water_source"].candidate_claims[-1].qualifiers = None
    from water_source_research.schemas.research import ClaimQualifiers
    s.fields["reservoir_names"].candidate_claims.append(FieldClaim(
        field_key="reservoir_names", value="El Yeso reservoir", confidence=0.8,
        evidence=[EvidenceRef(url="https://x.example", passage="El Yeso")],
        qualifiers=ClaimQualifiers(source_kind="reservoir", relations=[
            {"relation": "on_river", "target": "Yeso River", "quote": "q", "url": "u"}])))
    ctx = build_relevance_context(s, [])
    assert {"yeso river", "el yeso reservoir", "santiago metropolitan region"} <= ctx.features

def test_features_do_not_open_city_fields():
    s = state(water_source=["Maipo River"])
    pop = chunk("pop", "The Maipo River basin has 7 million inhabitants by census count.",
                "https://dga.example/basin-pop", source_type="GOVERNMENT")
    assert bundle("city_population_millions", [pop], s).status is \
        ContextStatus.NO_RELEVANT_EVIDENCE

def test_generic_candidates_and_other_city_water_bodies_are_not_features():
    s = state(water_source=["groundwater", "mountain spring water"])
    other = chunk("lima", "Lima draws its water from the Rimac River.", "https://x.example/l")
    ctx = build_relevance_context(s, [other])
    assert ctx.features == frozenset()
    rimac = chunk("rimac", "Flow in the Rimac River fell to 20 m3/s; the dam stores water.",
                  "https://y.example/r", source_type="GOVERNMENT")
    assert bundle("reservoir_names", [rimac, other], s).status is \
        ContextStatus.NO_RELEVANT_EVIDENCE

def test_other_place_passages_are_never_relevant():
    s = state(water_source=["Maipo River"])
    ctx = build_relevance_context(s, [])
    assert ctx.rules(BOGOTA, "OTHER", BOGOTA.text, ContextManager().fields["water_source"]) == []

def test_gate_inputs_are_in_the_cache_key():
    mgr = ContextManager()
    task = ContextTask(ContextPurpose.EXTRACT, ["water_quality_notes"])
    assert mgr.cache_key(task, state(), [ALMAR]) != mgr.cache_key(
        task, state(water_source=["Maipo River"]), [ALMAR])

# --- graph: skipped, recorded, retried when relevant evidence arrives ---------------------------

class Gateway:
    def __init__(self, values=None):
        self.values, self.calls = values or {}, []

    async def extract(self, bundle, run_id, field_key):
        self.calls.append(field_key)
        out = []
        for v in self.values.get(field_key, []):
            sid = next(iter(bundle.sources))
            passage = bundle.sources[sid].passages[0]
            out.append(FieldClaim(field_key=field_key, value=v, confidence=0.8,
                                  evidence=[bundle.evidence_ref(sid, passage=passage[:40])]))
        return out

def graph_state(chunks, fields, needing, rnd, research=None, reextractions=None):
    research = research or initialize_city_state("Santiago", "Chile")
    return {"run_id": "run-1", "research_state": research.model_dump(mode="json"),
            "unresolved_fields": fields, "fields_needing_evidence": needing,
            "discovery_rounds": rnd, "evidence_chunks": [c.model_dump(mode="json")
                                                        for c in chunks],
            "seen_evidence": [], "reextractions": reextractions or {}, "events": [],
            "claims": []}

def test_gated_field_is_recorded_and_retried_when_relevant_evidence_arrives():
    gateway = Gateway({"water_quality_notes": ["Chlorinated"]})
    node = make_extract_node(ContextService(), gateway)
    out = asyncio.run(node(graph_state([ALMAR], ["water_quality_notes"],
                                       ["water_quality_notes"], 2)))
    assert gateway.calls == []
    assert out["fields_needing_evidence"] == ["water_quality_notes"]
    [event] = [e for e in out["events"] if e["field_key"] == "water_quality_notes"]
    assert event["strategy"] == "NO_RELEVANT_EVIDENCE"
    assert event["attrs"]["skip_reason"] and event["attrs"]["rule_hits"] == "none"
    assert "llm_requests" not in event["attrs"]
    assert out["seen_evidence"] == []  # not marked seen: re-judged next round
    local = chunk("q", "Santiago's drinking water treatment meets the quality standard.",
                  "https://gob.example/q", source_type="GOVERNMENT")
    out2 = asyncio.run(node({**out, "evidence_chunks": out["evidence_chunks"]
                             + [local.model_dump(mode="json")], "discovery_rounds": 3}))
    assert gateway.calls == ["water_quality_notes"]
    claims = out2["research_state"]["fields"]["water_quality_notes"]["candidate_claims"]
    assert [c["value"] for c in claims] == ["Chlorinated"]

def test_gated_reextraction_does_not_use_the_budget():
    research = state(water_source=["Maipo River"])
    s = graph_state([ALMAR], ["water_source", "water_type"], ["water_type"], 2, research)
    service = ContextService()
    # ALMAR names no water body and no city: not new relevant evidence at all
    assert reextraction_targets(service, s, ["water_type"]) == {}
    out = asyncio.run(make_extract_node(service, Gateway())(s))
    assert out["reextractions"] == {}

def test_upstream_evidence_triggers_reextraction_of_sources():
    research = state(water_source=["Maipo River"])
    s = graph_state([UPSTREAM], ["water_source", "water_type"], ["water_type"], 2, research)
    assert reextraction_targets(ContextService(), s, ["water_type"]) == {"water_source": 1}
