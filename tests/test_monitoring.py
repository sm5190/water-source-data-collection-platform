"""Monitoring numbers computed from stored records: the time split (active vs queue, review
and other waiting), pipeline counts, worker status, crash/recovery incidents (a restarted
worker is not a resumed run), usage with Unknown kept Unknown, and the ledger-vs-events
precedence (never both)."""

from datetime import UTC, datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from procedure_helpers import make_engine
from queue_helpers import make_queue
from sqlalchemy.orm import Session
from water_source_research.api.main import create_app
from water_source_research.core.config import Settings
from water_source_research.db.evidence_models import (
    DiscoveryResultRecord, EvidenceChunkRecord, FieldClaimRecord, SourceDocumentRecord,
)
from water_source_research.db.observability_models import (
    ProviderUsageRecord, RunTraceRecord, WorkerEventRecord, WorkerInstanceRecord,
)
from water_source_research.db.research_models import ResearchJobRecord, ResearchRunStepRecord
from water_source_research.observability import monitoring as m

T0 = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=3)
SETTINGS = Settings(_env_file=None, observability={"sample_seconds": 30,
                                                   "langfuse_ui_url": ""})

def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)

def job(run_id, status, created=0, finished=None, attempts=1, city="Lima", **kw):
    return ResearchJobRecord(run_id=run_id, city_name=city, country="Peru",
                             city_key=f"{city}|{run_id}", status=status, priority=0,
                             attempts=attempts, max_attempts=3, created_at=at(created),
                             started_at=at(created + 1), updated_at=at(created + 1),
                             finished_at=at(finished) if finished is not None else None, **kw)

def step(run_id, attempt, seq, node, end_min, seconds):
    return ResearchRunStepRecord(run_id=run_id, attempt=attempt, seq=seq, node=node,
                                 duration_ms=seconds * 1000, summary={},
                                 finished_at=at(end_min))

def usage_row(run_id, **kw):
    base = {"run_id": run_id, "category": "research", "kind": "LLM", "provider": "vt",
            "model": "gpt-oss-120b", "task": "extract", "requests": 1, "retries": 0,
            "cache_hit": False, "latency_ms": 100, "created_at": at(30)}
    return ProviderUsageRecord(**{**base, **kw})

