"""Persistent extraction cache (llm/response_store.py, router tier 2): reused across processes
only for the same question on the same evidence, re-grounded against the reusing run's own
passages, invalidated by any input that can change the answer, and never hiding model usage."""

import asyncio
import pytest
from llm_helpers import Server, chat_json, make_llm, vt_settings
from procedure_helpers import make_engine
from sqlalchemy import select
from sqlalchemy.orm import Session
from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
from water_source_research.core.versions import EXTRACTION_PROMPT_VERSION
from water_source_research.db.observability_models import LLMResponseCacheRecord
from water_source_research.db.observability_models import ProviderUsageRecord as U
from water_source_research.llm.extraction import (
    SYSTEM_PROMPT, StructuredExtractionGateway, cache_context, evidence_passages, response_model,
    user_prompt,
)
from water_source_research.llm.response_store import ResponseStore
from water_source_research.llm.router import _LockedCache
from water_source_research.observability import usage
from water_source_research.observability.llm_telemetry import llm_call_context
from water_source_research.observability.monitoring import usage as usage_report
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)

FIELDS = load_field_definitions()
TEXT = ("Toronto Water treats drinking water drawn from Lake Ontario, a surface water source. "
        "The utility serves 3.6 million people.")

def chunk(retrieved_at="2026-10-01T00:00:00Z", text=TEXT):
    return EvidenceChunk(chunk_id="util", url="https://toronto.ca/water", title="Toronto Water",
                         source_type="UTILITY", text=text, retrieved_at=retrieved_at)

def bundle(field="water_type", chunks=None, city="Toronto"):
    state = initialize_city_state(city, "Canada")
    return ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field]), state,
                                  chunks or [chunk()])

ANSWER = {"claims": [{"value": "Surface", "value_status": "KNOWN", "value_origin": "REPORTED",
                      "confidence": 0.9, "citations": [
                          {"source_id": "S1", "quote": "a surface water source"}]}]}

@pytest.fixture
def store(tmp_path):
    engine = make_engine(tmp_path, "cache.db")
    yield ResponseStore(engine)
    engine.dispose()

def gateway(server, store, **llm):
    """A fresh process: its own in-process cache, the shared persistent store."""
    return StructuredExtractionGateway(make_llm(
        server, vt_settings(**llm), task="extract", prompt_version=EXTRACTION_PROMPT_VERSION,
        cache=_LockedCache(3600, 100), store=store), FIELDS)

def extract(gw, b, field="water_type"):
    return asyncio.run(gw.extract(b, run_id="run-1", field_key=field))

def test_identical_extraction_is_reused_by_another_process_with_its_own_provenance(store):
    first = Server(chat_json(ANSWER))
    [a] = extract(gateway(first, store), bundle())
    second = Server(chat_json({"claims": []}))  # would answer differently if it were asked
    [b] = extract(gateway(second, store), bundle(chunks=[chunk("2026-10-29T00:00:00Z")]))
    assert len(first.requests) == 1 and second.requests == []
    assert (a.value, b.value) == ("Surface", "Surface")
    # re-grounded against the reusing run's own passage: its retrieval time, not the first's
    assert b.evidence[0].retrieved_at == "2026-10-29T00:00:00Z"
    assert b.evidence[0].passage == "a surface water source"

def test_changed_evidence_is_a_miss_and_a_quote_no_longer_shown_is_rejected(store):
    extract(gateway(Server(chat_json(ANSWER)), store), bundle())
    changed = chunk(text="Toronto Water treats drinking water drawn from Lake Ontario.")
    server = Server(chat_json(ANSWER))  # the old answer quotes text that is no longer there
    assert extract(gateway(server, store), bundle(chunks=[changed])) == []
    assert len(server.requests) == 1  # asked again: different evidence, different key

def test_write_only_stores_but_never_reads(store):
    extract(gateway(Server(chat_json(ANSWER)), store, persistent_cache="write_only"), bundle())
    server = Server(chat_json(ANSWER))
    extract(gateway(server, store, persistent_cache="write_only"), bundle())
    assert len(server.requests) == 1
    with Session(store.engine) as s:
        assert len(s.scalars(select(LLMResponseCacheRecord)).all()) == 1

def test_off_neither_reads_nor_writes(store):
    extract(gateway(Server(chat_json(ANSWER)), store, persistent_cache="off"), bundle())
    with Session(store.engine) as s:
        assert s.scalars(select(LLMResponseCacheRecord)).all() == []

def test_entry_keeps_components_and_original_usage(store):
    with llm_call_context(run_id="run-1"):
        extract(gateway(Server(chat_json(ANSWER, prompt_tokens=900, completion_tokens=120)),
                        store), bundle())
    with Session(store.engine) as s:
        [row] = s.scalars(select(LLMResponseCacheRecord)).all()
    assert (row.input_tokens, row.output_tokens, row.requests) == (900, 120, 1)
    assert row.source_run_id == "run-1" and row.task == "extract"
    assert set(row.context) == {"field_definition", "scope", "evidence", "retrieval_policy"}
    assert row.context["scope"] == "Toronto|Canada"
    assert row.response == ANSWER

