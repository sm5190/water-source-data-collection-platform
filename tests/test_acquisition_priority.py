"""Acquisition order (Milestone 3.1 Phase 1): a UTILITY classification is not a serving
relationship. Candidates below are modelled on Houston run b13e0d1d, where
houstonpublicworks.org was returned 29 times and never fetched while UTILITY-classed pages of
other organisations took the slots. Host names are test data only."""

from water_source_research.acquisition.config import AcquisitionSettings
from water_source_research.acquisition.service import select_candidates
from water_source_research.discovery.normalize import municipal_hint

def cand(url, source_type, **kw):
    return {"url": url, "title": kw.pop("title", url), "source_type": source_type, **kw}

HOUSTON_ROUND = [
    cand("https://precisionutility.biz/water", "UTILITY"),
    cand("https://www.amwater.com/sam-houston/report.pdf", "UTILITY"),
    cand("https://www.nfbwa.com/our-projects", "UTILITY"),
    cand("https://www.westutx.gov/water", "GOVERNMENT"),
    cand("https://houstonpublicworks.org/drinking-water-operations", "OTHER"),
    cand("https://houstonpublicworks.org/wqr.pdf", "TECHNICAL"),
    cand("https://www.twdb.texas.gov/waterplanning/", "GOVERNMENT"),
    cand("https://doi.org/10.1/x", "ACADEMIC"),
]

def urls(chosen):
    return [c["url"] for c in chosen]

def test_city_department_host_is_fetched_before_unverified_utility_pages():
    s = AcquisitionSettings(max_documents_per_round=6, max_per_host=2)
    chosen = urls(select_candidates(HOUSTON_ROUND, set(), s, budget=18, city="Houston"))
    assert chosen[:2] == ["https://www.westutx.gov/water",
                          "https://houstonpublicworks.org/drinking-water-operations"]
    assert "https://houstonpublicworks.org/wqr.pdf" in chosen
    # at most two unverified UTILITY-classified pages per round
    utility = [u for u in chosen if u in {c["url"] for c in HOUSTON_ROUND
                                          if c["source_type"] == "UTILITY"}]
    assert len(utility) == 2

def test_the_hint_needs_the_city_and_a_department_word():
    assert municipal_hint("houstonpublicworks.org", "Houston")
    assert municipal_hint("www.publicworkshouston.com", "Houston")
    assert not municipal_hint("houstonpublicworks.org", "Dallas")
    assert not municipal_hint("precisionutility.biz", "Houston")
    assert not municipal_hint("houstonwater.example", "Houston")  # utility rules decide that
    assert not municipal_hint("houston.publicworks.example", "Houston")  # separate labels

def test_verified_and_candidate_hosts_keep_their_places():
    s = AcquisitionSettings(max_documents_per_round=4, max_per_host=1)
    cands = [cand("https://gov.example/a", "GOVERNMENT"),
             cand("https://named-utility.example/a", "UTILITY"),
             cand("https://verified.example/a", "OTHER"),
             cand("https://tech.example/a.pdf", "TECHNICAL")]
    chosen = urls(select_candidates(cands, set(), s, budget=10,
                                    priority_hosts=["verified.example"],
                                    candidate_hosts=["named-utility.example"]))
    assert chosen == ["https://verified.example/a", "https://gov.example/a",
                      "https://named-utility.example/a", "https://tech.example/a.pdf"]

def test_other_place_pages_stay_last_and_capped():
    s = AcquisitionSettings(max_documents_per_round=6, max_per_host=2)
    cands = [cand("https://msdh.ms.gov/ccr.pdf", "GOVERNMENT", geo_scope="OTHER_REGION"),
             cand("https://other.ms.gov/b.pdf", "GOVERNMENT", geo_scope="OTHER_REGION"),
             cand("https://blog.example/houston", "OTHER")]
    chosen = urls(select_candidates(cands, set(), s, budget=10, city="Houston"))
    assert chosen == ["https://blog.example/houston", "https://msdh.ms.gov/ccr.pdf"]
