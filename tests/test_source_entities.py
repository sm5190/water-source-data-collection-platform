"""Water-source entities under the standardization rules: kinds (natural sources, Reclaimed,
general groundwater, reservoirs/systems/categories), relationships only when the evidence
states them (kept with their quotes, nothing converted), out-of-area documents, extraction
schema and grounding, and the evaluation's kind counts."""

import asyncio
from resolution_helpers import FIELDS, World, checks, claim, normalized_claim, resolve
from water_source_research.agents.nodes import make_extract_node
from water_source_research.context_manager import ContextService
from water_source_research.evaluation.evaluate import source_kinds, source_metrics
from water_source_research.llm.extraction import ground_claims, response_model, user_prompt
from water_source_research.schemas.common import FieldResolutionStatus as S
from water_source_research.schemas.research import ClaimQualifiers, FieldClaim
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.normalization import infer_source_kind

UTIL = "https://www.houstonpublicworks.org/water"

def kind_claim(world, value, kind, quote, url=UTIL, relations=()):
    c = claim("water_source", value, (url, quote))
    c.qualifiers = ClaimQualifiers(source_kind=kind, relations=list(relations))
    return c

# --- kinds ------------------------------------------------------------------------------------

def test_natural_sources_reclaimed_and_general_groundwater_are_valid():
    w = World()
    w.doc(UTIL, "Houston draws from the San Jacinto River. Reclaimed water is reused. "
                "Some districts use local wells.")
    for value, kind, quote, expected in [
        ("San Jacinto River", "river", "the San Jacinto River", "PASS"),
        ("Reclaimed", "reclaimed", "Reclaimed water is reused", "PASS"),
        ("local wells", "general_groundwater", "local wells", "WARN"),  # allowed, confirmed
    ]:
        c = normalized_claim(w, kind_claim(w, value, kind, quote))
        assert checks(c)["SOURCE_KIND"] == expected, value
        assert c.validation.outcome.value != "INVALID"  # never rejected for lacking a name

def test_reservoirs_systems_and_categories_go_to_review_not_rejection():
    w = World()
    w.doc(UTIL, "Surface water is sourced from Lake Houston and the Northeast supply system.")
    for value, kind in [("Lake Houston", "reservoir"), ("Northeast supply system",
                                                         "supply_system")]:
        state, _ = resolve(w, kind_claim(w, value, kind, value))
        c = state.fields["water_source"].candidate_claims[0]
        assert checks(c)["SOURCE_KIND"] == "WARN"
        [entity] = state.fields["water_source"].resolution.entities
        assert entity.status is S.HITL_REQUIRED and "WARN_SOURCE_KIND" in entity.reason_codes

def test_kind_inference_is_conservative():
    assert infer_source_kind("Ndakaini Dam") == "reservoir"
    assert infer_source_kind("Groundwater (domestic wells)") == "general_groundwater"
    assert infer_source_kind("Reclaimed") == "reclaimed"
    # a name alone never decides the kind (no proper-noun rule, no geography)
    assert infer_source_kind("Lake Houston") is None
    assert infer_source_kind("Thika River") is None

# --- relations --------------------------------------------------------------------------------

def test_relations_are_kept_only_when_stated_and_link_both_entities():
    w = World()
    w.doc(UTIL, "Lake Houston is on the San Jacinto River. Lake Livingston stores water.")
    stated = {"relation": "on_river", "target": "San Jacinto River",
              "quote": "Lake Houston is on the San Jacinto River", "url": UTIL}
    state, _ = resolve(
        w,
        kind_claim(w, "Lake Houston", "reservoir", "Lake Houston is on the San Jacinto River",
                   relations=[stated]),
        kind_claim(w, "San Jacinto River", "river", "the San Jacinto River"),
        kind_claim(w, "Lake Livingston", "reservoir", "Lake Livingston stores water"))
    entities = {e.entity_key: e for e in state.fields["water_source"].resolution.entities}
    # both kept; nothing converted
    assert {"lake houston", "san jacinto river", "lake livingston"} <= set(entities)
    [link] = entities["lake houston"].relations
    assert link["target_entity_key"] == "san jacinto river"
    assert link["quote"] == "Lake Houston is on the San Jacinto River"
    # no relation stated for Lake Livingston: none inferred (not even from geography)
    assert entities["lake livingston"].relations == []
    assert entities["san jacinto river"].relations == []

# --- out of area ------------------------------------------------------------------------------

def test_a_document_from_another_us_state_is_flagged():
    w = World()
    ms = w.doc("https://msdh.ms.gov/ccr/2024/271576.pdf",
               "Our water source is from wells drawing from the Eutaw Aquifer.",
               source_type="GOVERNMENT")
    tx = w.doc("https://www.twdb.texas.gov/surfacewater/houston/",
               "Lake Houston is on the San Jacinto River.", source_type="GOVERNMENT")
    jurisdiction = claim("jurisdiction", "Texas", (tx, "San Jacinto River"))
    eutaw = kind_claim(w, "Eutaw Aquifer", "aquifer", "the Eutaw Aquifer", url=ms)
    state, _ = resolve(w, jurisdiction, eutaw)
    c = state.fields["water_source"].candidate_claims[0]
    assert checks(c)["OUT_OF_AREA"] == "WARN"
    # the same state, or an unknown jurisdiction: no flag
    w2 = World()
    ms2 = w2.doc("https://msdh.ms.gov/ccr/x.pdf", "wells drawing from the Eutaw Aquifer")
    c2 = normalized_claim(w2, kind_claim(w2, "Eutaw Aquifer", "aquifer", "the Eutaw Aquifer",
                                         url=ms2))
    assert "OUT_OF_AREA" not in checks(c2)

