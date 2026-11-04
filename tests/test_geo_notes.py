"""E5: informational geographic notes on claims (approved identity only). They record each
cited passage's place, a source's stated location and review signals, and never change a
validation outcome, resolution or automatic acceptance."""

import pytest
from resolution_helpers import World, claim, normalize
from water_source_research.services import normalization
from water_source_research.services.field_state import initialize_city_state, load_field_definitions
from water_source_research.services.normalization import normalize_research
from water_source_research.services.resolution import resolve_research

FIELDS = load_field_definitions()

def run(world, city, country, *claims):
    s = initialize_city_state(city, country)
    for c in claims:
        s.fields[c.field_key].candidate_claims.append(c)
    n = normalize_research(s, FIELDS, world.index, current_year=2026)
    r, _ = resolve_research(n, FIELDS, world.index)
    return n, r

def first(state, key):
    return state.fields[key].candidate_claims[0]

def test_notes_record_place_location_and_signals():
    w = World()
    url = w.doc("https://www.houstontx.gov/water", "Houston, Texas takes its water from Lake "
                "Livingston in Polk County.", source_type="GOVERNMENT")
    n, _ = run(w, "Houston", "United States of America",
               claim("water_source", "Lake Livingston", (url, "Lake Livingston in Polk County")))
    geo = first(n, "water_source").qualifiers.geo
    assert geo["passages"][0]["place"] in ("CONFIRMED", "PROBABLE")
    assert geo["source_locations"] == ["Polk County"]
    assert "OUTSIDE_SOURCE_LOCATED" in geo["signals"]

def test_a_homonym_passage_is_flagged():
    w = World()
    url = w.doc("https://www.example.org/ms", "Houston, Mississippi takes its water from the "
                "Eutaw Aquifer.", source_type="OTHER")
    n, _ = run(w, "Houston", "United States of America",
               claim("water_source", "Eutaw Aquifer", (url, "the Eutaw Aquifer")))
    assert "PASSAGE_OTHER_PLACE" in first(n, "water_source").qualifiers.geo["signals"]

def test_name_only_cities_get_no_notes(no_identity):
    w = World()
    url = w.doc("https://www.toronto.ca/water", "Toronto takes its water from Lake Ontario.",
                source_type="GOVERNMENT")
    n = normalize(w, claim("water_source", "Lake Ontario", (url, "from Lake Ontario")))
    assert first(n, "water_source").qualifiers.geo is None

@pytest.mark.parametrize("city,country,text,value,field", [
    ("Toronto", "Canada", "Toronto's drinking water comes from Lake Ontario.", "Lake Ontario",
     "water_source"),
    ("Houston", "United States of America",
     ("Houston Public Works supplies drinking water to Houston, Texas."),
     "Houston Public Works", "utility_name"),
])
def test_notes_never_change_validation_or_resolution(monkeypatch, city, country, text, value,
                                                     field):
    w = World()
    url = w.doc("https://www.example.gov/x", text, source_type="GOVERNMENT")
    c = claim(field, value, (url, text.rstrip(".")))
    with_notes = run(w, city, country, c.model_copy(deep=True))
    monkeypatch.setattr(normalization, "geo_notes", lambda *a, **k: None)
    without = run(w, city, country, c.model_copy(deep=True))
    a, b = first(with_notes[0], field), first(without[0], field)
    assert a.validation.outcome == b.validation.outcome
    assert [(k.code, k.result) for k in a.validation.checks] == \
        [(k.code, k.result) for k in b.validation.checks]
    assert with_notes[1].fields[field].status == without[1].fields[field].status