def event(kind, minutes, **kw):
    return WorkerEventRecord(event_id=f"{kind}-{minutes}-{kw.get('run_id', '')}", kind=kind,
                             occurred_at=at(minutes), source="supervisor", facts={}, **kw)

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path, "mon.db")
    with Session(eng) as s, s.begin():
        # r1: queued 2 min, attempt 1 pauses on review at minute 10, resumed at minute 70.
        s.add(job("r1", "SUCCEEDED", created=0, finished=80, attempts=2, completion="FINALIZED"))
        s.add_all([step("r1", 1, 1, "plan", 2.5, 30), step("r1", 1, 2, "extract", 9, 300),
                   step("r1", 1, 3, "human_review", 10, 60),
                   step("r1", 2, 1, "human_review", 71, 60), step("r1", 2, 2, "finalize", 80,
                                                                   480)])
        # r2: crashed during extract, reclaimed and resumed after a 5-minute gap.
        s.add(job("r2", "SUCCEEDED", created=100, finished=130, attempts=2, city="Quito"))
        s.add_all([step("r2", 1, 1, "plan", 102, 60), step("r2", 2, 1, "extract", 115, 120),
                   step("r2", 2, 2, "finalize", 130, 600)])
        # r3: awaiting review since minute 160; historical (events-only) usage.
        s.add(job("r3", "AWAITING_REVIEW", created=150, city="Oslo", result={"events": [
            {"kind": "NODE", "node": "extract", "field_key": "water_source",
             "attrs": {"llm_requests": 2, "cache_hits": 0, "prompt_tokens": 900,
                       "completion_tokens": 100, "model": "vt:gpt-oss-120b", "claims": 2,
                       "bundle_status": "READY", "discovery_round": 1}}]}))
        s.add(step("r3", 1, 1, "human_review", 160, 30))
        s.add(job("r4", "FAILED", created=170, finished=175, last_error="boom"))
        doc = SourceDocumentRecord(run_id="r1", requested_url="https://a", canonical_url="a",
                                   status="OK", round=1)
        s.add_all([doc, SourceDocumentRecord(run_id="r1", requested_url="https://b",
                                             canonical_url="b", status="FAILED", round=2)])
        s.flush()
        s.add_all([EvidenceChunkRecord(run_id="r1", chunk_id=f"c{i}", document_id=doc.id,
                                       url="https://a", ordinal=i, text="t",
                                       char_count=1)
                   for i in range(3)])
        s.add_all([DiscoveryResultRecord(run_id="r1", round=1, call_index=0, result_index=i,
                                         provider="searxng", strategy="web_search",
                                         url=f"https://x/{i}", retained=i < 2,
                                         retrieved_at=at(1)) for i in range(5)])
        s.add_all([FieldClaimRecord(run_id="r1", field_key="water_source", value="x",
                                    value_status="KNOWN", value_origin="REPORTED",
                                    confidence=0.9, validation_outcome=o)
                   for o in ("VALID", "VALID", "INVALID", "WARN")])
        s.add_all([usage_row("r1", field_key="water_source", input_tokens=100,
                             output_tokens=10),
                   usage_row("r1", field_key="utility_name", input_tokens=None,
                             output_tokens=None, latency_ms=300),
                   usage_row("r1", cache_hit=True, requests=0, latency_ms=0),
                   usage_row("r1", retries=2, attempt_errors={"rate_limited": 2},
                             error="rate_limited", input_tokens=None, output_tokens=None),
                   usage_row("r2", input_tokens=50, output_tokens=5, cost_usd=0.01,
                             cost_basis="vt:gpt-oss-120b"),
                   usage_row("r1", category="evaluation", task="eval_judge")])
        s.add(RunTraceRecord(run_id="r1", kind="RUN", attempt=1, trace_id="ab" * 16,
                             span_id="cd" * 8, sampled=True, created_at=at(1)))
        s.add_all([
            WorkerInstanceRecord(instance_id="w-alive", last_heartbeat_at=NOW
                                 - timedelta(seconds=20), updated_at=NOW,
                                 last_sample={"cpu_percent": 12.5, "rss_mb": 300,
                                              "private_mb": 280, "active_runs": {}}),
            WorkerInstanceRecord(instance_id="w-stale", last_heartbeat_at=NOW
                                 - timedelta(minutes=10), updated_at=NOW - timedelta(minutes=1)),
            WorkerInstanceRecord(instance_id="w-dead", last_heartbeat_at=at(103),
                                 stopped_at=at(104), exit_code=1,
                                 termination_class="UNKNOWN_TERMINATION",
                                 updated_at=NOW - timedelta(minutes=2))])
        crash = event("WORKER_EXITED_UNEXPECTEDLY", 104, instance_id="w-dead",
                      inferred_cause="UNKNOWN_TERMINATION", cause_confidence="UNKNOWN")
        crash.facts = {"exit_code": 1, "affected_runs": ["r2", "r9"]}
        s.add_all([crash,
                   event("JOB_INTERRUPTED", 104, run_id="r2", instance_id="w-dead"),
                   event("WORKER_RESTARTED", 104.5, instance_id="w-new"),
                   event("JOB_RECLAIMED", 109, run_id="r2", instance_id="w-new"),
                   event("CHECKPOINT_RECOVERY_ATTEMPTED", 109, run_id="r2"),
                   event("JOB_RESUMED", 130, run_id="r2")])
    with Session(eng) as s, s.begin():
        restarted = s.query(WorkerEventRecord).filter_by(kind="WORKER_RESTARTED").one()
        restarted.facts = {"previous_instance": "w-dead"}
    yield eng
    eng.dispose()

def test_time_split_separates_active_queue_review_and_other_waiting(engine):
    r1 = next(r for r in m.runs(engine, now=NOW) if r["run_id"] == "r1")
    assert r1["wall_s"] == 80 * 60
    assert r1["active_s"] == 30 + 300 + 60 + 60 + 480
    assert r1["queue_wait_s"] == 2 * 60  # first step started at minute 2
    assert r1["review_wait_s"] == 60 * 60  # paused at minute 10, next attempt from minute 70
    assert r1["other_wait_s"] == 0
    r2 = next(r for r in m.runs(engine, now=NOW) if r["run_id"] == "r2")
    assert r2["other_wait_s"] == 11 * 60  # crash gap: attempt 1 ended 102, attempt 2 at 113
    assert r2["review_wait_s"] == 0

def test_awaiting_review_counts_review_wait_until_now(engine):
    r3 = next(r for r in m.runs(engine, now=NOW) if r["run_id"] == "r3")
    assert r3["review_wait_s"] == (180 - 160) * 60
    r4 = next(r for r in m.runs(engine, now=NOW) if r["run_id"] == "r4")
    assert r4["active_s"] is None  # no step records: Unknown, not 0

