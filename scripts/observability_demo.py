"""Show what the observability pipeline records for model calls.

    python scripts/observability_demo.py --mock
        No credentials, no network: a scripted research run (mocked web, scripted model)
        through the real queue/worker/graph, with Langfuse's span processor attached to the
        same TracerProvider but exporting to memory. Prints the trace tree (what Jaeger would
        show) and the observations Langfuse would receive. MOCKED.

    python scripts/observability_demo.py --live [--jaeger http://localhost:16686]
        One REAL model call on the configured provider (VT by default; never paid OpenAI unless
        LLM_PROVIDER=openai) inside a selected trace, exported to the collector (needs
        `docker compose --profile observability up -d`) and, when OBSERVABILITY__LANGFUSE_ENABLED
        and keys are set, to Langfuse. Then asks Jaeger's and Langfuse's APIs for that trace and
        says which backends were verified. No research data is written.
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def tree(spans: list, root_name: str = "research.run") -> list[str]:
    by_parent: dict = {}
    for s in spans:
        by_parent.setdefault(s.parent.span_id if s.parent else None, []).append(s)
    roots = [s for s in spans if s.name == root_name]
    lines: list[str] = []

    def walk(span, depth):
        kids = sorted(by_parent.get(span.context.span_id, []), key=lambda k: k.start_time)
        ms = (span.end_time - span.start_time) / 1e6
        extra = ""
        a = span.attributes or {}
        if span.name == "llm.call":
            extra = (f"  [{a.get('langfuse.observation.type')}] "
                     f"in={a.get('gen_ai.usage.input_tokens', 'UNKNOWN')} "
                     f"out={a.get('gen_ai.usage.output_tokens', 'UNKNOWN')} "
                     f"cache_hit={a.get('llm.cache_hit')}")
        if span.name == "extraction.field":
            extra = (f"  field={a.get('research.field_key')} "
                     f"round={a.get('research.discovery_round')} "
                     f"reextraction={a.get('extraction.reextraction')}")
        lines.append(f"{'  ' * depth}{span.name} ({ms:.1f} ms){extra}")
        if depth < 6:
            for k in kids:
                walk(k, depth + 1)
    for r in roots[:1]:
        walk(r, 0)
    return lines

def mock() -> int:
    sys.path.insert(0, str(ROOT / "tests"))
    os.environ.setdefault("OBSERVABILITY__HASH_KEY", "demo-hash-key")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from water_source_research.core.config import Settings
    from water_source_research.observability.langfuse import configure_langfuse
    from water_source_research.observability.otel import research_sampler
    backend, langfuse_out = InMemorySpanExporter(), InMemorySpanExporter()
    provider = TracerProvider(sampler=research_sampler(1.0))
    provider.add_span_processor(SimpleSpanProcessor(backend))
    trace.set_tracer_provider(provider)
    settings = Settings(_env_file=None, langfuse_public_key="pk-demo",
                        langfuse_secret_key="sk-demo", langfuse_host="http://127.0.0.1:9",
                        observability={"langfuse_enabled": True})
    client = configure_langfuse(provider, settings, span_exporter=langfuse_out)

    from acquisition_helpers import make_acquisition
    from langgraph.checkpoint.memory import InMemorySaver
    from procedure_helpers import make_engine
    from queue_helpers import make_queue, make_worker
    from slice_helpers import TARGETS, ModelScript, make_discovery, make_gateway, make_site
    from water_source_research.agents.graph import build_graph
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(Path(tmp), "demo.db")
        q = make_queue(engine)
        run_id = q.submit("Toronto", "Canada", field_keys=TARGETS).job.run_id
        gateway, server = make_gateway(ModelScript(), structured_max_attempts=1)
        graph = build_graph(gateway=gateway, discovery=make_discovery(),
                            acquisition=make_acquisition(make_site()),
                            max_discovery_rounds=2, checkpointer=InMemorySaver())
        asyncio.run(make_worker(q, graph, "demo-worker").drain())
        engine.dispose()
    client.flush()
    spans = backend.get_finished_spans()
    print(f"MOCKED demonstration (scripted model, mocked web) - run {run_id}\n")
    print("== Trace (what the tracing backend receives) ==")
    print("\n".join(tree(spans)))
    lf = langfuse_out.get_finished_spans()
    gens = [s for s in lf if s.attributes.get("langfuse.observation.type") == "generation"]
    print(f"\n== Langfuse export: {len(lf)} spans, {len(gens)} generations "
          f"({len(server.requests)} model requests made) ==")
    for g in gens[:6]:
        a = g.attributes
        print(json.dumps({k: a.get(k) for k in (
            "research.run_id", "research.field_key", "research.discovery_round",
            "extraction.reextraction", "llm.provider", "langfuse.observation.model.name",
            "llm.prompt_version", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
            "llm.latency_ms", "llm.attempts", "llm.retries", "llm.cache_hit",
            "llm.usage_known", "llm.cost_known", "llm.prompt_hmac")}, indent=1))
    leaked = [k for s in lf for k in (s.attributes or {}) if k.endswith((".input", ".output"))]
    print(f"\nprompt/response text exported: {'YES' if leaked else 'no'} "
          f"(OBSERVABILITY__CAPTURE_LLM_CONTENT=off)")
    return 0

def live(jaeger: str) -> int:
    from pydantic import BaseModel
    from water_source_research.core.config import get_settings
    from water_source_research.llm.base import LLMError
    from water_source_research.llm.router import build_structured_llm
    from water_source_research.observability.llm_telemetry import llm_call_context
    from water_source_research.observability.otel import (
        TRACE_REQUESTED, configure_telemetry, get_tracer, shutdown_telemetry,
    )
    settings = get_settings()
    if not settings.observability.otel_enabled:
        print("set OBSERVABILITY__OTEL_ENABLED=true (and start the collector) for --live")
        return 2
    if configure_telemetry("demo") is None:
        print("telemetry could not be configured")
        return 2

    class Ok(BaseModel):
        ok: bool

    run_id = f"demo-{uuid.uuid4().hex[:8]}"
    outcome = "ok"
    with get_tracer().start_as_current_span(
            "research.run", attributes={TRACE_REQUESTED: True, "research.run_id": run_id}) as root:
        trace_id = f"{root.get_span_context().trace_id:032x}"
        try:
            llm = build_structured_llm(settings, task="observability_demo", prompt_version="1")
            with llm_call_context(run_id=run_id, field_key="demo", task_kind="demo"):
                llm.structured("Reply with JSON only.", 'Return {"ok": true}.', Ok)
        except LLMError as exc:
            outcome = f"model call failed: {exc}"
    shutdown_telemetry()
    print(f"LIVE: provider={settings.llm_provider} run_id={run_id} trace_id={trace_id} "
          f"call={outcome}")

    found = None
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"{jaeger}/api/traces/{trace_id}", timeout=5) as r:
                data = json.load(r).get("data") or []
            if data:
                found = data[0]
                break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    if found:
        call = next((s for s in found["spans"] if s["operationName"] == "llm.call"), None)
        tags = {t["key"]: t["value"] for t in (call or {}).get("tags", [])}
        print(f"Jaeger: VERIFIED ({len(found['spans'])} spans); llm.call tokens "
              f"in={tags.get('gen_ai.usage.input_tokens', 'UNKNOWN')} "
              f"out={tags.get('gen_ai.usage.output_tokens', 'UNKNOWN')} "
              f"latency_ms={tags.get('llm.latency_ms')}")
    else:
        print("Jaeger: NOT VERIFIED (trace not found; is the collector running?)")

    obs = settings.observability
    if not (obs.langfuse_enabled and settings.langfuse_public_key):
        print("Langfuse: NOT CONFIGURED (no keys) - not verified")
        return 0 if found else 1
    host = (settings.langfuse_host or "https://cloud.langfuse.com").rstrip("/")
    auth = base64.b64encode(f"{settings.langfuse_public_key}:"
                            f"{settings.langfuse_secret_key}".encode()).decode()
    seen = None
    for _ in range(30):  # ingestion is asynchronous
        try:
            req = urllib.request.Request(f"{host}/api/public/traces/{trace_id}",
                                         headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                seen = json.load(r)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    if seen:
        gens = [o for o in seen.get("observations", []) if o.get("type") == "GENERATION"]
        print(f"Langfuse: VERIFIED ({len(seen.get('observations', []))} observations, "
              f"{len(gens)} generation(s))")
        return 0 if found else 1
    print("Langfuse: NOT VERIFIED (trace not found via the public API)")
    return 1

def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--mock", action="store_true")
    g.add_argument("--live", action="store_true")
    p.add_argument("--jaeger", default="http://localhost:16686")
    a = p.parse_args()
    return mock() if a.mock else live(a.jaeger)

if __name__ == "__main__":
    sys.exit(main())