# --- invalidation: one component at a time ----------------------------------------------------

def key(llm_settings=None, *, field="water_type", b=None, prompt_version=None, task="extract",
        context_update=None, fields=FIELDS):
    llm = make_llm(Server(chat_json(ANSWER)), vt_settings(**(llm_settings or {})), task=task,
                   prompt_version=prompt_version or EXTRACTION_PROMPT_VERSION)
    b = b or bundle(field)
    definition = fields[field]
    passages = evidence_passages(b, field)
    context = {**cache_context(b, definition, passages), **(context_update or {})}
    return llm.cache_key(SYSTEM_PROMPT, user_prompt(b, definition, passages),
                         response_model(definition, sorted(passages)), context)

def test_every_input_that_can_change_the_answer_changes_the_key():
    base = key()
    assert key() == base  # deterministic
    changed = {
        "model settings: max output tokens": key({"max_output_tokens": 2000}),
        "model settings: structured mode": key({"vt_structured_mode": "json_object"}),
        "model settings: repair budget": key({"structured_max_attempts": 1}),
        "prompt version": key(prompt_version="extract-999"),
        "task": key(task="other"),
        "evidence": key(b=bundle(chunks=[chunk(text=TEXT + " Wells are a backup.")])),
        "scope": key(b=bundle(city="Torontoville")),
        "field (schema and prompt)": key(field="water_source"),
        "retrieval policy": key(context_update={"retrieval_policy": "999"}),
    }
    edited = dict(FIELDS)
    edited["water_type"] = FIELDS["water_type"].model_copy(
        update={"definition": "Something else entirely."})
    changed["field definition"] = key(fields=edited)
    assert all(k != base for k in changed.values()), [n for n, k in changed.items() if k == base]
    assert len(set(changed.values())) == len(changed)

def test_model_and_endpoint_change_the_key():
    from water_source_research.core.config import Settings
    from water_source_research.llm.config import LLMSettings
    from water_source_research.llm.router import build_structured_llm

    def k(**s):
        settings = Settings(_env_file=None, llm_provider="vt", vt_llm_api_key="sk-vt-test",
                            openai_api_key="", llm=LLMSettings(),
                            **{"vt_llm_base_url": "http://vt.test/api/v1",
                               "vt_llm_model": "gpt-oss-120b", **s})
        llm = build_structured_llm(settings, task="extract", prompt_version="p",
                                   cache=_LockedCache(1, 1))
        return llm.cache_key("s", "u", response_model(FIELDS["water_type"], ["S1"]), {})
    assert len({k(), k(vt_llm_model="other-model"),
                k(vt_llm_base_url="http://vt2.test/api/v1")}) == 3

# --- usage honesty -----------------------------------------------------------------------------

@pytest.fixture
def ledger(tmp_path):
    engine = make_engine(tmp_path, "ledger.db")
    rec = usage.UsageRecorder(engine, flush_seconds=0.05)
    usage.set_recorder(rec)
    yield engine, rec
    usage.set_recorder(None)
    rec.close()
    engine.dispose()

def test_a_hit_spends_nothing_and_names_the_entry_whose_tokens_it_avoided(ledger):
    engine, rec = ledger
    store = ResponseStore(engine)
    with llm_call_context(run_id="run-1", field_key="water_type"):
        extract(gateway(Server(chat_json(ANSWER, prompt_tokens=900, completion_tokens=120)),
                        store), bundle())
    with llm_call_context(run_id="run-2", field_key="water_type"):
        extract(gateway(Server(chat_json(ANSWER)), store), bundle())
    rec.flush()
    with Session(engine) as s:
        fresh, hit = s.scalars(select(U).order_by(U.id)).all()
        [entry] = s.scalars(select(LLMResponseCacheRecord)).all()
    assert (fresh.cache_hit, fresh.cache_layer, fresh.requests) == (False, None, 1)
    assert (hit.cache_hit, hit.cache_layer, hit.requests) == (True, "persistent", 0)
    assert hit.input_tokens is None and hit.output_tokens is None
    assert hit.cache_source == entry.cache_key
    report = usage_report(engine, hours=None)["total"]
    assert (report["input_tokens"], report["output_tokens"]) == (900, 120)  # spent: once
    assert (report["persistent_cache_hits"], report["tokens_avoided"]) == (1, 1020)

def test_in_process_hit_is_labelled_memory(ledger):
    engine, rec = ledger
    gw = gateway(Server(chat_json(ANSWER)), None)
    extract(gw, bundle())
    extract(gw, bundle())
    rec.flush()
    with Session(engine) as s:
        rows = s.scalars(select(U).order_by(U.id)).all()
    assert [(r.cache_hit, r.cache_layer) for r in rows] == [(False, None), (True, "memory")]
    total = usage_report(engine, hours=None)["total"]
    assert (total["memory_cache_hits"], total["avoided_unknown_hits"]) == (1, 1)