def test_run_detail_pipeline_stages_traces_and_usage(engine):
    d = m.run_detail(engine, "r1", SETTINGS, now=NOW)
    p = d["pipeline"]
    assert (p["search_results"], p["search_results_kept"], p["chunks"]) == (5, 2, 3)
    assert p["documents"] == {"OK": 1, "FAILED": 1}
    assert p["documents_by_round"] == {1: 1, 2: 1}
    assert p["claims_by_validation"] == {"VALID": 2, "INVALID": 1, "WARN": 1}
    assert p["validation_failures"] == 1
    assert d["retries"] == 1
    assert d["stage_totals"]["human_review"] == {"runs": 2, "seconds": 120.0}
    assert [x["done"] for x in d["progress"]][:2] == [True, False]  # discover not recorded
    llm = d["llm"]
    assert llm["source"] == "ledger"
    assert (llm["calls"], llm["invocations"], llm["cache_hits"]) == (5, 4, 1)
    assert llm["input_tokens"] == 100  # only calls whose usage is known
    assert llm["unknown_token_calls"] == 3  # never counted as 0
    assert llm["cost_status"] == "UNKNOWN" and llm["cost_usd_known"] is None
    assert llm["attempt_errors"] == {"rate_limited": 2} and llm["failed_calls"] == 1
    assert d["traces"][0]["jaeger_url"].endswith("ab" * 16)
    assert d["traces"][0]["langfuse_url"] is None  # Langfuse not configured
    assert any("rate_limited" == e["error"] for e in d["errors"])

def test_historical_runs_use_events_and_are_labelled(engine):
    d = m.run_detail(engine, "r3", SETTINGS, now=NOW)
    assert d["llm"]["source"] == "events (extraction only)"
    assert (d["llm"]["requests"], d["llm"]["input_tokens"]) == (2, 900)
    assert d["llm"]["cost_status"] == "UNKNOWN"
    assert d["pipeline"]["extraction"]["water_source"]["claims"] == 2

def test_usage_groups_and_unknown_costs(engine):
    u = m.usage(engine, hours=None, group_by="city", now=NOW)
    by = {g["group"]: g for g in u["groups"]}
    assert by["Quito"]["cost_usd_known"] == 0.01 and by["Quito"]["cost_status"] == "KNOWN"
    assert by["Lima"]["cost_status"] == "UNKNOWN"
    assert u["total"]["cost_status"] == "PARTIAL"
    cats = {g["group"]: g for g in m.usage(engine, hours=None, group_by="category",
                                           now=NOW)["groups"]}
    assert set(cats) == {"research", "evaluation"}
    assert m.usage(engine, hours=1, now=NOW)["total"]["calls"] == 0  # outside the window

def test_worker_status(engine):
    ws = {w["instance_id"]: w for w in m.workers(engine, SETTINGS, now=NOW)}
    assert ws["w-alive"]["status"] == "ALIVE" and ws["w-alive"]["cpu_percent"] == 12.5
    assert ws["w-stale"]["status"] == "STALE"
    assert ws["w-dead"]["status"] == "EXITED"

def test_recovery_incident_separates_restart_from_resume(engine):
    (incident,) = m.recovery_timeline(engine, hours=None, now=NOW)
    assert incident["worker_restart"] == "RESTARTED"
    outcomes = {r["run_id"]: r["research_outcome"] for r in incident["runs"]}
    assert outcomes == {"r2": "RESUMED", "r9": "PENDING"}  # restart != resumed research
    assert incident["crash"]["track"] == "worker"
    assert all(e["track"] == "research" for e in incident["runs"][0]["steps"])

def test_overview(engine):
    o = m.overview(engine, hours=24, settings=SETTINGS, now=NOW)
    assert o["runs"]["awaiting_review"] == 1 and o["runs"]["failed_total"] == 1
    assert o["workers"]["ALIVE"] == 1 and o["workers"]["EXITED"] == 1
    assert any(e["error"] == "boom" for e in o["recent_errors"])
    assert any("UNKNOWN_TERMINATION" in e["error"] for e in o["recent_errors"])

def test_monitoring_api(engine):
    client = TestClient(create_app(queue=make_queue(engine)))
    assert client.get("/monitoring/overview").status_code == 200
    assert client.get("/monitoring/runs/r1").json()["pipeline"]["chunks"] == 3
    assert client.get("/monitoring/runs/nope").status_code == 404
    assert client.get("/monitoring/usage", params={"group_by": "bad"}).status_code == 422
    assert client.get("/monitoring/usage", params={"hours": 0, "group_by": "run"}
                      ).json()["total"]["calls"] == 6
    assert len(client.get("/monitoring/workers").json()) == 3
    assert client.get("/monitoring/recovery", params={"hours": 100000}).json()
    backends = {b["backend"]: b for b in client.get("/monitoring/backends").json()}
    assert set(backends) == {"otel_collector", "jaeger", "langfuse"}
