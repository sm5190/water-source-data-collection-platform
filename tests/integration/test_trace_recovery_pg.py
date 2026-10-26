"""End to end with real processes on Postgres: API submit (trace=true, sampling ratio 0) ->
worker process under the supervisor -> hard kill mid-run -> the supervisor records the exit
and the interrupted run with its last trace ids -> the restarted worker reclaims the lease,
resumes from the Postgres checkpoint, and its recovery trace links back.

Spans are exported by the processes to JSON-lines files (no collector needed). The killed
worker's unflushed spans are expected to be missing: the supervisor's events and the
run_traces rows (written when spans start) are what connect the attempts."""

import json
import sys
import threading
import time
from pathlib import Path
import psutil
import pytest
from conftest import URL
from fastapi.testclient import TestClient
from sqlalchemy import text
from water_source_research.api.main import create_app
from water_source_research.observability.config import ObservabilitySettings
from water_source_research.observability.supervisor import Supervisor
from water_source_research.observability.tracing import TraceStore
from water_source_research.observability.worker_events import EventStore
from water_source_research.research_queue.queue import JobStatus
from queue_helpers import make_queue

WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "trace_worker.py"

@pytest.fixture
def drop_checkpoints(pg_engine):
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                          "checkpoints, checkpoint_migrations"))

def spans_in(directory: Path) -> list[dict]:
    out = []
    for p in directory.glob("*.jsonl*"):
        out += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]
    return out

def wait_for(predicate, timeout=60.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("condition not reached")

def test_crash_reclaim_resume_trace_chain(pg_engine, drop_checkpoints, tmp_path, monkeypatch,
                                         _tracer_provider):
    logs = tmp_path / "logs"
    traces = tmp_path / "traces"
    for key, value in {
        "DATABASE_URL": URL, "OBSERVABILITY__OTEL_ENABLED": "true",
        "OBSERVABILITY__TRACE_EXPORTER": "file", "OBSERVABILITY__SAMPLE_RATIO": "0",
        "OBSERVABILITY__LOG_DIR": str(logs),
        "OBSERVABILITY__TRACE_FILE": str(traces / "worker.jsonl"),
        "RESEARCH_QUEUE__LEASE_SECONDS": "3", "RESEARCH_QUEUE__HEARTBEAT_SECONDS": "1",
        "RESEARCH_QUEUE__POLL_INTERVAL_SECONDS": "0.2",
        "RESEARCH_QUEUE__BACKOFF_BASE_SECONDS": "0", "RESEARCH_QUEUE__KEEP_CHECKPOINTS": "true",
    }.items():
        monkeypatch.setenv(key, value)
    # Each worker process writes its own trace file (pid in the name would also do).
    q = make_queue(pg_engine, lease_seconds=3, heartbeat_seconds=1)
    client = TestClient(create_app(queue=q))
    run_id = client.post("/research/city", json={"city_name": "Oslo", "country": "Norway",
                                                "trace": True}).json()["run_id"]
    selected_unrelated = client.post("/research/city", json={"city_name": "Lima"}).json()
    q.cancel(selected_unrelated["run_id"])  # only the selected run runs

    modes = ["slow", "normal"]
    starts: list[str] = []

    def command(instance_id, supervisor_id):
        mode = modes[min(len(starts), 1)]
        starts.append(mode)
        monkeypatch.setenv("OBSERVABILITY__TRACE_FILE", str(traces / f"{mode}.jsonl"))
        return [sys.executable, str(WORKER), "--log-dir", str(logs), "--instance-id",
                instance_id, "--supervisor-id", supervisor_id, "--mode", mode]

    sup = Supervisor(command=command, log_dir=logs, engine=pg_engine, poll_seconds=0.1,
                     collect_windows_events=False,
                     settings=ObservabilitySettings(sample_seconds=0.2, restart_base_seconds=0.1,
                                                    event_sync_seconds=0.5))
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(sup.run()), daemon=True)
    thread.start()

    # Wait until the first worker is inside the run (heartbeat lists it), then kill it hard.
    def running():
        for f in (logs / "heartbeats").glob("*.json"):
            hb = json.loads(f.read_text())
            live = (hb.get("active_runs") or {}).get(run_id)
            if live and live.get("trace_id") and live.get("stage") == "discover":
                return hb
        return None
    hb = wait_for(running)
    # LangGraph writes checkpoints asynchronously; give the finished steps a moment to be
    # durable so the resume point is deterministic (a kill before that is covered by
    # test_unfinished_checkpoint_without_pending_steps_is_run_not_recorded).
    time.sleep(1.5)
    psutil.Process(hb["pid"]).kill()  # no handlers, no flush
    thread.join(120)
    assert result == [0]

    job = q.get(run_id)
    assert (job.status, job.resumed) == (JobStatus.SUCCEEDED, True)

    # 1. the supervisor recorded the death and the interrupted run with its trace ids
    events = EventStore(pg_engine).events()
    exit_event = next(e for e in events if e["kind"] == "WORKER_EXITED_UNEXPECTEDLY")
    assert exit_event["facts"]["affected_runs"] == [run_id]
    interrupted = next(e for e in events if e["kind"] == "JOB_INTERRUPTED")
    rows = TraceStore(pg_engine).list(run_id)
    first = next(r for r in rows if r["kind"] == "RUN")
    assert (interrupted["trace_id"], interrupted["span_id"]) == (first["trace_id"],
                                                                 first["span_id"])
    # 2. recovery: lease reclaimed, checkpoint resumed, linked trace
    run_events = {e["kind"]: e for e in EventStore(pg_engine).events(run_id=run_id)}
    assert run_events["JOB_RECLAIMED"]["facts"]["previous_owner"] == hb["instance_id"]
    assert run_events["CHECKPOINT_RECOVERY_ATTEMPTED"]["facts"]["next_nodes"] == ["acquire"]
    assert run_events["JOB_RESUMED"]["facts"]["outcome"] == "SUCCEEDED"
    recovery_row = next(r for r in rows if r["kind"] == "RECOVERY")
    assert recovery_row["sampled"] is True  # selected run: traced although ratio is 0
    # 3. the trace chain, as exported
    recovered = spans_in(traces)
    rescue_run = next(s for s in recovered if s["name"] == "research.run"
                      and s["trace_id"] == recovery_row["trace_id"])
    assert any(lk["span_id"] == first["span_id"] for lk in rescue_run["links"])
    recovery_span = next(s for s in recovered if s["name"] == "research.recovery")
    assert recovery_span["attributes"]["research.recovery.crash_event_id"] == \
        interrupted["event_id"]
    assert [lk["span_id"] for lk in recovery_span["links"]] == [first["span_id"]]
    # 4. the submission trace is the first attempt's trace (API -> worker continuity)
    submit = (job.trace_context or {}).get("traceparent", "")
    assert submit.split("-")[1] == first["trace_id"]
    # 5. the killed worker's research.run span never ended: it is not in the export
    assert not any(s["name"] == "research.run" and s["span_id"] == first["span_id"]
                   for s in recovered)
