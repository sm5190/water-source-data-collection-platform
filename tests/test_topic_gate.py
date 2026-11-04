"""The topical half of the extraction gate (context_manager/topic.py): a passage about the
right city but clearly off the field's drinking-water topic makes no model call; ambiguous
evidence is kept; local languages, headings, tables, water bodies and verified-utility pages
count. Synthetic passages; name-only geography (no approved identity for this city)."""

import pytest
from water_source_research.context_manager import (
    ContextManager, ContextPurpose, ContextStatus, ContextTask,
)
from water_source_research.context_manager.topic import (
    CLEARLY_IRRELEVANT, PASS, UNCERTAIN, decide,
)
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.field_state import initialize_city_state, load_field_definitions

pytestmark = pytest.mark.usefixtures("no_identity")
FIELDS = load_field_definitions()
CITY, COUNTRY = "Testburg", "Germany"

def chunk(cid, text, url="https://www.example.org/x", title="", source_type="OTHER"):
    return EvidenceChunk(chunk_id=cid, url=url, text=text, title=title,
                         source_type=source_type)

def bundle(field, chunks):
    return ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field]),
                                  initialize_city_state(CITY, COUNTRY), chunks)

# Off-topic passages that retrieval still selects through generic field words.
HQ = chunk("hq", "The group's main revenue source is its headquarters in Testburg, with "
                 "regional hubs in Canada.", title="Group Annual Report 2025")
VENUE = chunk("venue", "The congress in Testburg gathered 7000 delegates; the network of "
                       "partners and the annual report are online.", title="Congress Report")

def test_a_city_mention_off_topic_makes_no_call():
    for field in ("water_source", "water_system"):
        b = bundle(field, [HQ, VENUE])
        assert b.status is ContextStatus.NO_RELEVANT_EVIDENCE, field
        assert b.off_topic(field)
    assert decide(HQ, HQ.text, FIELDS["water_source"])[0] == CLEARLY_IRRELEVANT

def test_on_topic_passages_are_sent():
    cases = {
        "utility_name": "Testburg Water Board supplies drinking water to Testburg.",
        "water_source": "Testburg draws its water from the Elm River and two springs.",
        "total_city_water_withdrawals_m3_day": "Testburg water withdrawal was 410,000 m3/day "
                                               "in 2023.",
        "city_population_millions": "Testburg has 1.2 million inhabitants (census 2022).",
    }
    for field, text in cases.items():
        assert bundle(field, [chunk(field, text)]).status is ContextStatus.READY, field

def test_local_languages_count():
    de = chunk("de", "Die Wasserversorgung von Testburg stützt sich auf Grundwasser.")
    es = chunk("es", "El abastecimiento de agua potable de Testburg proviene del río Lerma.")
    assert decide(de, de.text, FIELDS["water_source"])[0] == PASS
    assert decide(es, es.text, FIELDS["water_source"])[0] == PASS

def test_a_heading_or_table_header_supplies_the_topic():
    table = chunk("t", "Water production 2023\nTestburg  410,000  395,000", title="Annex 3")
    assert decide(table, "Testburg  410,000  395,000",
                  FIELDS["total_city_water_withdrawals_m3_day"])[0] == PASS

def test_general_water_vocabulary_is_uncertain_and_kept():
    loose = chunk("l", "Testburg's sanitation programme was discussed with residents.")
    assert decide(loose, loose.text, FIELDS["water_source"])[0] == UNCERTAIN
    kept = chunk("k", "The sanitation network of Testburg was renewed in 2020.")
    assert decide(kept, kept.text, FIELDS["water_system"])[0] == PASS  # 'network' + water word
    assert bundle("water_system", [kept]).status is ContextStatus.READY

def test_a_verified_utility_page_or_a_known_feature_passes():
    plain = chunk("p", "Our customers can pay online.")
    assert decide(plain, plain.text, FIELDS["water_source"], ["VERIFIED_UTILITY"])[0] == PASS
    assert decide(plain, plain.text, FIELDS["water_source"], ["LOCAL_WATER_FEATURE"])[0] \
        == PASS

def test_a_distant_upstream_source_page_is_on_topic():
    dam = chunk("dam", "The Elm Dam, 120 km upstream in the hills, stores water for Testburg.",
                url="https://www.foreign-agency.example/dams")
    assert decide(dam, dam.text, FIELDS["reservoir_names"])[0] == PASS

def test_identity_fields_are_never_off_topic():
    assert decide(HQ, HQ.text, FIELDS["jurisdiction"])[0] != CLEARLY_IRRELEVANT
