"""The provider_usage ledger: one row per logical model call, written in the background;
Unknown stays NULL; a cache hit is a row with no requests; nothing ever fails a call."""

import pytest
from llm_helpers import Server, chat, chat_json, error, make_llm, vt_settings
from procedure_helpers import make_engine
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from water_source_research.db.observability_models import ProviderUsageRecord as U
from water_source_research.llm.base import LLMError
from water_source_research.llm.router import _LockedCache
from water_source_research.observability import usage
from water_source_research.observability.llm_telemetry import llm_call_context

class Answer(BaseModel):
    value: str

@pytest.fixture
def ledger(tmp_path):
    engine = make_engine(tmp_path, "ledger.db")
    rec = usage.UsageRecorder(engine, flush_seconds=0.05)
    usage.set_recorder(rec)

    def rows():
        rec.flush()
        with Session(engine) as s:
            return list(s.scalars(select(U).order_by(U.id)))
    yield rows
    usage.set_recorder(None)
    rec.close()
    engine.dispose()

def test_one_row_per_call_with_context_tokens_and_unknown_cost(ledger):
    llm = make_llm(Server(chat_json({"value": "x"}, prompt_tokens=50, completion_tokens=9)),
                   task="extract", prompt_version="extract-3")
    with llm_call_context(run_id="r1", field_key="water_source"):
        llm.structured("s", "u", Answer)
    (row,) = ledger()
    assert (row.run_id, row.field_key, row.category, row.kind, row.provider, row.model,
            row.task, row.prompt_version) == ("r1", "water_source", "research", "LLM", "vt",
                                              "gpt-oss-120b", "extract", "extract-3")
    assert (row.requests, row.retries, row.cache_hit, row.error) == (1, 0, False, None)
    assert (row.input_tokens, row.output_tokens) == (50, 9)
    assert row.cost_usd is None and row.cost_basis is None  # VT has no price: Unknown
    assert row.latency_ms is not None

def test_cache_hit_is_a_row_without_requests(ledger):
    cache = _LockedCache(3600, 10)
    server = Server(chat_json({"value": "x"}))
    make_llm(server, cache=cache).structured("s", "u", Answer)
    make_llm(server, cache=cache).structured("s", "u", Answer)
    first, second = ledger()
    assert (first.cache_hit, first.requests) == (False, 1)
    assert (second.cache_hit, second.requests) == (True, 0)
    assert second.input_tokens is None

def test_missing_usage_stays_null(ledger):
    import httpx2
    reply = httpx2.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": '{"value": "x"}'}}]})
    make_llm(Server(reply)).structured("s", "u", Answer)
    (row,) = ledger()
    assert row.input_tokens is None and row.output_tokens is None and row.requests == 1

def test_retries_attempt_errors_and_failures(ledger):
    make_llm(Server(error(429, headers={"Retry-After": "0"}), chat_json({"value": "x"}))
             ).structured("s", "u", Answer)
    with pytest.raises(LLMError):
        make_llm(Server(error(500)), vt_settings(max_retries=1)).structured("s", "u", Answer)
    with pytest.raises(LLMError):
        make_llm(Server(chat("not json")), vt_settings(structured_max_attempts=0)
                 ).structured("s", "u", Answer)
    ok, failed, schema = ledger()
    assert (ok.retries, ok.attempt_errors, ok.error) == (1, {"rate_limited": 1}, None)
    assert (failed.retries, failed.attempt_errors, failed.error) == (
        1, {"server_error": 2}, "server_error")
    assert schema.error == "schema" and schema.requests == 1

def test_judge_calls_are_evaluation_spend(ledger):
    llm = make_llm(Server(chat_json({"value": "x"})), task="eval_judge")
    with llm_call_context(run_id="r1", category="evaluation"):
        llm.structured("s", "u", Answer)
    (row,) = ledger()
    assert (row.run_id, row.category) == ("r1", "evaluation")

def test_price_table_gives_a_cost(ledger, monkeypatch):
    from water_source_research.observability import llm_telemetry
    monkeypatch.setattr(llm_telemetry, "_prices", {"vt:gpt-oss-120b": {
        "input_per_1m": 1.0, "output_per_1m": 2.0}})
    make_llm(Server(chat_json({"value": "x"}, prompt_tokens=1000, completion_tokens=500))
             ).structured("s", "u", Answer)
    (row,) = ledger()
    assert row.cost_usd == pytest.approx(0.002) and row.cost_basis == "vt:gpt-oss-120b"

def test_full_queue_drops_and_counts_without_blocking(tmp_path):
    rec = usage.UsageRecorder(make_engine(tmp_path, "q.db"), max_queue=2, flush_seconds=60)
    rec._stop.set()  # writer idle: the queue fills
    for _ in range(5):
        rec.record({"category": "research", "kind": "LLM", "provider": "vt"})
    assert rec.dropped >= 3

def test_database_outage_never_fails_a_call(tmp_path):
    down = create_engine(f"sqlite:///{tmp_path / 'no' / 'such' / 'x.db'}")
    rec = usage.UsageRecorder(down, flush_seconds=0.05)
    usage.set_recorder(rec)
    try:
        assert make_llm(Server(chat_json({"value": "x"}))).structured("s", "u", Answer).value \
            == "x"
        rec.flush()
        assert rec.stats()["usage_rows_dropped"] >= 1 and rec.failed_batches >= 1
    finally:
        usage.set_recorder(None)
        rec.close()

def test_no_recorder_means_no_rows_and_no_error():
    usage.set_recorder(None)
    make_llm(Server(chat_json({"value": "x"}))).structured("s", "u", Answer)
