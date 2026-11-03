"""The reserved acquisition slot (Milestone 3.1 Phase 2): at most one of a round's slots for a
credible unverified utility candidate (a placed service statement, or a utility_name claim
whose quote places the city). A UTILITY classification, a water hostname or a name only
mined from text never qualifies; unused, the slot is filled normally, and official, technical
and academic pages keep their places. Sentences and hosts are synthetic test data (modelled on
the stored Houston run); production code holds no utility names or domains."""

import pytest
from water_source_research.acquisition.config import AcquisitionSettings
from water_source_research.acquisition.service import select_candidates
from water_source_research.agents.nodes import reserved_candidates, reserved_pages
from water_source_research.schemas.research import EvidenceChunk, EvidenceRef, FieldClaim
from water_source_research.services import geo_match
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.utility_registry import build_registry

CITY, COUNTRY = "Houston", "United States of America"
S = AcquisitionSettings(max_documents_per_round=6, max_per_host=2)

@pytest.fixture(autouse=True)
def identity_on(monkeypatch):
    monkeypatch.setenv("CITY_IDENTITY_ALLOW_DRAFT", "1")
    geo_match._matcher.cache_clear()
    yield
    geo_match._matcher.cache_clear()

def chunk(url, text, source_type="OTHER", title=""):
    return EvidenceChunk(chunk_id=str(abs(hash((url, text))) % 10**8), url=url, text=text,
                         title=title, source_type=source_type)

def cand(url, source_type, title=None):
    return {"url": url, "title": title or url, "source_type": source_type}

def state(chunks, discovery, claims=()):
    research = initialize_city_state(CITY, COUNTRY)
    for value, url, quote in claims:
        research.fields["utility_name"].candidate_claims.append(FieldClaim(
            field_key="utility_name", value=value, confidence=0.8,
            evidence=[EvidenceRef(url=url, passage=quote)]))
    reg = build_registry(research, chunks)
    return research, {"research_state": research.model_dump(mode="json"),
                      "evidence_chunks": [c.model_dump(mode="json") for c in chunks],
                      "utility_registry": reg.model_dump(mode="json"),
                      "discovery_candidates": discovery, "suppressed_hosts": []}

OFFICIAL = [cand(f"https://agency{i}.texas.gov/water.pdf", "GOVERNMENT") for i in range(5)] + [
    cand("https://www.tceq.texas.gov/drinkingwater/b", "REGULATORY"),
    cand("https://www.twdb.texas.gov/surfacewater/c", "GOVERNMENT")]
WHOLESALER_PAGE = cand("https://www.coastalwaterauthority.org/",
                       "UTILITY", "Coastal Water Authority - Harris, Chambers, Liberty Counties")
STATEMENT = chunk("https://www.example-news.com/houston-water",
                  "The Coastal Water Authority supplies untreated raw water to Houston, Texas, "
                  "from Lake Livingston.")

def chosen_urls(st, research, pool):
    pages, _ = reserved_pages(st, research)
    return [c["url"] for c in select_candidates(pool, set(), S, budget=18, city=CITY,
                                                reserved=pages)], pages

def test_a_wholesaler_with_a_placed_statement_gets_one_slot():
    research, st = state([STATEMENT], OFFICIAL + [WHOLESALER_PAGE])
    cands = reserved_candidates(st)
    assert [c["name"] for c in cands] == ["Coastal Water Authority"]
    assert "supplies_wholesale" in cands[0]["_reason"]
    urls, pages = chosen_urls(st, research, OFFICIAL + [WHOLESALER_PAGE])
    assert urls[0] == WHOLESALER_PAGE["url"] and len(urls) == 6
    assert "host spells" in pages[0]["_reserved_reason"]  # bootstrap: no own page yet
    # the other five are still the official pages, in their order
    assert urls[1:] == [c["url"] for c in OFFICIAL[:5]]

def test_without_the_reservation_the_wholesaler_waits_behind_official_pages():
    urls = [c["url"] for c in select_candidates(OFFICIAL + [WHOLESALER_PAGE], set(), S,
                                                budget=18, city=CITY)]
    assert WHOLESALER_PAGE["url"] not in urls

def test_a_claim_placing_the_city_qualifies():
    _, st = state([], OFFICIAL + [WHOLESALER_PAGE], claims=[(
        "Coastal Water Authority", "https://www.example-news.com/x",
        "Coastal Water Authority conveys raw water to Houston, Texas")])
    cands = reserved_candidates(st)
    assert cands and "claim" in cands[0]["_reason"]

def test_a_claim_naming_a_homonym_place_does_not_qualify():
    _, st = state([], OFFICIAL + [WHOLESALER_PAGE], claims=[(
        "Coastal Water Authority", "https://www.example-news.com/x",
        "Coastal Water Authority serves Houston, Mississippi")])
    assert reserved_candidates(st) == []

