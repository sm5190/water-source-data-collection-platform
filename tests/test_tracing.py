"""Distributed tracing (mocked: in-memory exporter, scripted model, mocked web). Live
ingestion into Jaeger is checked separately (scripts/observability_check.py).

Covers: the span hierarchy of a research run, API submit -> worker continuity, retries and
failures, rounds and re-extraction, crash -> recovery links, selected-run sampling with ratio
0 (and that external headers can't force sampling), a dead exporter, single provider, and
no sensitive attribute values."""

import asyncio
import logging
import time
import pytest
from acquisition_helpers import make_acquisition
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from llm_helpers import error
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from procedure_helpers import make_engine
from queue_helpers import Clock, Crash, Probe, make_graph, make_queue, make_worker
from slice_helpers import TARGETS, ModelScript, make_discovery, make_gateway, make_site
from water_source_research.agents.graph import build_graph
from water_source_research.api.main import create_app
from water_source_research.core.config import Settings
from water_source_research.observability import otel
from water_source_research.observability.otel import (
    JsonLinesSpanExporter, TraceContextFilter, configure_telemetry, research_sampler,
)
from water_source_research.observability.tracing import TraceStore, safe_url
from water_source_research.observability.worker_events import EventStore, WorkerEvent
from water_source_research.research_queue.queue import JobStatus

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path, "trace.db")
    yield eng
    eng.dispose()

@pytest.fixture
def ratio(_tracer_provider):
    """Swap the sampler of the test provider (restored afterwards)."""
    original = _tracer_provider.sampler

    def use(r: float):
        _tracer_provider.sampler = research_sampler(r)
        _tracer_provider._tracers.clear()  # tracers keep the sampler they were created with
    yield use
    _tracer_provider.sampler = original
    _tracer_provider._tracers.clear()

def run(coro):
    return asyncio.run(coro)

def hexid(n: int, width: int) -> str:
    return f"{n:0{width}x}"

def tree(spans):
    finished = spans.get_finished_spans()
    by_id = {s.context.span_id: s for s in finished}
    def parent(s):
        return by_id[s.parent.span_id].name if s.parent and s.parent.span_id in by_id else None
    return finished, parent

def slice_graph(script=None, **llm):
    gateway, server = make_gateway(script or ModelScript(), structured_max_attempts=1, **llm)
    graph = build_graph(gateway=gateway, discovery=make_discovery(),
                        acquisition=make_acquisition(make_site()), max_discovery_rounds=2,
                        checkpointer=InMemorySaver())
    return graph, server

# --- hierarchy --------------------------------------------------------------------------

def test_research_run_trace_hierarchy(engine, spans, ratio):
    ratio(1.0)
    q = make_queue(engine)
    run_id = q.submit("Toronto", "Canada", field_keys=TARGETS).job.run_id
    graph, _ = slice_graph()
    run(make_worker(q, graph, "w1").drain())
    assert q.get(run_id).status is JobStatus.SUCCEEDED
    finished, parent = tree(spans)
    names = {s.name for s in finished}
    assert {"research_queue.submit", "research.run", "graph.discover", "discovery.discover",
            "discovery.rank_results", "graph.acquire", "acquisition.fetch_document",
            "acquisition.parse_document", "graph.extract", "extraction.field",
            "context.get_context", "llm.extract", "llm.call", "llm.request", "graph.validate",
            "graph.resolve", "graph.finalize"} <= names
    run_span = next(s for s in finished if s.name == "research.run")
    submit = next(s for s in finished if s.name == "research_queue.submit")
    # one trace from submission to the last LLM request
    assert run_span.context.trace_id == submit.context.trace_id
    assert parent(run_span) == "research_queue.submit"
    assert all(s.context.trace_id == submit.context.trace_id for s in finished
               if s.name.startswith(("graph.", "llm.", "extraction.", "acquisition.")))
    assert parent(next(s for s in finished if s.name == "graph.discover")) == "research.run"
    assert parent(next(s for s in finished if s.name == "acquisition.parse_document")) \
        == "acquisition.fetch_document"
    assert parent(next(s for s in finished if s.name == "extraction.field")) == "graph.extract"
    assert parent(next(s for s in finished if s.name == "llm.call")) == "llm.extract"
    assert parent(next(s for s in finished if s.name == "llm.request")) == "llm.call"
    # run <-> trace link stored by run_id
    rows = TraceStore(engine).list(run_id)
    assert [r["kind"] for r in rows] == ["RUN"]
    assert rows[0]["trace_id"] == hexid(run_span.context.trace_id, 32) and rows[0]["sampled"]

