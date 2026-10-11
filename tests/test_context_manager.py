import pytest
from water_source_research.acquisition.base import AcquiredDocument
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import EvidenceChunk, EvidenceRef, FieldClaim
from water_source_research.context_manager import (
    ContextManager, ContextPurpose, ContextTask,
)
from water_source_research.services.evidence import chunk_document
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)

UTILITY_TEXT = (
    "Toronto Water is the municipal utility responsible for drinking water in Toronto. "
    "It operates four treatment plants drawing from Lake Ontario."
)
POPULATION_TEXT = "Toronto had a population of 2.79 million residents in the 2021 census."
IRRELEVANT_TEXT = "The annual film festival attracts visitors from around the world."

def chunk(cid, text, url=None, source_type="UTILITY"):
    return EvidenceChunk(
        chunk_id=cid, url=url or f"https://example.org/{cid}", text=text, title=cid,
        source_type=source_type,
    )

def claim(value, confidence, url, passage):
    return FieldClaim(
        field_key="x", value=value, confidence=confidence,
        evidence=[EvidenceRef(url=url, title="t", passage=passage)],
    )

@pytest.fixture
def state():
    return initialize_city_state("Toronto", "Canada")

@pytest.fixture
def mgr():
    return ContextManager()

def extract(fields, max_tokens=None):
    return ContextTask(ContextPurpose.EXTRACT, fields, max_tokens)

def test_field_definitions_carry_metadata():
    defs = load_field_definitions()
    assert len(defs) == 32
    assert defs["percentage_total_population"].derived
    assert "population_served_millions" in defs["percentage_total_population"].depends_on
    assert all(d.keywords for d in defs.values())

def test_chunk_document_is_paragraph_aligned_and_stable():
    doc = AcquiredDocument("https://u.org", "T", "text/html", "a" * 30 + "\n\n" + "b" * 30,
                           "DIRECT_HTTP")
    chunks = chunk_document(doc, source_type="UTILITY", max_chars=40)
    assert [c.text for c in chunks] == ["a" * 30, "b" * 30]
    assert chunks[0].chunk_id == chunk_document(doc, max_chars=40)[0].chunk_id
    assert chunks[0].source_type == "UTILITY"

def test_unknown_field_raises(state, mgr):
    with pytest.raises(ValueError):
        mgr.build(extract(["not_a_field"]), state)

def test_resolved_and_derived_targets_are_skipped(state, mgr):
    state.fields["utility_name"].status = FieldResolutionStatus.RESOLVED
    bundle = mgr.build(extract(["utility_name", "percentage_total_population"]), state)
    assert bundle.is_empty
    assert bundle.render() == ""
    assert set(bundle.skipped_fields) == {"utility_name", "percentage_total_population"}

def test_relevant_chunks_selected_and_irrelevant_excluded(state, mgr):
    chunks = [chunk("film", IRRELEVANT_TEXT), chunk("util", UTILITY_TEXT)]
    bundle = mgr.build(extract(["utility_name"]), state, chunks)
    evidence = [i for i in bundle.items if i.kind == "evidence"]
    assert [i.chunk_id for i in evidence] == ["util"]
    assert bundle.stats["chunks_irrelevant"] == 1
    assert "## Evidence" in bundle.render()

def test_authoritative_source_outranks_other(state, mgr):
    chunks = [
        chunk("blog", UTILITY_TEXT + " Blog.", source_type="OTHER"),
        chunk("gov", UTILITY_TEXT + " Gov.", source_type="GOVERNMENT"),
    ]
    bundle = mgr.build(extract(["utility_name"]), state, chunks)
    evidence = [i for i in bundle.items if i.kind == "evidence"]
    # Near-identical text: only the higher-weighted source survives deduplication.
    assert [i.chunk_id for i in evidence] == ["gov"]

def test_token_budget_respected(state, mgr):
    chunks = [chunk(f"c{i}", UTILITY_TEXT + f" Variant number {i} " + "x" * 400)
              for i in range(20)]
    bundle = mgr.build(extract(["utility_name"], max_tokens=600), state, chunks)
    assert bundle.stats["tokens_used"] <= 600
    assert bundle.stats["dropped_budget"] > 0

def test_near_duplicates_dropped(state, mgr):
    chunks = [chunk("a", UTILITY_TEXT), chunk("b", UTILITY_TEXT + " ")]
    bundle = mgr.build(extract(["utility_name"]), state, chunks)
    assert bundle.stats["chunks_selected"] == 1
    assert bundle.stats["dropped_duplicate"] == 1

