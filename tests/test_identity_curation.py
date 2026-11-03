"""Curation rules of the identity builder (scripts/build_city_identity.py), generic for every
city: a native name that may name a large homonym abroad is unverified; a metropolitan alias is
context, never the city. Synthetic identities; no research answers."""

import importlib.util
from pathlib import Path
import yaml
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.services.city_identity import (
    CityIdentity, CountryInfo, Homonym, IdentityName, NameSource,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_city_identity.py"
spec = importlib.util.spec_from_file_location("build_city_identity", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

OFFICIAL = NameSource(source="wikidata", id="Q1", property="P1448")
ALIAS = NameSource(source="wikidata", id="Q1", property="alias:es")
GN = NameSource(source="geonames", id="1", property="alternatenames")
COUNTRIES = {"AA": {"languages": ["xa", "ya"]}, "BB": {"languages": ["yb", "ya"]}}

def make(names=(), unverified=(), homonyms=(), city="Testopolis"):
    return CityIdentity(city=city, country="Aland", status="DRAFT",
                        country_info=CountryInfo(name="Aland", iso2="AA"),
                        names=list(names), unverified_names=list(unverified),
                        homonyms=list(homonyms))

BIG_ABROAD = Homonym(name="Testopolis", country="Bland", iso2="BB", population=500_000,
                     sources=[GN])
SMALL_ABROAD = Homonym(name="Testopolis", country="Bland", iso2="BB", population=5_000,
                       sources=[GN])

def test_official_only_name_in_a_large_homonyms_language_becomes_unverified():
    shared = IdentityName(name="Testopolis-ya", kind="OFFICIAL_LOCAL", lang="ya",
                          sources=[OFFICIAL])
    own = IdentityName(name="Testopolis-xa", kind="OFFICIAL_LOCAL", lang="xa",
                       sources=[OFFICIAL])
    i = make(names=[shared, own], homonyms=[BIG_ABROAD])
    moved = builder.curate(i, COUNTRIES)
    assert [n.name for n in i.names] == ["Testopolis-xa"]
    assert "Testopolis-ya" in [n.name for n in i.unverified_names]
    assert moved and any("unverified" in m for m in moved)

def test_a_second_source_or_a_small_homonym_keeps_the_name():
    corroborated = IdentityName(name="Testopolis-ya", lang="ya", sources=[OFFICIAL, GN])
    i = make(names=[corroborated], homonyms=[BIG_ABROAD])
    assert builder.curate(i, COUNTRIES) == [] and i.names == [corroborated]
    only = IdentityName(name="Testopolis-ya", lang="ya", sources=[OFFICIAL])
    j = make(names=[only], homonyms=[SMALL_ABROAD])
    assert builder.curate(j, COUNTRIES) == [] and j.names == [only]

def test_metropolitan_aliases_become_context_names():
    i = make(unverified=[IdentityName(name="Gran Testopolis", sources=[ALIAS]),
                         IdentityName(name="Greater Testopolis", sources=[ALIAS]),
                         IdentityName(name="Testopolis metropolitan area", sources=[ALIAS]),
                         IdentityName(name="Ciudad de Testopolis", sources=[ALIAS])])
    builder.curate(i, COUNTRIES)
    assert [c.name for c in i.context_names] == ["Gran Testopolis", "Greater Testopolis",
                                                 "Testopolis metropolitan area"]
    assert all(c.kind == "METRO_AREA" for c in i.context_names)
    assert [n.name for n in i.unverified_names] == ["Ciudad de Testopolis"]

def test_a_city_whose_own_name_starts_with_a_marker_keeps_it():
    i = make(city="Grand Rapids",
             names=[IdentityName(name="Grand Rapids City", sources=[ALIAS, GN])])
    assert builder.curate(i, COUNTRIES) == []

def test_the_identity_file_has_no_metropolitan_alias_as_a_name_and_nothing_approved_by_curation():
    doc = yaml.safe_load((CONFIG_DIR / "city_identity.yaml").read_text(encoding="utf-8"))
    for entry in doc["cities"]:
        ident = CityIdentity.model_validate(entry)
        for n in [*ident.names, *ident.unverified_names]:
            assert not builder._metro_alias(n.name, ident.catalog_names()), (ident.city, n.name)
    assert doc["approval"]["approved_by"] is None