# --- extraction -------------------------------------------------------------------------------

class Bundle:
    def __init__(self, passages):
        from water_source_research.context_manager.manager import SourceRef
        self.sources = {sid: SourceRef(sid, f"https://ex.org/{sid}", "t", None, None)
                        for sid in passages}

    def evidence_ref(self, sid, passage):
        from water_source_research.schemas.research import EvidenceRef
        return EvidenceRef(url=self.sources[sid].url, passage=passage)

def test_source_claims_carry_kind_and_only_grounded_relations():
    passages = {"S1": "Lake Houston is on the San Jacinto River."}
    raw = [{"value": "Lake Houston", "value_status": "KNOWN", "value_origin": "REPORTED",
            "confidence": 0.9, "source_kind": "reservoir",
            "citations": [{"source_id": "S1", "quote": "Lake Houston"}],
            "relations": [
                {"relation": "on_river", "target": "San Jacinto River", "source_id": "S1",
                 "quote": "Lake Houston is on the San Jacinto River"},
                {"relation": "fed_by", "target": "Trinity River", "source_id": "S1",
                 "quote": "fed by the Trinity River"}]}]  # not in the passage
    claims, rejected, _ = ground_claims(Bundle(passages), "water_source", raw, passages)
    [c] = claims
    assert c.qualifiers.source_kind == "reservoir"
    assert [r["target"] for r in c.qualifiers.relations] == ["San Jacinto River"]
    assert c.qualifiers.relations[0]["url"] == "https://ex.org/S1"
    assert rejected == 1

def test_schema_requires_a_kind_for_source_fields_only():
    ws = response_model(FIELDS["water_source"], ["S1"])
    assert "source_kind" in ws.model_json_schema()["$defs"]["Claim"]["required"]
    util = response_model(FIELDS["utility_name"], ["S1"])
    assert "source_kind" not in util.model_json_schema()["$defs"]["Claim"]["properties"]

def test_prompt_shows_the_methodology_definition():
    from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
    state = initialize_city_state("Houston", "United States of America")
    bundle = ContextManager().build(ContextTask(ContextPurpose.EXTRACT, ["water_source"]),
                                    state, [{"chunk_id": "a", "url": UTIL, "title": "t",
                                             "text": "Houston uses the San Jacinto River.",
                                             "source_type": "UTILITY", "page_number": None,
                                             "retrieved_at": None}])
    prompt = user_prompt(bundle, FIELDS["water_source"], {"S1": "x"})
    assert "Definition (standardization rules): Name of the specific natural water source" \
        in prompt
    assert "never infer one from names or geography" in prompt
    assert "is two claims" in prompt

# --- extract node: a reservoir is also a reservoir_names candidate ------------------------------

class Gateway:
    async def extract(self, bundle, run_id, field_key):
        if field_key != "water_source":
            return []
        sid = next(iter(bundle.sources))
        return [FieldClaim(field_key="water_source", value="Ndakaini Dam", confidence=0.8,
                           evidence=[bundle.evidence_ref(sid, passage="Ndakaini")],
                           qualifiers=ClaimQualifiers(source_kind="reservoir"))]

def test_a_reservoir_named_as_a_source_is_kept_in_both_fields():
    research = initialize_city_state("Nairobi", "Kenya")
    state = {"run_id": "r", "research_state": research.model_dump(mode="json"),
             "unresolved_fields": ["water_source", "reservoir_names"], "discovery_rounds": 1,
             "evidence_chunks": [{"chunk_id": "c", "url": "https://u.example", "title": "t",
                                  "text": "Nairobi water comes from Ndakaini Dam reservoir.",
                                  "source_type": "UTILITY", "page_number": None,
                                  "retrieved_at": None}],
             "seen_evidence": [], "events": [], "claims": []}
    out = asyncio.run(make_extract_node(ContextService(), Gateway())(state))
    fields = out["research_state"]["fields"]
    assert [c["value"] for c in fields["water_source"]["candidate_claims"]] == ["Ndakaini Dam"]
    reservoirs = fields["reservoir_names"]["candidate_claims"]
    assert [c["value"] for c in reservoirs] == ["Ndakaini Dam"]
    assert reservoirs[0]["evidence"][0]["passage"] == "Ndakaini"

# --- evaluation: counts by kind ---------------------------------------------------------------

def test_source_metrics_count_non_sources_and_out_of_area():
    claims = [
        {"field_key": "water_source", "value": "Lake Houston",
         "qualifiers": {"source_kind": "reservoir"}, "validation": {"checks": []}},
        {"field_key": "water_source", "value": "Eutaw Aquifer",
         "qualifiers": {"source_kind": "aquifer"},
         "validation": {"checks": [{"code": "OUT_OF_AREA", "result": "WARN"}]}},
        {"field_key": "water_source", "value": "local wells",
         "qualifiers": {}, "validation": {"checks": []}},
    ]
    pairing = {"RAW": {"pairs": {"san jacinto river": None},
                       "predicted": [{"key": "lake houston", "label": "Lake Houston"},
                                     {"key": "eutaw aquifer", "label": "Eutaw Aquifer"},
                                     {"key": "local wells", "label": "local wells"}],
                       "extra": [], "matched": [], "missing_labels": ["San Jacinto River"]}}
    [precision, _] = source_metrics(pairing, source_kinds(claims))
    d = precision["detail"]
    assert d["non_source_kind"] == 1 and d["general_groundwater"] == 1
    assert d["out_of_area"] == 1
    assert d["kinds"] == {"reservoir": 1, "aquifer": 1, "general_groundwater": 1}
