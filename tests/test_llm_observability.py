"""LLM observability (mocked: scripted VT HTTP, in-memory exporters; Langfuse's own span
processor runs with a fake exporter, so nothing leaves the machine). Live Langfuse ingestion
is checked separately (scripts/observability_demo.py --live, only with keys).

Covers: one generation per fresh model call with run/field ids and usage; UNKNOWN usage;
retries, failures and schema failures; cache hits that are not invocations; the
hierarchy-preserving Langfuse filter on a full research run; no duplicate observations; one
tracer provider; privacy by default (no prompt/evidence text, keyed hashes only), opt-in
content, masking; Langfuse being unreachable; the hash key kept out of git."""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path
import pytest
from acquisition_helpers import make_acquisition
from langgraph.checkpoint.memory import InMemorySaver
from llm_helpers import Server, chat, chat_json, error, make_llm, vt_settings
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from procedure_helpers import make_engine
from pydantic import BaseModel
from queue_helpers import make_queue, make_worker
from slice_helpers import (
    SOURCE_SENTENCE, TARGETS, UTIL_SENTENCE, ModelScript, make_discovery, make_gateway,
    make_site,
)
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import Settings
from water_source_research.llm.base import LLMError
from water_source_research.llm.router import _LockedCache, llm_call_log
from water_source_research.observability import langfuse as lf
from water_source_research.observability import llm_telemetry
from water_source_research.observability.llm_telemetry import llm_call_context

class Answer(BaseModel):
    value: str

def calls(spans, name="llm.call"):
    return [s for s in spans.get_finished_spans() if s.name == name]

@pytest.fixture
def langfuse_export(_tracer_provider):
    """Langfuse attached to the (session) test provider with an in-memory exporter; detached
    afterwards. A unique public key per test avoids the SDK's per-key client cache."""
    exporter = InMemorySpanExporter()
    settings = Settings(_env_file=None, langfuse_public_key=f"pk-test-{uuid.uuid4().hex}",
                        langfuse_secret_key="sk-test", langfuse_host="http://127.0.0.1:9",
                        observability={"langfuse_enabled": True,
                                        "langfuse_flush_interval_seconds": 0.1})
    before = _tracer_provider._active_span_processor._span_processors
    client = lf.configure_langfuse(_tracer_provider, settings, span_exporter=exporter)
    assert client is not None
    added = [p for p in _tracer_provider._active_span_processor._span_processors
             if p not in before]

    def exported():
        client.flush()
        return exporter.get_finished_spans()

    yield exported
    lf._client = None
    for p in added:
        p.shutdown()
    _tracer_provider._active_span_processor._span_processors = before

# --- the llm.call observation ---------------------------------------------------------------

def test_generation_carries_ids_usage_and_versions(spans):
    llm = make_llm(Server(chat_json({"value": "x"}, prompt_tokens=120, completion_tokens=30)),
                   task="extract", prompt_version="extract-3")
    with llm_call_context(run_id="run-1", field_key="water_source", discovery_round=2,
                          reextraction=True, attempt=2, bundle_id="b-1"):
        llm.structured("system", "user prompt", Answer)
    (span,) = calls(spans)
    a = span.attributes
    assert a["langfuse.observation.type"] == "generation" and a["llm.invocation"] is True
    assert (a["research.run_id"], a["research.field_key"], a["research.discovery_round"],
            a["extraction.reextraction"], a["extraction.attempt"], a["context.bundle_id"]) \
        == ("run-1", "water_source", 2, True, 2, "b-1")
    assert a["session.id"] == "run-1"
    assert (a["llm.provider"], a["llm.model"], a["llm.task"], a["llm.prompt_version"]) == \
        ("vt", "gpt-oss-120b", "extract", "extract-3")
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (120, 30)
    assert json.loads(a["langfuse.observation.usage_details"]) == {"input": 120, "output": 30}
    assert a["llm.usage_known"] is True and a["llm.cost_known"] is False  # VT: no price
    assert "llm.cost_usd" not in a
    assert a["llm.latency_ms"] >= 0 and a["llm.attempts"] == 1

def test_missing_usage_is_unknown_not_zero(spans):
    import httpx2
    reply = httpx2.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": '{"value": "x"}'}}]})
    log: list[dict] = []
    llm_call_log.set(log)
    make_llm(Server(reply)).structured("s", "u", Answer)
    (span,) = calls(spans)
    assert span.attributes["llm.usage_known"] is False
    assert "gen_ai.usage.input_tokens" not in span.attributes
    assert "langfuse.observation.usage_details" not in span.attributes
    assert log[-1]["prompt_tokens"] is None and log[-1]["completion_tokens"] is None
    from water_source_research.agents.nodes import llm_usage
    assert llm_usage(log)["usage_unknown_calls"] == 1

def test_retries_are_counted_and_attempts_are_child_spans(spans):
    llm = make_llm(Server(error(429, headers={"Retry-After": "0"}), chat_json({"value": "x"})))
    llm.structured("s", "u", Answer)
    (span,) = calls(spans)
    assert span.attributes["llm.retries"] == 1 and span.attributes["llm.attempts"] == 2
    requests = calls(spans, "llm.request")
    assert [r.attributes["llm.attempt"] for r in requests] == [1, 2]
    assert all(r.attributes["langfuse.observation.type"] == "span" for r in requests)
    assert all(r.parent.span_id == span.context.span_id for r in requests)

