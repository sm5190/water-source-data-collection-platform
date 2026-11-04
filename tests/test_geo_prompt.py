"""E2: with an approved identity the extraction prompt names City, Region, Country and the
largest homonyms to avoid, says sources may lie outside the city, and tags each passage with
its place (outside the quotable text). Name-only cities keep today's prompt. The target-fields
header stays."""

from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.field_state import initialize_city_state

def chunk(cid, text, url="https://www.example.org/a"):
    return EvidenceChunk(chunk_id=cid, url=url, text=text, title=cid, source_type="OTHER")

def render(city, country, chunks, field="water_source"):
    b = ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field]),
                               initialize_city_state(city, country), chunks)
    return b, b.render()

def test_an_approved_identity_gets_region_homonyms_and_the_outside_note():
    _, text = render("Houston", "United States of America", [chunk(
        "a", "Houston, Texas draws its water from Lake Livingston in Polk County.")])
    city_line = next(line for line in text.splitlines() if line.startswith("City: "))
    assert "Texas" in city_line and "(not: " in city_line and "Mississippi" in city_line
    assert "Water sources may lie outside the city" in text
    assert "## Target fields" in text and "- water_source" in text

def test_passages_carry_a_place_tag_that_is_not_part_of_the_passage():
    b, text = render("Houston", "United States of America", [chunk(
        "a", "Houston, Texas draws its water from Lake Livingston in Polk County.")])
    item = next(i for i in b.items if i.kind == "evidence")
    assert item.place_tag.startswith("place: the city")
    assert "source located in Polk County" in item.place_tag
    assert f"({item.place_tag})" in text and item.place_tag not in item.text

def test_a_name_only_city_keeps_the_previous_prompt(no_identity):
    b, text = render("Houston", "United States of America", [chunk(
        "a", "Houston, Texas draws its water from Lake Livingston.")])
    assert "City: Houston, United States of America\n## Target fields" in text
    assert all(i.place_tag is None for i in b.items if i.kind == "evidence")
    assert "Water sources may lie outside" not in text
