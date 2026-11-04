"""The City + Region + Country identity in retrieval, the extraction gate and validation: a
same-named place's evidence no longer passes as the city's, upstream evidence linked from
the real city still does, and nothing changes without an approved identity. Cases follow the
saved runs (Colombian and Mexican reports for Santiago; Houston, Mississippi's health
department; Vienna, Virginia)."""

import pytest
from water_source_research.context_manager import (
    ContextManager, ContextPurpose, ContextStatus, ContextTask,
)
from water_source_research.context_manager.relevance import (
    CITY_NEARBY, DOC_CONTEXT, LOCAL_WATER_FEATURE, build_relevance_context,
)
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services import geo_match
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.places import names_city

@pytest.fixture
def identity_on(monkeypatch):
    """Use the drafted identities (as an approved file would be used)."""
    monkeypatch.setenv("CITY_IDENTITY_ALLOW_DRAFT", "1")
    geo_match._matcher.cache_clear()
    yield
    geo_match._matcher.cache_clear()

@pytest.fixture
def identity_off(no_identity):
    """Name-only geography (a city without an approved identity)."""
    yield

def chunk(cid, text, url, page=None, source_type="GOVERNMENT", title=None):
    return EvidenceChunk(chunk_id=cid, url=url, text=text, title=title or cid,
                         page_number=page, source_type=source_type)

def bundle(field, chunks, city="Santiago", country="Chile"):
    return ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field]),
                                  initialize_city_state(city, country), chunks)

COLOMBIA = chunk("inca", "National drinking water quality report. The municipality of "
                         "Santiago reported no risk in treated water quality.",
                 "https://www.minvivienda.gov.co/inca-2024.pdf", page=88)
CALI = chunk("cali", "Santiago de Cali obtiene el agua potable del río Cauca y del río Cali.",
             "https://www.example.org/agua", source_type="TECHNICAL")
CHILE = chunk("siss", "En Santiago la calidad del agua potable cumple la norma NCh409.",
              "https://www.siss.gob.cl/calidad")
CITY_SOURCE = chunk("maipo1", "Santiago, Chile, draws most of its drinking water from the Maipo "
                              "River.", "https://www.dga.mop.gob.cl/a")
UPSTREAM = chunk("maipo2", "Flows in the Maipo River fell 30% during the megadrought; "
                           "intake capacity was preserved.", "https://www.cr2.cl/b",
                 source_type="ACADEMIC")
CAUCA = chunk("cauca", "Flows in the Cauca River fell sharply; intake capacity was preserved.",
              "https://www.example.edu/c", source_type="ACADEMIC")

# --- the extraction gate ------------------------------------------------------------------

def test_a_colombian_report_is_not_santiago_evidence(identity_on):
    b = bundle("water_quality_notes", [COLOMBIA])
    assert b.status is ContextStatus.NO_RELEVANT_EVIDENCE
    item = next(i for i in b.items if i.chunk_id == "inca")
    # The text names no country: the Colombian host makes the bare "Santiago" uncertain
    # (a homonym is listed in Colombia), not proof the report is about Colombia.
    assert item.geo.startswith("UNKNOWN") and "homonym's country" in item.geo

def test_without_an_identity_the_same_report_still_passes(identity_off):
    b = bundle("water_quality_notes", [COLOMBIA])
    assert b.status is ContextStatus.READY  # name-only behaviour, unchanged
    assert next(i for i in b.items if i.chunk_id == "inca").geo is None

def test_a_homonym_passage_is_other_and_a_chilean_one_is_relevant(identity_on):
    b = bundle("water_source", [CALI, CHILE, CITY_SOURCE])
    rel = {i.chunk_id: i.relevance["water_source"] for i in b.items if i.kind == "evidence"}
    loc = {i.chunk_id: i.locality["water_source"] for i in b.items if i.kind == "evidence"}
    assert rel.get("cali", []) == [] and loc.get("cali") in (None, "OTHER", "NONE")
    assert CITY_NEARBY in rel["maipo1"]