def test_failed_calls_are_visible(spans):
    llm = make_llm(Server(error(500)), vt_settings(max_retries=1))
    with pytest.raises(LLMError):
        llm.structured("s", "u", Answer)
    (span,) = calls(spans)
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["llm.error"] == "server_error"
    assert span.attributes["langfuse.observation.level"] == "ERROR"
    assert [r.attributes["llm.error"] for r in calls(spans, "llm.request")] == \
        ["server_error", "server_error"]

def test_structured_output_failures_are_recorded(spans):
    llm = make_llm(Server(chat("not json")), vt_settings(structured_max_attempts=1))
    with pytest.raises(LLMError):
        llm.structured("s", "u", Answer)
    (span,) = calls(spans)
    assert span.attributes["llm.error"] == "schema"
    assert span.attributes["llm.validation_failures"] == 2  # the first reply and the repair
    assert span.attributes["llm.repairs"] == 1

def test_cache_hits_are_not_invocations(spans):
    cache = _LockedCache(3600, 10)
    server = Server(chat_json({"value": "x"}))
    log: list[dict] = []
    llm_call_log.set(log)
    make_llm(server, cache=cache).structured("s", "u", Answer)
    make_llm(server, cache=cache).structured("s", "u", Answer)
    first, second = calls(spans)
    assert first.attributes["langfuse.observation.type"] == "generation"
    assert second.attributes["langfuse.observation.type"] == "span"
    assert second.attributes["llm.cache_hit"] is True
    assert second.attributes["llm.invocation"] is False
    assert "gen_ai.usage.input_tokens" not in second.attributes
    assert len(server.requests) == 1 and [c["requests"] for c in log] == [1, 0]

def test_cached_prompt_tokens_from_the_provider(spans):
    import httpx2
    body = json.loads(chat_json({"value": "x"}).content)
    body["usage"]["prompt_tokens_details"] = {"cached_tokens": 4}
    make_llm(Server(httpx2.Response(200, json=body))).structured("s", "u", Answer)
    (span,) = calls(spans)
    assert span.attributes["llm.cached_tokens"] == 4
    assert json.loads(span.attributes["langfuse.observation.usage_details"])[
        "input_cached_tokens"] == 4

def test_cost_only_from_a_configured_price(monkeypatch):
    monkeypatch.setattr(llm_telemetry, "_prices",
                        {"openai:gpt-x": {"input_per_1m": 1.0, "cached_input_per_1m": 0.5,
                                          "output_per_1m": 2.0}})
    assert llm_telemetry.estimate_cost("openai", "gpt-x", 1000, 500, 200) == pytest.approx(
        (800 * 1.0 + 200 * 0.5 + 500 * 2.0) / 1e6)
    assert llm_telemetry.estimate_cost("vt", "gpt-oss-120b", 1000, 500, None) is None
    assert llm_telemetry.estimate_cost("openai", "gpt-x", None, 500, None) is None

# --- privacy --------------------------------------------------------------------------------

def test_no_prompt_or_response_text_by_default(spans):
    secret_prompt = "Toronto draws its drinking water from Lake Ontario. key sk-abcdefghijk"
    make_llm(Server(chat_json({"value": "Lake Ontario"}))).structured(
        "system", secret_prompt, Answer)
    (span,) = calls(spans)
    values = " ".join(str(v) for v in span.attributes.values())
    assert "Lake Ontario" not in values and "sk-abcdefghijk" not in values
    assert "langfuse.observation.input" not in span.attributes
    assert span.attributes["llm.prompt_hmac"].startswith("hmac-sha256:")
    assert span.attributes["llm.response_hmac"].startswith("hmac-sha256:")
    assert span.attributes["llm.prompt_hmac"] != llm_telemetry.digest("other")

def test_content_capture_is_opt_in_redacted_and_truncated(spans, monkeypatch):
    monkeypatch.setattr(llm_telemetry, "content_mode", lambda: "truncated")
    prompt = ("write to a.person@example.org at https://x.example/p?token=abc "
              "with key sk-abcdefghijkl " + "x" * 5000)
    make_llm(Server(chat_json({"value": "ok"}))).structured("s", prompt, Answer)
    (span,) = calls(spans)
    captured = json.loads(span.attributes["langfuse.observation.input"])
    assert len(captured) <= llm_telemetry.MAX_CONTENT_CHARS
    assert "a.person@example.org" not in captured and "token=abc" not in captured
    assert "sk-abcdefghijkl" not in captured and "https://x.example/p" in captured

