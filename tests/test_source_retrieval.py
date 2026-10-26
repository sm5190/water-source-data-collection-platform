"""Retrieval for the source fields, on verbatim passages from saved runs (fixtures) plus
synthetic counter-cases. The rules are generic — locality judged on the matching sentences,
named water bodies, a per-source cap relaxed only for strong city-specific chunks — and the
counter-cases check they don't fire where they shouldn't."""

import json
from pathlib import Path
import pytest
from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
from water_source_research.context_manager.manager import (
    _SENTENCE_SPLIT, HYDRONYM, _locality, _place_patterns,
)
from water_source_research.services.field_state import initialize_city_state

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "source_chunks.json")
                      .read_text(encoding="utf-8"))["chunks"]

def city_chunks(city):
    return [{**c, "retrieved_at": None} for c in FIXTURES if c["city"] == city]

def distractors(city, n=40):
    """Generic competition: long passages full of source-field keywords about other places
    or about nothing in particular."""
    out = []
    for i in range(n):
        place = ["Johannesburg", "Kinshasa", "Lagos", "Cairo"][i % 4]
        out.append({"chunk_id": f"d{i}", "url": f"https://report.example/{i % 3}",
                    "title": "Regional groundwater report", "source_type": "TECHNICAL",
                    "page_number": i, "retrieved_at": None,
                    "text": f"In {place}, groundwater from the wellfield and aquifer sources "
                            f"supplied by boreholes is the main raw water source; abstraction "
                            f"from rivers and springs varies {i}."})
    return out

def bundle(city, country, chunks, field="water_source"):
    state = initialize_city_state(city, country)
    return ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field]), state, chunks)

def selected(b):
    return [i.chunk_id for i in b.items if i.kind == "evidence"]

@pytest.mark.parametrize("city, country, must_include", [
    ("Luanda", "Angola", {"21afafb3", "ac1ca9f2"}),          # "Bengo and Cuanza Rivers"
    ("Vienna", "Austria", {"c2f6ea98", "c17ae44d"}),         # spring mains, Lobau waterworks
    ("Houston", "United States of America", {"6c31275a", "21a1f416"}),  # Evangeline & Chicot
    ("Nairobi", "Kenya", {"4de24f6a"}),                      # "Chania and Thika rivers"
])
def test_named_source_evidence_reaches_the_bundle(city, country, must_include):
    b = bundle(city, country, city_chunks(city) + distractors(city))
    chosen = {c[:8] for c in selected(b)}
    assert chosen & must_include, (city, chosen)
    # distractors about other places never outrank the city's own named-source passages
    first = selected(b)[0]
    assert not first.startswith("d"), first

def test_a_multi_city_running_header_does_not_make_another_city_local():
    target, other = _place_patterns("Luanda", "Angola")
    def place(prefix):
        text = next(c for c in FIXTURES if c["chunk_id"].startswith(prefix))["text"]
        return _locality([s for s in _SENTENCE_SPLIT.split(text) if s.strip()], [HYDRONYM],
                         target, other)
    # the header names Luanda on every page; the wellfield sentence is about South Africa
    assert place("53592aa3") == "OTHER"
    assert place("ac1ca9f2") == "TARGET"  # "... Bengo and Cuanza Rivers. ... Luanda ..."

def test_counter_case_another_citys_river_is_not_seeded_for_this_city():
    chunks = [{"chunk_id": "x1", "url": "https://elsewhere.example", "title": "Rivers",
               "source_type": "GOVERNMENT", "page_number": None, "retrieved_at": None,
               "text": "Johannesburg is supplied from the Vaal River. The Vaal Dam stores "
                       "water for Gauteng."},
              {"chunk_id": "x2", "url": "https://local.example", "title": "Luanda water",
               "source_type": "UTILITY", "page_number": None, "retrieved_at": None,
               "text": "Luanda's water supply comes from the Kwanza River intake."}]
    target, other = _place_patterns("Luanda", "Angola")
    assert _locality(_SENTENCE_SPLIT.split(chunks[0]["text"]), [HYDRONYM], target,
                     other) == "OTHER"
    b = bundle("Luanda", "Angola", chunks)
    assert selected(b)[0] == "x2"
    scores = {i.chunk_id: i.score for i in b.items if i.kind == "evidence"}
    assert scores.get("x1", 0) < scores["x2"] * 0.5

def test_counter_case_no_named_water_body_no_seed():
    chunks = [{"chunk_id": "y1", "url": "https://u.example", "title": "Water",
               "source_type": "UTILITY", "page_number": None, "retrieved_at": None,
               "text": "Luanda's water supply is drawn from surface water sources."}]
    b = bundle("Luanda", "Angola", chunks)
    assert b.stats.get("hydronym_seeds", 0) == 0
    assert selected(b) == ["y1"]  # still retrievable through the field's keywords

def test_retrieval_never_fills_the_budget_with_irrelevant_text():
    chunks = city_chunks("Nairobi") + [
        {"chunk_id": f"z{i}", "url": f"https://misc.example/{i}", "title": "Misc",
         "source_type": "OTHER", "page_number": None, "retrieved_at": None,
         "text": f"Annual meeting minutes {i}: budget approved, staff training planned."}
        for i in range(30)]
    b = bundle("Nairobi", "Kenya", chunks)
    assert not any(c.startswith("z") for c in selected(b))
    assert b.stats["tokens_used"] < b.stats["budget_tokens"]
