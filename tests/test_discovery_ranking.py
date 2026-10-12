import pytest
from discovery_helpers import paper, web
from water_source_research.discovery.base import SearchResult
from water_source_research.discovery.normalize import (
    canonical_url, classify_source_type, iso_date, normalize_doi, normalize_title,
)
from water_source_research.discovery.ranking import merge_and_rank

DOI = "10.1016/j.watres.2019.01.001"

@pytest.mark.parametrize("raw", [
    DOI, DOI.upper(), f"https://doi.org/{DOI}", f"http://dx.doi.org/{DOI}", f"doi:{DOI}",
    f"https://doi.org/{DOI.replace('/', '%2F')}",
])
def test_normalize_doi_variants(raw):
    assert normalize_doi(raw) == DOI

def test_normalize_doi_rejects_non_dois():
    assert normalize_doi("not a doi") is None and normalize_doi(None) is None

def test_canonical_url_ignores_cosmetic_differences():
    a = canonical_url("https://www.toronto.ca/water/?utm_source=x&b=2&a=1#top")
    assert a == canonical_url("http://toronto.ca/water?a=1&b=2") == \
        "https://toronto.ca/water?a=1&b=2"
    assert canonical_url(f"https://doi.org/{DOI}") == f"doi:{DOI}"
    assert canonical_url("not a url") is None

def test_normalize_title():
    assert normalize_title("Drinking-Water Sources of São Paulo!") == \
        normalize_title("drinking water sources of sao paulo")
    assert normalize_title("Home") is None  # too short to dedupe safely

def test_iso_date_parts():
    assert iso_date([[2019, 2, 14]]) == "2019-02-14"
    assert iso_date([[2019, 2]]) == "2019-02"
    assert iso_date([[None]]) is None and iso_date("junk") is None  # type: ignore[arg-type]

@pytest.mark.parametrize(("url", "title", "provider", "expected"), [
    ("https://www.epa.gov/ground-water", "Ground water", "searxng", "GOVERNMENT"),
    ("https://www.ofwat.gov.uk/", "Ofwat regulator", "searxng", "REGULATORY"),
    ("https://www.dep.nyc.gov/", "Water supply", "searxng", "GOVERNMENT"),
    ("https://www.thameswater.co.uk/", "Home", "searxng", "UTILITY"),
    ("https://example.com/", "Denver Water | About", "searxng", "UTILITY"),
    ("https://www.sciencedirect.com/x", "Toronto Water", "searxng", "ACADEMIC"),
    ("https://en.wikipedia.org/wiki/x", "Toronto Water", "searxng", "OTHER"),
    ("https://news.example.com/a", "Toronto drinking water", "searxng", "OTHER"),
    ("https://example.org/plan.pdf", "Master plan", "searxng", "TECHNICAL"),
    ("https://example.org/p", "Some paper", "openalex", "ACADEMIC"),
])
def test_classify_source_type(url, title, provider, expected):
    assert classify_source_type(url, title, provider) == expected

def test_city_domain_is_municipal_only_with_city():
    assert classify_source_type("https://www.toronto.ca/x", "Water", "searxng") == "OTHER"
    assert classify_source_type("https://www.toronto.ca/x", "Water", "searxng",
                                city="Toronto") == "GOVERNMENT"
    assert classify_source_type("https://torontolife.com/x", "Water", "searxng",
                                city="Toronto") == "OTHER"

def test_same_publication_from_three_providers_collapses_by_doi():
    oa = paper("Drinking water sources of Toronto", f"https://doi.org/{DOI.upper()}",
               url="https://www.sciencedirect.com/article/1", rank=2,
               provider_ids={"openalex": "W1"}, published_date="2019")
    s2 = paper("Drinking Water Sources of Toronto", DOI, provider="semantic_scholar",
               url="https://www.semanticscholar.org/paper/abc", rank=1,
               provider_ids={"semantic_scholar": "abc"}, authors=["A. Smith"])
    cr = paper("Drinking water sources of Toronto", DOI, provider="crossref", rank=None,
               published_date="2019-02-14", venue="Water Research", doi_verified=True,
               provider_ids={"crossref": DOI})
    [merged] = merge_and_rank([oa, s2, cr], "Toronto")
    assert set(merged.providers) == {"openalex", "semantic_scholar", "crossref"}
    assert merged.provider_ids == {"openalex": "W1", "semantic_scholar": "abc", "crossref": DOI}
    assert merged.doi == DOI and merged.doi_verified is True
    assert merged.published_date == "2019-02-14" and merged.venue == "Water Research"
    assert merged.authors == ["A. Smith"] and merged.rank == 1
    assert merged.url != f"https://doi.org/{DOI}"  # an original search-hit URL is kept