def test_langfuse_mask_drops_unlisted_attributes_and_redacts_secrets():
    from types import SimpleNamespace
    mask = lf.mask_spans(content_opt_in=False)
    span = SimpleNamespace(name="llm.call", attributes={
        "research.run_id": "r1", "llm.model": "m", "db.statement": "select * from x",
        "acquisition.url": "https://h/p", "llm.note": "Bearer abcdefghijklmnop",
        "langfuse.observation.input": '"the prompt"', "llm.prompt_hmac": "hmac-sha256:ab"})
    result = mask(params=SimpleNamespace(spans={"id1": span}))
    patch = result.span_patches["id1"]
    assert set(patch.delete_attributes) == {"db.statement", "acquisition.url",
                                            "langfuse.observation.input"}
    assert patch.set_attributes == {"llm.note": "[REDACTED]"}
    opted_in = lf.mask_spans(content_opt_in=True)(params=SimpleNamespace(spans={"id1": span}))
    assert "langfuse.observation.input" not in opted_in.span_patches["id1"].delete_attributes

def test_hash_key_file_is_created_privately_and_ignored_by_git(tmp_path, monkeypatch):
    monkeypatch.delenv("OBSERVABILITY__HASH_KEY", raising=False)
    monkeypatch.setattr(llm_telemetry, "_key", None)
    key = llm_telemetry.hash_key(str(tmp_path))
    assert len(key) == 32 and (tmp_path / ".telemetry-key").exists()
    llm_telemetry._key = None
    assert llm_telemetry.hash_key(str(tmp_path)) == key  # stable across processes
    llm_telemetry._key = None
    root = Path(__file__).resolve().parents[1]
    ignored = subprocess.run(["git", "check-ignore", "-q", "logs/.telemetry-key"], cwd=root,
                             check=False).returncode
    assert ignored == 0  # never committed

# --- Langfuse on the application's provider ----------------------------------------------

def test_langfuse_uses_the_existing_provider(langfuse_export, _tracer_provider):
    assert trace.get_tracer_provider() is _tracer_provider
    assert lf._client._resources.tracer_provider is _tracer_provider  # SDK internals (5.0.4)
    names = [type(p).__name__ for p in _tracer_provider._active_span_processor._span_processors]
    assert names.count("LangfuseSpanProcessor") == 1

def test_langfuse_gets_the_llm_chain_with_its_hierarchy_and_no_duplicates(
        tmp_path, spans, langfuse_export):
    engine = make_engine(tmp_path, "lf.db")
    q = make_queue(engine)
    q.submit("Toronto", "Canada", field_keys=TARGETS)
    gateway, server = make_gateway(ModelScript(), structured_max_attempts=1)
    graph = build_graph(gateway=gateway, discovery=make_discovery(),
                        acquisition=make_acquisition(make_site()), max_discovery_rounds=2,
                        checkpointer=InMemorySaver())
    asyncio.run(make_worker(q, graph).drain())
    exported = langfuse_export()
    names = {s.name for s in exported}
    assert names <= lf.LANGFUSE_SPANS
    assert {"research.run", "graph.extract", "extraction.field", "llm.extract", "llm.call",
            "llm.request"} <= names
    assert not names & {"graph.discover", "acquisition.fetch_document", "context.retrieval"}
    by_id = {s.context.span_id for s in exported}
    for s in exported:  # hierarchy kept: every parent is exported, or the span is a root
        if s.name != "research.run":
            assert s.parent is not None and s.parent.span_id in by_id, s.name
    generations = [s for s in exported
                   if s.attributes.get("langfuse.observation.type") == "generation"]
    fresh = [s for s in calls(spans) if s.attributes.get("llm.invocation")]
    assert len(generations) == len(fresh) == len(server.requests)  # one per real request
    assert all(g.name == "llm.call" for g in generations)
    for s in exported:  # nothing from documents or prompts in the Langfuse copy
        text = " ".join(str(v) for v in (s.attributes or {}).values())
        assert UTIL_SENTENCE[:40] not in text and SOURCE_SENTENCE[:40] not in text
        assert "langfuse.observation.input" not in (s.attributes or {})
    engine.dispose()

def test_unreachable_langfuse_never_breaks_a_call(_tracer_provider, spans):
    import time
    settings = Settings(_env_file=None, langfuse_public_key=f"pk-dead-{uuid.uuid4().hex}",
                        langfuse_secret_key="sk-dead", langfuse_host="http://127.0.0.1:1",
                        observability={"langfuse_enabled": True,
                                       "langfuse_flush_interval_seconds": 0.1,
                                       "export_timeout_seconds": 1})
    before = _tracer_provider._active_span_processor._span_processors
    client = lf.configure_langfuse(_tracer_provider, settings)
    try:
        result = make_llm(Server(chat_json({"value": "x"}))).structured("s", "u", Answer)
        assert result.value == "x"
        started = time.monotonic()
        client.flush()  # export fails (connection refused): logged, never raised
        assert time.monotonic() - started < 30
    finally:
        lf._client = None
        for p in _tracer_provider._active_span_processor._span_processors:
            if p not in before:
                p.shutdown()
        _tracer_provider._active_span_processor._span_processors = before

def test_langfuse_is_off_without_keys_or_flag():
    assert lf.configure_langfuse(None, Settings(_env_file=None)) is None
    on_without_keys = Settings(_env_file=None, observability={"langfuse_enabled": True})
    assert lf.configure_langfuse(None, on_without_keys) is None