@pytest.mark.parametrize("text,host,title", [
    # a vendor serving utilities (no potable service statement)
    ("Precision Utility Services reads meters for utilities near Houston.",
     "https://precisionutility.biz/clients", "Precision Utility Services"),
    # a commercial "<X> Water Solutions" company mentioned with the city
    ("Gulf Water Solutions held a conference in Houston, Texas.",
     "https://gulfwatersolutions.com/", "Gulf Water Solutions"),
    # a UTILITY-classified page of a utility without any statement
    ("Welcome to Sample Water Authority. Pay your bill online.",
     "https://www.samplewaterauthority.org/", "Sample Water Authority"),
    # a contractor that builds plants
    ("Delta Water Company designed and built the treatment plant for Houston, Texas.",
     "https://deltawaterco.com/", "Delta Water Company"),
    # a utility serving another place
    ("Baytown Water Authority supplies drinking water to Baytown residents.",
     "https://baytownwaterauthority.org/", "Baytown Water Authority"),
])
def test_unrelated_water_companies_get_no_slot(text, host, title):
    page = cand(host, "UTILITY", title)
    research, st = state([chunk(host, text, "UTILITY", title)], OFFICIAL + [page])
    assert reserved_candidates(st) == []
    urls, pages = chosen_urls(st, research, OFFICIAL + [page])
    assert pages == [] and urls == [c["url"] for c in OFFICIAL[:6]]

def test_a_water_hostname_alone_gets_no_slot():
    page = cand("https://www.houstonwater.example/", "UTILITY", "Houston Water Info")
    research, st = state([], OFFICIAL + [page])
    pages, why = reserved_pages(st, research)
    assert pages == [] and why == "no qualifying utility candidate"

def test_technical_and_academic_pages_keep_the_remaining_slots():
    pool = [cand("https://www.houstontx.gov/a.pdf", "GOVERNMENT"),
            cand("https://www.usgs.gov/b", "GOVERNMENT"),
            WHOLESALER_PAGE,
            cand("https://www.engineering-report.example/c", "TECHNICAL"),
            cand("https://doi.org/10.1/d", "ACADEMIC"),
            cand("https://doi.org/10.1/e", "ACADEMIC"),
            cand("https://blog.example/f", "OTHER")]
    research, st = state([STATEMENT], pool)
    urls, _ = chosen_urls(st, research, pool)
    assert urls[0] == WHOLESALER_PAGE["url"]
    assert {"https://www.houstontx.gov/a.pdf", "https://www.usgs.gov/b",
            "https://www.engineering-report.example/c", "https://doi.org/10.1/d"} <= set(urls)
    assert len(urls) == 6

def test_the_slot_never_exceeds_the_round_or_run_budget():
    research, st = state([STATEMENT], OFFICIAL + [WHOLESALER_PAGE])
    pages, _ = reserved_pages(st, research)
    one = select_candidates(OFFICIAL + [WHOLESALER_PAGE], set(), S, budget=1, city=CITY,
                            reserved=pages)
    assert [c["url"] for c in one] == [WHOLESALER_PAGE["url"]]
    assert len(select_candidates(OFFICIAL + [WHOLESALER_PAGE], set(), S, budget=18, city=CITY,
                                 reserved=pages * 3)) == 6
    from water_source_research.acquisition.service import doc_key
    attempted = {doc_key(WHOLESALER_PAGE["url"])}
    again = select_candidates(OFFICIAL + [WHOLESALER_PAGE], attempted, S, budget=18,
                              city=CITY, reserved=pages)
    assert WHOLESALER_PAGE["url"] not in [c["url"] for c in again] and len(again) == 6

# --- precision lessons from the stored runs (counterfactual on Houston b13e0d1d) ---------------

def test_a_city_named_claim_never_matches_a_longer_company_host():
    """A "Houston Water" claim (the city's own utility system) must not reserve the slot for
    houstonwatersolutions.net, a commercial company whose host only contains the name."""
    page = cand("https://houstonwatersolutions.net/did-you-know/", "UTILITY",
                "Did You Know Where Houston Water Comes From? - Houston Water Solutions")
    research, st = state([], OFFICIAL + [page], claims=[(
        "Houston Water", "https://www.houstontx.gov/x",
        "Houston's combined utility system, Houston Water, serves Houston, Texas")])
    pages, why = reserved_pages(st, research)
    assert pages == [] and "no page" in why

def test_the_utilitys_own_compact_host_matches():
    from water_source_research.services.utility_registry import candidate_page
    reg = {"key": "nairobi water company", "name": "Nairobi Water Company"}
    assert candidate_page(reg, "https://www.nairobiwater.co.ke/plan.pdf",
                          "Nairobi Water Company Strategic Plan", "Nairobi", "Kenya")
    assert candidate_page(reg, "https://www.nairobiwaterservices.example/",
                          "Nairobi Water Company", "Nairobi", "Kenya") is None

def test_a_commercial_or_news_host_is_never_a_bootstrap_page():
    page = {**WHOLESALER_PAGE, "publisher_kind": "NEWS"}
    research, st = state([STATEMENT], OFFICIAL + [page])
    pages, _ = reserved_pages(st, research)
    assert pages == []

def test_a_statement_placed_only_by_its_document_does_not_qualify():
    """A first-person planning sentence that names no place, on a page that mentions the city
    elsewhere, is not city-supply relationship evidence."""
    own = chunk("https://www.northbendwater.example/projects",
                "Houston, Texas area.\n\nNorth Bend Water Authority supplies drinking water "
                "through 30 miles of new lines.", "UTILITY", "North Bend Water Authority")
    _, st = state([own], OFFICIAL)
    names = [c["name"] for c in reserved_candidates(st)]
    assert "North Bend Water Authority" not in names