def test_per_source_cap(state):
    mgr = ContextManager(max_chunks_per_source=2)
    texts = [f"{UTILITY_TEXT} Distinct paragraph {i} about plant {i} capacity and staff {i}."
             for i in range(5)]
    chunks = [chunk(f"c{i}", t, url="https://same.org") for i, t in enumerate(texts)]
    bundle = mgr.build(extract(["utility_name"]), state, chunks)
    assert bundle.stats["chunks_selected"] == 2
    assert bundle.stats["dropped_source_cap"] == 3
    assert len(bundle.sources) == 1

def test_seed_pass_gives_each_target_evidence(state, mgr):
    utility_chunks = [chunk(f"u{i}", f"{UTILITY_TEXT} Detail {i} on utility governance {i}.")
                      for i in range(6)]
    chunks = utility_chunks + [chunk("pop", POPULATION_TEXT)]
    bundle = mgr.build(
        extract(["utility_name", "city_population_millions"], max_tokens=300), state, chunks,
    )
    covered = {k for i in bundle.items if i.kind == "evidence" for k in i.field_keys}
    assert covered == {"utility_name", "city_population_millions"}

def test_seen_pairs_excluded_and_consumed_reported(state, mgr):
    chunks = [chunk("util", UTILITY_TEXT)]
    first = mgr.build(extract(["utility_name"]), state, chunks)
    assert first.consumed == {("util", "utility_name")}
    second = mgr.build(extract(["utility_name"]), state, chunks, seen=first.consumed)
    assert not [i for i in second.items if i.kind == "evidence"]

def test_long_chunk_excerpted_to_matching_sentences(state, mgr):
    filler = " ".join(f"Sentence {i} talks about parks and libraries." for i in range(60))
    text = filler + " " + UTILITY_TEXT + " " + filler
    bundle = mgr.build(extract(["utility_name"]), state, [chunk("long", text)])
    passage = bundle.items[-1].text
    assert "Toronto Water is the municipal utility" in passage
    assert passage.startswith("…") and passage.endswith("…")
    assert len(passage) < len(text) / 5

def test_known_facts_include_resolved_dependencies_with_provenance(state, mgr):
    fs = state.fields["population_served_millions"]
    fs.status = FieldResolutionStatus.RESOLVED
    fs.selected_claim = claim(2.8, 0.9, "https://toronto.ca/r", "serves 2.8 million")
    bundle = mgr.build(ContextTask(ContextPurpose.VALIDATE, ["percentage_total_population"]),
                       state)
    # No claim exists for the derived field, so there is nothing to validate.
    assert bundle.is_empty
    state.fields["percentage_total_population"].candidate_claims = [
        claim(100.0, 0.7, "https://calc", "2.8 / 2.79")
    ]
    bundle = mgr.build(ContextTask(ContextPurpose.VALIDATE, ["percentage_total_population"]),
                       state)
    rendered = bundle.render()
    assert "- population_served_millions = 2.8 [S1]" in rendered
    assert "top candidate" in rendered

def test_all_conflict_candidates_kept_under_tight_budget(state, mgr):
    fs = state.fields["water_source"]
    fs.status = FieldResolutionStatus.CONFLICT
    fs.candidate_claims = [
        claim(f"Source {i}", 0.5 + i / 10, f"https://s{i}.org", "long passage " * 200)
        for i in range(4)
    ]
    bundle = mgr.build(ContextTask(ContextPurpose.RESOLVE_CONFLICT, ["water_source"], 400),
                       state)
    claims = [i for i in bundle.items if i.kind == "claim"]
    assert len(claims) == 4
    assert claims[0].score == pytest.approx(0.8)
    assert len(bundle.sources) == 4

def test_conflict_needs_two_candidates(state, mgr):
    state.fields["water_source"].candidate_claims = [claim("Lake", 0.9, "https://a", "p")]
    bundle = mgr.build(ContextTask(ContextPurpose.RESOLVE_CONFLICT, ["water_source"]), state)
    assert bundle.is_empty

def test_plan_context_has_status_but_no_evidence(state, mgr):
    bundle = mgr.build(ContextTask(ContextPurpose.PLAN, ["water_type"]), state,
                       [chunk("util", UTILITY_TEXT)])
    rendered = bundle.render()
    assert "water_type (enum: Surface | Groundwater | Reclaimed | Desalination)" in rendered
    assert "## Evidence" not in rendered
    assert "- country = Canada" in rendered

def test_evidence_ref_round_trip_and_fingerprint(state, mgr):
    chunks = [{"chunk_id": "util", "url": "https://toronto.ca/water", "text": UTILITY_TEXT,
               "page_number": 4}]
    a = mgr.build(extract(["utility_name"]), state, chunks)
    b = mgr.build(extract(["utility_name"]), state, chunks)
    assert a.fingerprint == b.fingerprint
    ref = a.evidence_ref("S1")
    assert ref.url == "https://toronto.ca/water"
    assert ref.page_number == 4
    assert ref.passage == UTILITY_TEXT
    with pytest.raises(KeyError):
        a.evidence_ref("S9")