def test_rounds_and_reextraction_are_distinguishable(engine, spans, ratio):
    ratio(1.0)
    q = make_queue(engine)
    q.submit("Toronto", "Canada", field_keys=TARGETS)
    graph, _ = slice_graph()
    run(make_worker(q, graph).drain())
    fields = [s for s in spans.get_finished_spans() if s.name == "extraction.field"]
    assert fields
    for s in fields:
        assert {"research.field_key", "research.discovery_round", "extraction.reextraction",
                "extraction.attempt"} <= set(s.attributes)
    rounds = {s.attributes["research.discovery_round"] for s in fields}
    assert min(rounds) >= 1

def test_retries_and_failures_are_visible(engine, spans, ratio):
    ratio(1.0)
    script = ModelScript()
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return error(429, headers={"Retry-After": "0"})
        return script(request)

    q = make_queue(engine)
    q.submit("Toronto", "Canada", field_keys=["utility_name"])
    graph, _ = slice_graph(flaky, backoff_base_seconds=0, max_retries=2)
    run(make_worker(q, graph).drain())
    requests = [s for s in spans.get_finished_spans() if s.name == "llm.request"]
    failed = [s for s in requests if s.attributes.get("llm.error") == "rate_limited"]
    assert failed and failed[0].attributes["http.status_code"] == 429
    assert failed[0].status.status_code.name == "ERROR"
    retried_call = next(s for s in spans.get_finished_spans() if s.name == "llm.call"
                        and s.context.span_id == failed[0].parent.span_id)
    siblings = [s for s in requests if s.parent.span_id == retried_call.context.span_id]
    assert [s.attributes["llm.attempt"] for s in siblings] == [1, 2]

# --- crash and recovery -----------------------------------------------------------------

def test_recovery_trace_links_to_the_interrupted_attempt(engine, spans, ratio, monkeypatch):
    ratio(1.0)
    probe = Probe()
    probe.install(monkeypatch)
    probe.fail_validate_once = Crash
    clock = Clock()
    q = make_queue(engine, clock, lease_seconds=30)
    run_id = q.submit("Oslo", "Norway").job.run_id
    checkpoints = InMemorySaver()

    async def dying():
        w = make_worker(q, make_graph(checkpoints), "dying")
        await w._fill_slots()
        await asyncio.gather(*w.in_flight, return_exceptions=True)
        return w

    run(dying())
    first = TraceStore(engine).list(run_id)[0]  # written at span start: survives the crash
    # what the supervisor records when the process dies (from the heartbeat file)
    EventStore(engine).insert([WorkerEvent(kind="JOB_INTERRUPTED", source="supervisor",
                                           run_id=run_id, trace_id=first["trace_id"],
                                           span_id=first["span_id"], event_id="crash-1")])
    clock.advance(31)
    run(make_worker(q, make_graph(checkpoints), "rescuer").drain())
    assert q.get(run_id).status is JobStatus.SUCCEEDED
    finished, parent = tree(spans)
    runs = [s for s in finished if s.name == "research.run"]
    rescue = runs[-1]
    assert rescue.context.trace_id != runs[0].context.trace_id  # a new trace...
    linked = {(hexid(lk.context.trace_id, 32), lk.attributes.get("link.kind"))
              for lk in rescue.links}
    assert (first["trace_id"], "previous_attempt") in linked  # ...linked to the crashed one
    recovery = next(s for s in finished if s.name == "research.recovery")
    assert parent(recovery) == "research.run"
    assert recovery.attributes["research.recovery.crash_event_id"] == "crash-1"
    assert recovery.attributes["research.recovery.next_nodes"] == ("validate",)
    assert [hexid(lk.context.span_id, 16) for lk in recovery.links] == [first["span_id"]]
    assert parent(next(s for s in finished if s.name == "graph.validate"
                        and s.context.trace_id == rescue.context.trace_id)) \
        == "research.recovery"
    kinds = [r["kind"] for r in TraceStore(engine).list(run_id)]
    assert kinds == ["RUN", "RECOVERY"]

# --- sampling ---------------------------------------------------------------------------

def test_ratio_zero_traces_only_selected_runs(engine, spans, ratio, monkeypatch):
    ratio(0.0)
    Probe().install(monkeypatch)
    q = make_queue(engine)
    selected = q.submit("Lima", "Peru", trace=True).job.run_id
    ordinary = q.submit("Quito", "Ecuador").job.run_id
    run(make_worker(q, make_graph()).drain())
    finished = spans.get_finished_spans()
    by_run = {}
    for s in finished:
        rid = s.attributes.get("research.run_id")
        if rid:
            by_run.setdefault(rid, set()).add(s.context.trace_id)
    assert ordinary not in by_run  # unselected: no spans at all
    assert len(by_run[selected]) == 1  # selected: one complete trace
    trace_id = next(iter(by_run[selected]))
    names = {s.name for s in finished if s.context.trace_id == trace_id}
    assert {"research_queue.submit", "research.run", "graph.plan", "graph.validate",
            "graph.finalize"} <= names
    rows = {r["kind"]: r for r in TraceStore(engine).list(selected)}
    assert rows["RUN"]["sampled"] is True
    assert TraceStore(engine).list(ordinary)[0]["sampled"] is False