def test_upstream_evidence_linked_from_the_real_city_is_kept(identity_on):
    s = initialize_city_state("Santiago", "Chile")
    ctx = build_relevance_context(s, [CITY_SOURCE, UPSTREAM, CALI, CAUCA])
    assert "maipo river" in ctx.features
    assert "cauca river" not in ctx.features  # named only in the homonym's sentence
    b = bundle("water_source", [CITY_SOURCE, UPSTREAM, CALI, CAUCA])
    rel = {i.chunk_id: i.relevance["water_source"] for i in b.items if i.kind == "evidence"}
    assert LOCAL_WATER_FEATURE in rel["maipo2"]
    assert not rel.get("cauca")

def test_pdf_page_context_ignores_a_homonym_document(identity_on):
    s = initialize_city_state("Santiago", "Chile")
    ctx = build_relevance_context(s, [COLOMBIA, CHILE])
    assert "https://www.minvivienda.gov.co/inca-2024.pdf" not in ctx.city_pages
    assert ctx.doc_names_city(CHILE)

def test_houston_mississippi_health_department(identity_on):
    ms = chunk("msdh", "Houston water system (Chickasaw County) draws from wells in the Eutaw "
                       "aquifer.", "https://msdh.ms.gov/page/30,0,76.html",
               title="Mississippi public water systems")
    tx = chunk("twdb", "The City of Houston supplies treated water from Lake Houston and Lake "
                       "Conroe.", "https://www.twdb.texas.gov/surfacewater/houston/")
    b = bundle("water_source", [ms, tx], "Houston", "United States of America")
    rel = {i.chunk_id: i.relevance["water_source"] for i in b.items if i.kind == "evidence"}
    assert not rel.get("msdh")
    assert CITY_NEARBY in rel["twdb"]

def test_a_city_domain_of_another_country_is_not_the_citys_website(identity_on):
    ohio = chunk("ohio", "Water quality report for the Village of Lisbon, Ohio, United States. "
                         "Columbiana County.", "https://www.cityoflisbon.net/water-report")
    ctx = build_relevance_context(initialize_city_state("Lisbon", "Portugal"), [ohio])
    assert "cityoflisbon.net" not in ctx.city_hosts
    pt = chunk("pt", "Water quality in Lisbon, Portugal.", "https://www.lisbon.pt/agua")
    ctx = build_relevance_context(initialize_city_state("Lisbon", "Portugal"), [pt])
    assert "lisbon.pt" in ctx.city_hosts  # the city's own domain in its own country

def test_vienna_virginia_town_site(identity_on):
    va = chunk("va", "The Town of Vienna water source is the Potomac River via Fairfax Water.",
               "https://www.viennava.gov/water", source_type="GOVERNMENT")
    b = bundle("water_source", [va], "Vienna", "Austria")
    item = next(i for i in b.items if i.chunk_id == "va")
    assert not item.relevance["water_source"]
    assert DOC_CONTEXT not in item.relevance["water_source"]

# --- validation (CITY_NAMED / WRONG_PLACE) -------------------------------------------------

def test_names_city_uses_the_identity(identity_on):
    assert names_city("Santiago de Cali, Colombia", "Santiago", "Chile") == "OTHER"
    assert names_city("Santiago de Chile", "Santiago", "Chile") == "TARGET"
    assert names_city("Lake Houston reservoir", "Houston", "United States of America") == \
        "NONE"
    cl = geo_match.matcher_for("Santiago", "Chile").host_scope("https://www.ine.gob.cl/x")
    assert names_city("Santiago tenía 7 millones", "Santiago", "Chile", cl) == "TARGET"

def test_names_city_without_identity_is_name_only(identity_off):
    assert names_city("Santiago de Cali, Colombia", "Santiago", "Chile") == "TARGET"
