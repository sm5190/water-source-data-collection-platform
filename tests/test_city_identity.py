"""City identity (config/city_identity.yaml): provenance on every name, two independent
sources (or an official/native name) before a name is used, metropolitan areas kept apart from
the city, drafts unused by research, and no workbook answers in the catalog."""

import ast
from pathlib import Path
import pytest
import yaml
from pydantic import ValidationError
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.services.city_catalog import CatalogCity, load_city_catalog
from water_source_research.services.city_identity import (
    CityIdentity, ContextName, IdentityName, NameSource, Region, catalog_only, fold, identity_for,
    load_city_identities,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "water_source_research"
GN = NameSource(source="geonames", id="1", property="alternatenames")
WD = NameSource(source="wikidata", id="Q1", property="alias:es")
WD_OFFICIAL = NameSource(source="wikidata", id="Q1", property="P1448")

def ident(**kw) -> CityIdentity:
    base = {"city": "Santiago", "country": "Chile", "status": "APPROVED",
            "region": Region(name="Santiago Metropolitan Region", verified=True)}
    return CityIdentity(**(base | kw))

# --- the model's rules ------------------------------------------------------------------------

def test_a_single_source_name_cannot_be_used_for_matching():
    with pytest.raises(ValidationError, match="independent confirmation"):
        ident(names=[IdentityName(name="Gran Santiago", sources=[WD])])
    ok = ident(unverified_names=[IdentityName(name="Gran Santiago", sources=[WD])])
    assert "Gran Santiago" not in ok.city_names()

def test_two_sources_or_an_official_name_confirm_a_name():
    two = IdentityName(name="Santiago de Chile", sources=[WD, GN])
    official = IdentityName(name="Santiago de Chile", kind="OFFICIAL_LOCAL", sources=[WD_OFFICIAL])
    assert two.independently_confirmed and official.independently_confirmed
    assert ident(names=[two]).city_names() == ["Santiago", "Santiago de Chile"]

def test_every_name_needs_provenance():
    with pytest.raises(ValidationError):
        IdentityName(name="Santiago de Chile", sources=[])

def test_metropolitan_areas_are_context_not_the_city():
    i = ident(context_names=[ContextName(name="Greater Santiago", sources=[WD])])
    assert "Greater Santiago" in i.context_place_names()
    assert "Greater Santiago" not in i.city_names()

def test_a_name_shared_with_the_region_is_not_an_unambiguous_city_name():
    ny = CityIdentity(city="New York City", country="United States of America",
                      names=[IdentityName(name="New York", sources=[WD, GN],
                                          ambiguous_with=["region"])])
    assert "New York" in ny.city_names()
    assert "New York" not in ny.unambiguous_city_names()

def test_catalog_parenthesised_names_are_both_catalog_names():
    pune = CityIdentity(city="Pune (Poona)", country="India")
    assert pune.catalog_names() == ["Pune", "Poona"]

def test_drafts_are_used_only_when_explicitly_allowed():
    draft = ident(status="DRAFT")
    assert identity_for("Santiago", "Chile", identities=(draft,)).status == "CATALOG_ONLY"
    assert identity_for("Santiago", "Chile", identities=(draft,), allow_draft=True) is draft
    approved = ident()
    assert identity_for(" santiago ", "CHILE", identities=(approved,)) is approved

def test_a_shared_city_name_in_another_country_is_not_the_identity():
    approved = ident()
    assert identity_for("Santiago", "Mexico", identities=(approved,)).status == "CATALOG_ONLY"

def test_catalog_only_identity_has_city_and_country_and_nothing_else():
    c = catalog_only("Houston", "United States of America")
    assert c.city_names() == ["Houston"] and c.region is None and not c.names

def test_fold():
    assert fold("Bogotá  D.C.") == "bogota d.c." and fold("Santiago-de-Chile") == fold(
        "santiago de chile")

# --- the drafted file ---------------------------------------------------------------------

IDENTITIES = load_city_identities()

def test_the_file_describes_exactly_the_catalog_cities():
    catalog = {(c.city, c.country) for c in load_city_catalog()}
    assert {(i.city, i.country) for i in IDENTITIES} == catalog

def test_every_entry_cites_only_independent_geographic_sources():
    allowed = {"geonames", "wikidata", "iso3166", "person"}
    for i in IDENTITIES:
        for n in [*i.names, *i.unverified_names, *i.context_names, *i.homonyms,
                  *((i.region.names if i.region else []))]:
            assert n.sources and {s.source for s in n.sources} <= allowed, (i.city, n)
        if i.region:
            assert i.region.sources, i.city

def test_region_terms_are_first_level_terms_of_the_country():
    data = yaml.safe_load((CONFIG_DIR / "admin_levels.yaml").read_text(encoding="utf-8"))
    by_country = {}
    for name, entry in data["countries"].items():
        for key in [name, *entry.get("aliases", [])]:
            by_country[fold(key)] = [fold(t) for t in entry.get("first_level", [])]
    for i in IDENTITIES:
        if i.region and i.region.level_term:
            assert fold(i.region.level_term) in by_country.get(fold(i.country), []), i.city

def test_known_ambiguous_names_are_drafted_as_expected():
    """Regression expectations only (identity, not research answers)."""
    by = {(i.city, i.country): i for i in IDENTITIES}
    santiago = by[("Santiago", "Chile")]
    assert santiago.region.iso_3166_2 == "CL-RM" and santiago.region.verified
    assert "Santiago de Chile" in santiago.city_names()
    assert "Gran Santiago" not in santiago.city_names()  # a metropolitan name
    homonyms = {(h.name, h.iso2) for h in santiago.homonyms}
    assert {("Santiago de Cali", "CO"), ("Santiago de Querétaro", "MX")} <= homonyms
    cali = by[("Cali", "Colombia")]
    assert "Santiago de Cali" in cali.city_names()
    houston = by[("Houston", "United States of America")]
    assert houston.region.iso_3166_2 == "US-TX"
    assert ("Houston", "US", "Mississippi") in {(h.name, h.iso2, h.region)
                                                 for h in houston.homonyms}
    assert "Greater Houston" in houston.context_place_names()
    assert "Greater Houston" not in houston.city_names()
    hyderabad = by[("Hyderabad", "India")]
    assert hyderabad.region.name == "Telangana"
    assert ("Hyderabad", "PK") in {(h.name, h.iso2) for h in hyderabad.homonyms}
    vienna = by[("Vienna", "Austria")]
    assert ("Vienna", "US", "Virginia") in {(h.name, h.iso2, h.region) for h in vienna.homonyms}

# --- no workbook answers reach research ---------------------------------------------------

def test_the_catalog_holds_identity_only():
    data = yaml.safe_load((CONFIG_DIR / "cities.yaml").read_text(encoding="utf-8"))
    for c in data["cities"]:
        assert set(c) <= {"city", "country", "aliases", "source"}, c
    with pytest.raises(ValidationError):
        CatalogCity(city="X", country="Y", jurisdiction="Z")
    with pytest.raises(ValidationError):
        CatalogCity(city="X", country="Y", latitude=1.0)

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out

def test_identity_data_never_fills_a_field_value():
    """Modules that produce or decide field values don't read the identity file."""
    for rel in ("services/supply_records.py", "services/resolution.py", "services/export.py",
                "services/rules_engine.py", "services/review.py", "llm/extraction.py"):
        assert "water_source_research.services.city_identity" not in _imports(SRC / rel), rel

def test_the_identity_build_reads_only_city_and_country_from_the_catalog():
    text = (Path(__file__).resolve().parents[1] / "scripts" / "build_city_identity.py"
            ).read_text(encoding="utf-8")
    assert 'entries = [{"city": e["city"], "country": e["country"]} for e in catalog]' in text
    assert "openpyxl" not in text and "xlsx" not in text.replace("cities.yaml", "")

# --- official city hosts (municipal affiliation) ---------------------------------------------

def test_official_hosts_need_the_identity_or_a_controlled_suffix():
    from water_source_research.services.city_identity import CountryInfo, OfficialWebsite
    from water_source_research.services.geo_match import GeoMatcher
    site = OfficialWebsite(host="exampleville.ca", url="https://www.exampleville.ca/",
                           sources=[NameSource(source="wikidata", id="Q1", property="P856")])
    ca = GeoMatcher(CityIdentity(city="Exampleville", country="Canada", status="APPROVED",
                                 country_info=CountryInfo(name="Canada", iso2="CA",
                                                          cctld="ca"),
                                 official_websites=[site]))
    assert ca.official_host("https://www.exampleville.ca/water")
    assert ca.official_host("maps.exampleville.ca")  # a subdomain of the official site
    assert ca.official_host("exampleville-city.com") is None  # spelling on an open suffix
    us = GeoMatcher(CityIdentity(
        city="Exampleton", country="United States of America", status="APPROVED",
        region=Region(name="Texas", iso_3166_2="US-TX", verified=True),
        country_info=CountryInfo(name="United States of America", iso2="US", cctld="us")))
    assert us.official_host("https://www.exampletontx.gov/")
    assert us.official_host("cityofexampleton.gov")
    assert us.official_host("exampletonms.gov") is None  # another state's place
    ke = GeoMatcher(CityIdentity(city="Samplecity", country="Kenya", status="APPROVED",
                                 country_info=CountryInfo(name="Kenya", iso2="KE", cctld="ke")))
    assert ke.official_host("samplecity.go.ke") and ke.official_host("x.samplecity.go.ke")
    assert ke.official_host("samplecity.co.ke") is None  # not a government suffix
    assert ke.official_host("samplecity.go.tz") is None