def test_external_headers_cannot_force_sampling(engine, spans, ratio, monkeypatch):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    ratio(0.0)
    Probe().install(monkeypatch)
    q = make_queue(engine)
    app = create_app(queue=q)
    FastAPIInstrumentor.instrument_app(app)
    try:
        forged = "ab" * 16
        r = TestClient(app).post("/research/city", json={"city_name": "Lima"},
                                 headers={"traceparent": f"00-{forged}-{'cd' * 8}-01"})
        assert r.status_code == 202
        run(make_worker(q, make_graph()).drain())
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    assert [s for s in spans.get_finished_spans()] == []  # nothing, not even the HTTP span
    assert int(forged, 16) not in {s.context.trace_id for s in spans.get_finished_spans()}

def test_api_trace_flag_selects_the_run_and_links_the_request(engine, spans, ratio,
                                                             monkeypatch):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    ratio(0.0)
    Probe().install(monkeypatch)
    q = make_queue(engine)
    app = create_app(queue=q)
    FastAPIInstrumentor.instrument_app(app)
    try:
        client = TestClient(app)
        run_id = client.post("/research/city", json={"city_name": "Lima", "trace": True}
                             ).json()["run_id"]
        run(make_worker(q, make_graph()).drain())
        traces = client.get(f"/research/runs/{run_id}/traces").json()
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    assert traces["trace_requested"] is True
    run_row = next(t for t in traces["traces"] if t["kind"] == "RUN")
    assert run_row["sampled"] and run_row["url"].endswith(run_row["trace_id"])
    submit = next(s for s in spans.get_finished_spans() if s.name == "research_queue.submit")
    assert hexid(submit.context.trace_id, 32) == run_row["trace_id"]

# --- configuration and export safety ------------------------------------------------------

def test_configure_telemetry_never_replaces_an_installed_provider(_tracer_provider):
    settings = Settings(_env_file=None, observability={"otel_enabled": True})
    assert configure_telemetry("worker", settings=settings) is None
    assert trace.get_tracer_provider() is _tracer_provider
    disabled = Settings(_env_file=None)
    assert configure_telemetry("worker", settings=disabled) is None
    assert not otel.telemetry_enabled()

def test_dead_collector_does_not_block_or_fail(tmp_path):
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    provider = TracerProvider(sampler=research_sampler(1.0))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint="http://127.0.0.1:1", insecure=True, timeout=1),
        max_queue_size=64, max_export_batch_size=16, export_timeout_millis=1000))
    tracer = provider.get_tracer("t")
    started = time.monotonic()
    for i in range(500):  # more than the queue holds: extra spans are dropped, not buffered
        with tracer.start_as_current_span(f"s{i}"):
            pass
    assert time.monotonic() - started < 2.0  # producing spans never waits on the exporter
    t0 = time.monotonic()
    provider.shutdown()
    assert time.monotonic() - t0 < 15.0  # bounded flush

def test_file_exporter_writes_bounded_json_lines(tmp_path):
    import json
    provider = TracerProvider(sampler=research_sampler(1.0))
    exporter = JsonLinesSpanExporter(tmp_path / "traces.jsonl", max_mb=0.001, backups=1)
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("t")
    for i in range(50):
        with tracer.start_as_current_span("parent", attributes={"i": i}),                 tracer.start_as_current_span("child"):
            pass
    provider.shutdown()
    files = sorted(tmp_path.glob("traces.jsonl*"))
    assert len(files) <= 2  # rotated, bounded
    rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    assert {"name", "trace_id", "span_id", "parent_span_id", "attributes", "links"} <= set(rec)

def test_log_lines_carry_trace_ids(_tracer_provider):
    record = logging.LogRecord("x", logging.INFO, "f", 1, "m", None, None)
    TraceContextFilter().filter(record)
    assert record.trace_id == "-"
    with trace.get_tracer("t").start_as_current_span("s") as span:
        TraceContextFilter().filter(record)
    assert record.trace_id == hexid(span.get_span_context().trace_id, 32)

def test_no_sensitive_values_in_span_attributes(engine, spans, ratio):
    ratio(1.0)
    assert safe_url("https://u:p@host.example/a/b?token=SECRET#frag") == \
        "https://host.example/a/b"
    q = make_queue(engine)
    q.submit("Toronto", "Canada", field_keys=TARGETS)
    graph, _ = slice_graph()
    run(make_worker(q, graph).drain())
    from slice_helpers import SOURCE_SENTENCE, UTIL_SENTENCE
    for s in spans.get_finished_spans():
        for k, v in (s.attributes or {}).items():
            text = str(v)
            assert "?" not in text or "=" not in text, (s.name, k)
            assert UTIL_SENTENCE[:40] not in text and SOURCE_SENTENCE[:40] not in text, \
                (s.name, k)
            assert "Bearer" not in text and "sk-" not in text, (s.name, k)
