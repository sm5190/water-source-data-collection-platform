"""Municipal affiliation: MUNICIPAL_VERIFIED only from the city's official host (a statement
there, or a departments-list link naming the organisation); self-declarations, footers,
hostnames and bare links are MUNICIPAL_CANDIDATE. Synthetic identities and hosts."""

from water_source_research.schemas.research import EvidenceChunk
from water_source_research.schemas.utility import UtilityCandidate
from water_source_research.services.affiliation import assess, bootstrap_queries
from water_source_research.services.city_identity import (
    CityIdentity, CountryInfo, NameSource, OfficialWebsite, Region,
)
from water_source_research.services.geo_match import GeoMatcher

P856 = NameSource(source="wikidata", id="Q1", property="P856")
CITY = GeoMatcher(CityIdentity(
    city="Exampleville", country="Canada", status="APPROVED",
    region=Region(name="Ontario", iso_3166_2="CA-ON", verified=True),
    country_info=CountryInfo(name="Canada", iso2="CA", cctld="ca"),
    official_websites=[OfficialWebsite(host="exampleville.ca",
                                       url="https://www.exampleville.ca/", sources=[P856])]))
US_CITY = GeoMatcher(CityIdentity(
    city="Exampleton", country="United States of America", status="APPROVED",
    region=Region(name="Texas", iso_3166_2="US-TX", verified=True),
    country_info=CountryInfo(name="United States of America", iso2="US", cctld="us")))
WATER = UtilityCandidate(key="exampleville water", name="Exampleville Water",
                         bound_hosts=["examplevillewater.org"], origins=["claim"])

def chunk(url, text, cid="c"):
    return EvidenceChunk(chunk_id=cid, url=url, text=text, title="", source_type="OTHER")

def link(target, anchor, heading="", context="", footer=False):
    from urllib.parse import urlsplit
    return {"target_url": target, "target_host": urlsplit(target).hostname.removeprefix("www."),
            "anchor_text": anchor, "context": context or anchor, "heading": heading,
            "in_footer": footer, "observed_at": "2026-11-03T00:00:00Z"}

def status(matcher, chunks, links=None, cand=WATER, city="Exampleville"):
    found = assess(matcher, [cand], chunks, links or {}, city)
    return (found[0].status, found[0].evidence) if found else (None, [])

def test_a_self_declaration_or_footer_alone_is_a_candidate():
    own = chunk("https://www.examplevillewater.org/about", "Exampleville Water is a division "
                "of the City of Exampleville.\n© 2025 City of Exampleville")
    st, ev = status(CITY, [own])
    assert st == "MUNICIPAL_CANDIDATE"
    assert {e.basis for e in ev} == {"SELF_DECLARATION", "COPYRIGHT_FOOTER"}

def test_a_statement_on_the_official_host_verifies():
    gov = chunk("https://www.exampleville.ca/services/water", "Exampleville Water is a "
                "division of the City of Exampleville that treats and delivers drinking water.")
    st, ev = status(CITY, [gov])
    assert st == "MUNICIPAL_VERIFIED"
    e = next(e for e in ev if e.verifies)
    assert e.basis == "OFFICIAL_STATEMENT" and "P856" in e.official_reason

def test_a_departments_list_link_verifies_but_a_bare_link_does_not():
    url = "https://www.exampleville.ca/city-hall/departments"
    listed = {url: [link("https://www.examplevillewater.org/", "Exampleville Water",
                         heading="City Departments", context="Exampleville Water - drinking "
                         "water and sewers")]}
    assert status(CITY, [], listed)[0] == "MUNICIPAL_VERIFIED"
    bare = {"https://www.exampleville.ca/news/x": [link("https://www.examplevillewater.org/",
                                                        "click here")]}
    st, ev = status(CITY, [], bare)
    assert st == "MUNICIPAL_CANDIDATE" and ev[0].basis == "OFFICIAL_LINK_NO_CONTEXT"
    footer = {url: [link("https://www.examplevillewater.org/", "Exampleville Water",
                         heading="City Departments", footer=True)]}
    assert status(CITY, [], footer)[0] == "MUNICIPAL_CANDIDATE"

def test_a_look_alike_or_other_host_never_verifies():
    for host in ("https://www.exampleville-city.com/departments",
                 "https://www.examplevilleontario.org/departments"):
        listed = {host: [link("https://www.examplevillewater.org/", "Exampleville Water",
                              heading="City Departments")]}
        st, _ = status(CITY, [chunk(host, "Exampleville Water is a division of the City "
                                     "of Exampleville.")], listed)
        assert st is None, host  # a look-alike host's statement or link is no signal