def test_web_duplicates_collapse_by_canonical_url():
    a = web("Toronto Water", "https://www.toronto.ca/water/?utm_source=ddg", rank=3)
    b = web("Water | City of Toronto", "http://toronto.ca/water", rank=1)
    [merged] = merge_and_rank([a, b], "Toronto")
    assert merged.rank == 1 and merged.providers == ["searxng"]

def test_duplicates_collapse_transitively_by_title():
    a = paper("Water supply of Lima, Peru", "10.1234/lima", rank=1)
    b = paper("Water supply of Lima, Peru", "10.1234/lima", provider="semantic_scholar",
              url="https://s2/1", rank=2)
    c = SearchResult(title="Water Supply of Lima Peru", url="https://repo.example/lima",
                     provider="searxng", rank=4)
    assert len(merge_and_rank([a, b, c], "Lima")) == 1

def test_different_publications_are_not_merged():
    a = paper("Water supply of Lima, Peru", "10.1234/a")
    b = paper("Groundwater of Lima, Peru", "10.1234/b", rank=2)
    assert len(merge_and_rank([a, b], "Lima")) == 2

def test_official_sources_outrank_academic_even_with_worse_provider_rank():
    gov = web("Toronto water supply", "https://www.ontario.gov.on.ca/toronto", rank=9)
    utility = web("Toronto Water", "https://www.torontowater.example", rank=10)
    papers = [paper("Toronto water study", "10.1234/t", provider=p, rank=1)
              for p in ("openalex", "semantic_scholar", "crossref")]
    blog = web("Toronto water blog", "https://blog.example.com/toronto", rank=1)
    ranked = merge_and_rank([blog, *papers, utility, gov], "Toronto")
    assert [r.source_type for r in ranked] == ["GOVERNMENT", "UTILITY", "ACADEMIC", "OTHER"]
    assert ranked[0].score > ranked[2].score > ranked[3].score

def test_city_relevance_demotes_within_and_across_tiers():
    elsewhere = web("Water supply", "https://www.epa.gov/water", rank=1)
    local = web("Toronto water supply", "https://www.epa.gov/toronto", rank=5)
    assert [r.url for r in merge_and_rank([elsewhere, local], "Toronto")] == \
        ["https://www.epa.gov/toronto", "https://www.epa.gov/water"]

def test_per_host_cap_and_limit():
    many = [web(f"Toronto water page {i}", f"https://toronto.ca/p{i}", rank=i)
            for i in range(1, 6)]
    assert len(merge_and_rank(many, "Toronto", max_per_host=2)) == 2
    assert len(merge_and_rank(many, "Toronto", limit=3)) == 3

def test_merging_is_stable_across_rounds():
    first = merge_and_rank([paper("Water supply of Lima, Peru", "10.1234/lima")], "Lima")
    again = merge_and_rank([*[SearchResult.from_dict(r.to_dict()) for r in first],
                            paper("Water supply of Lima, Peru", "10.1234/lima",
                                  provider="semantic_scholar", rank=3)], "Lima")
    assert len(again) == 1 and again[0].providers == ["openalex", "semantic_scholar"]

def test_off_topic_results_without_water_terms_are_demoted():
    housing = paper("Collaborative governance for affordable housing in Toronto", "10.1234/h",
                    rank=1)
    water = paper("Lake Ontario intakes and Toronto's supply", "10.1234/w", rank=8)
    assert [r.doi for r in merge_and_rank([housing, water], "Toronto")] == \
        ["10.1234/w", "10.1234/h"]
