"""E4: validation records where each citation is (its chunk and, only when the quote occurs
verbatim, its character span); elided or re-spaced quotes get a chunk but no span; claim
relations get their own chunk, page and span. Never estimated."""

from resolution_helpers import World, claim, normalized_claim
from water_source_research.schemas.research import ClaimQualifiers

TEXT = "Toronto draws its drinking water from Lake Ontario through four plants."

def test_an_exact_quote_gets_chunk_and_span():
    w = World()
    url = w.doc("https://www.toronto.ca/water", TEXT, source_type="GOVERNMENT")
    c = normalized_claim(w, claim("water_source", "Lake Ontario",
                                  (url, "drinking water from Lake Ontario")))
    ref = c.evidence[0]
    assert ref.chunk_id
    assert TEXT[ref.char_start:ref.char_end] == "drinking water from Lake Ontario"

def test_a_respaced_or_elided_quote_gets_no_span():
    w = World()
    url = w.doc("https://www.toronto.ca/water", TEXT, source_type="GOVERNMENT")
    respaced = normalized_claim(w, claim("water_source", "Lake Ontario",
                                         (url, "drinking  WATER from Lake Ontario")))
    assert respaced.evidence[0].chunk_id and respaced.evidence[0].char_start is None
    elided = normalized_claim(w, claim("water_source", "Lake Ontario",
                                       (url, "Toronto draws … Lake Ontario")))
    assert elided.evidence[0].chunk_id and elided.evidence[0].char_start is None

def test_an_unfound_quote_gets_nothing():
    w = World()
    url = w.doc("https://www.toronto.ca/water", TEXT, source_type="GOVERNMENT")
    c = normalized_claim(w, claim("water_source", "Lake Erie", (url, "from Lake Erie")))
    assert c.evidence[0].chunk_id is None and c.evidence[0].char_start is None

def test_a_relation_gets_its_own_chunk_and_span():
    w = World()
    url = w.doc("https://www.toronto.ca/water", TEXT, source_type="GOVERNMENT")
    c = claim("water_source", "Lake Ontario", (url, "drinking water from Lake Ontario"))
    c.qualifiers = ClaimQualifiers(source_kind="lake", relations=[{
        "relation": "drawn_from", "target": "Lake Ontario", "url": url,
        "quote": "through four plants"}])
    rel = normalized_claim(w, c).qualifiers.relations[0]
    assert rel["chunk_id"] and TEXT[rel["char_start"]:rel["char_end"]] == "through four plants"