def test_a_hostname_hint_alone_is_a_candidate_and_other_states_dont_count():
    dpw = UtilityCandidate(key="exampleton public works", name="Exampleton Public Works",
                           bound_hosts=["examplettonpublicworks.org", "exampletonpublicworks.org"])
    st, ev = status(US_CITY, [], cand=dpw, city="Exampleton")
    assert st == "MUNICIPAL_CANDIDATE" and {e.basis for e in ev} == {"HOSTNAME_HINT"}
    ms = chunk("https://www.exampletonms.gov/x", "Exampleton Public Works is a department of "
               "the City of Exampleton.")
    assert status(US_CITY, [ms], cand=dpw, city="Exampleton")[0] == "MUNICIPAL_CANDIDATE"
    tx = chunk("https://www.exampletontx.gov/departments", "Exampleton Public Works is a "
               "department of the City of Exampleton.")
    assert status(US_CITY, [tx], cand=dpw, city="Exampleton")[0] == "MUNICIPAL_VERIFIED"

def test_the_bootstrap_check_is_planned_once_until_an_official_page_is_acquired():
    found = assess(CITY, [WATER], [chunk("https://www.examplevillewater.org/a", "© City of "
                                         "Exampleville")], {}, "Exampleville")
    q = bootstrap_queries(CITY, found, ["https://www.examplevillewater.org/a"], set())
    assert q == ['site:exampleville.ca "Exampleville Water"']
    assert bootstrap_queries(CITY, found, [], {q[0].casefold()}) == []
    assert bootstrap_queries(CITY, found, ["https://www.exampleville.ca/x"], set()) == []

def test_the_graph_records_department_of_and_weighs_a_departments_pages_as_government(
        monkeypatch):
    from water_source_research.services import geo_match, supply_relationships, utility_registry
    from water_source_research.services.field_state import initialize_city_state
    from water_source_research.services.supply_relationships import build_relationships
    for mod in (geo_match, utility_registry, supply_relationships):
        monkeypatch.setattr(mod, "matcher_for", lambda city, country: CITY)
    own = EvidenceChunk(chunk_id="o", url="https://www.examplevillewater.org/about",
                        text="Exampleville Water supplies drinking water to Exampleville.",
                        title="Exampleville Water", source_type="UTILITY")
    gov = EvidenceChunk(chunk_id="g", url="https://www.exampleville.ca/water",
                        text="Exampleville Water is a division of the City of Exampleville.",
                        title="Water", source_type="GOVERNMENT")
    s = initialize_city_state("Exampleville", "Canada")
    from water_source_research.schemas.research import EvidenceRef, FieldClaim
    s.fields["utility_name"].candidate_claims.append(FieldClaim(
        field_key="utility_name", value="Exampleville Water", confidence=0.8,
        evidence=[EvidenceRef(url=own.url, passage="Exampleville Water supplies")]))
    g = build_relationships(s, [own, gov], {c.url: {"title": c.title, "source_type":
                                                    c.source_type} for c in (own, gov)})
    dept = g.of_kind("DEPARTMENT_OF")
    assert dept and dept[0].status == "VERIFIED"
    assert dept[0].evidence[0].affiliation_basis
    serves = [r for r in g.of_kind("UTILITY_SERVES_CITY") if "Exampleville Water" in
              r.subject.name]
    assert serves and serves[0].status == "VERIFIED"
    assert "MUNICIPAL_DEPARTMENT" in serves[0].basis

def test_the_affiliation_check_uses_one_of_the_rounds_web_slots():
    from water_source_research.discovery.queries import DiscoveryPlan, build_queries
    from water_source_research.services.field_state import load_field_definitions
    fields = load_field_definitions()
    keys = [k for k in fields if not fields[k].derived]
    q = 'site:exampleville.ca "Exampleville Water"'
    base = build_queries(city="Exampleville", country="Canada", utility=None, field_keys=keys,
                         fields=fields, max_per_kind=4, plan=DiscoveryPlan())
    with_check = build_queries(city="Exampleville", country="Canada", utility=None,
                               field_keys=keys, fields=fields, max_per_kind=4,
                               plan=DiscoveryPlan(affiliation_checks=(q,)))
    web = [p.query for p in with_check if p.kind == "web"]
    assert q in web
    base_web = [p for p in base if p.kind == "web"]
    assert len(base_web) == 4  # the budget is full ...
    assert len(web) == 4  # ... so the check replaced one query instead of adding one
