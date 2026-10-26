"""Monitoring end to end with real processes on Postgres.

1. The supervisor starts a worker process (mocked web, scripted model through the real LLM
   router, usage ledger on).
2. A small research run is submitted through the API with trace=true.
3. The worker is hard-killed mid-run (no handlers, no flush).
4. The supervisor records the exit and restarts the worker; the new worker reclaims the
   lease and resumes from the Postgres checkpoint; the run succeeds.
5. All along, span export points at a dead port and Langfuse at a dead host: research
   continues, and the failures are visible as export counters and alerts.
6. The /monitoring endpoints and the Streamlit Monitoring views show these actual events.
"""

import json
import sys
import threading
import time
from pathlib import Path
import psutil
import pytest
from conftest import URL
from fastapi.testclient import TestClient
from queue_helpers import make_queue
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from water_source_research.api.main import create_app
from water_source_research.core.config import get_settings
from water_source_research.db.observability_models import WorkerInstanceRecord
from water_source_research.observability import health
from water_source_research.observability.config import ObservabilitySettings
from water_source_research.observability.supervisor import Supervisor
from water_source_research.research_queue.queue import JobStatus
from water_source_research.ui.client import ResearchApiClient

WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "slice_worker.py"
TARGETS = ["utility_name", "water_source", "total_city_water_withdrawals_m3_day"]

@pytest.fixture
def drop_checkpoints(pg_engine):
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                          "checkpoints, checkpoint_migrations"))

@pytest.fixture
def env(monkeypatch, tmp_path):
    values = {
        "DATABASE_URL": URL, "OBSERVABILITY__LOG_DIR": str(tmp_path / "logs"),
        "OBSERVABILITY__OTEL_ENABLED": "true", "OBSERVABILITY__TRACE_EXPORTER": "otlp",
        "OBSERVABILITY__USAGE_LEDGER": "true",  # off in the test session (conftest)
        "OBSERVABILITY__OTLP_ENDPOINT": "http://127.0.0.1:1",  # nothing listens here
        "OBSERVABILITY__COLLECTOR_HEALTH_URL": "http://127.0.0.1:1/",
        "OBSERVABILITY__JAEGER_API_URL": "http://127.0.0.1:1",
        "OBSERVABILITY__EXPORT_TIMEOUT_SECONDS": "1", "OBSERVABILITY__SAMPLE_RATIO": "0",
        "OBSERVABILITY__LANGFUSE_ENABLED": "true", "LANGFUSE_PUBLIC_KEY": "pk-dead",
        "LANGFUSE_SECRET_KEY": "sk-dead", "LANGFUSE_HOST": "http://127.0.0.1:1",
        "OBSERVABILITY__HEALTH_PROBE_TIMEOUT_SECONDS": "0.5",
        "RESEARCH_QUEUE__LEASE_SECONDS": "3", "RESEARCH_QUEUE__HEARTBEAT_SECONDS": "1",
        "RESEARCH_QUEUE__POLL_INTERVAL_SECONDS": "0.2",
        "RESEARCH_QUEUE__BACKOFF_BASE_SECONDS": "0"}
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    health.clear_cache()
    yield tmp_path / "logs"
    get_settings.cache_clear()
    health.clear_cache()

def wait_for(predicate, timeout=90.0, step=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("condition not reached")

def test_crash_recovery_usage_alerts_and_dashboard(pg_engine, drop_checkpoints, env,
                                                   _tracer_provider):
    logs = env
    q = make_queue(pg_engine, lease_seconds=3, heartbeat_seconds=1)
    app = create_app(queue=q)
    api = TestClient(app)
    run_id = api.post("/research/city", json={"city_name": "Toronto", "country": "Canada",
                                              "field_keys": TARGETS, "trace": True}
                      ).json()["run_id"]

    modes, starts = ["slow", "serve"], []

    def command(instance_id, supervisor_id):
        starts.append(modes[min(len(starts), 1)])
        return [sys.executable, str(WORKER), "--log-dir", str(logs), "--instance-id",
                instance_id, "--supervisor-id", supervisor_id, "--mode", starts[-1]]

    sup = Supervisor(command=command, log_dir=logs, engine=pg_engine, poll_seconds=0.1,
                     collect_windows_events=False,
                     settings=ObservabilitySettings(sample_seconds=0.2, restart_base_seconds=0.2,
                                                    event_sync_seconds=0.5,
                                                    stop_grace_seconds=10))
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    try:
        # 3. kill the first worker once it is inside the run (acquire hangs)
        def in_run():
            for f in (logs / "heartbeats").glob("*.json"):
                hb = json.loads(f.read_text())
                if ((hb.get("active_runs") or {}).get(run_id) or {}).get("stage") == "discover":
                    return hb
            return None
        hb = wait_for(in_run)
        time.sleep(1.5)  # let the finished steps' checkpoints become durable
        psutil.Process(hb["pid"]).kill()

        # 4. restart, reclaim, resume, success
        wait_for(lambda: q.get(run_id).status is JobStatus.SUCCEEDED, timeout=120)
        job = q.get(run_id)
        assert job.resumed is True

        # 5. the new worker reports failing span exports (dead collector) while alive
        def exports_failing():
            with Session(pg_engine) as s:
                for w in s.scalars(select(WorkerInstanceRecord)
                                   .where(WorkerInstanceRecord.stopped_at.is_(None))):
                    t = (w.last_sample or {}).get("telemetry") or {}
                    if t.get("exports_failed"):
                        return w
            return None
        wait_for(exports_failing, timeout=60)

        # 6a. monitoring API shows what happened
        crashes = api.get("/monitoring/crashes").json()
        assert len(crashes) == 1 and crashes[0]["instance_id"] == hb["instance_id"]
        assert crashes[0]["facts"]["affected_runs"] == [run_id]
        (incident,) = api.get("/monitoring/recovery").json()
        assert incident["worker_restart"] == "RESTARTED"
        assert [(r["run_id"], r["research_outcome"]) for r in incident["runs"]] == [
            (run_id, "RESUMED")]
        detail = api.get(f"/monitoring/runs/{run_id}").json()
        assert detail["llm"]["source"] == "ledger" and detail["llm"]["invocations"] >= 3
        assert detail["llm"]["cost_status"] == "UNKNOWN"  # VT-style scripted model: no price
        assert detail["llm"]["input_tokens"] is not None
        assert {e["kind"] for e in detail["lifecycle"]} >= {
            "JOB_INTERRUPTED", "JOB_RECLAIMED", "CHECKPOINT_RECOVERY_ATTEMPTED", "JOB_RESUMED"}
        assert detail["time"]["other_wait_s"] > 0  # time lost to the crash, not "processing"
        assert detail["pipeline"]["documents"].get("OK", 0) >= 1
        assert detail["pipeline"]["chunks"] > 0 and detail["pipeline"]["search_results"] > 0
        assert any(t["kind"] == "RECOVERY" and t["sampled"] for t in detail["traces"])
        workers = {w["instance_id"]: w for w in api.get("/monitoring/workers").json()}
        assert workers[hb["instance_id"]]["status"] == "EXITED"
        assert any(w["status"] == "ALIVE" for w in workers.values())
        found = api.get("/monitoring/alerts").json()["alerts"]
        titles = {a["title"] for a in found}
        assert "Worker crashed" in titles
        assert "Span export failing" in titles
        assert {"otel_collector unreachable", "langfuse unreachable"} <= titles

        # 6b. the dashboard shows the same events
        from test_ui_app import APP
        from streamlit.testing.v1 import AppTest
        client = ResearchApiClient("http://testserver")
        client.http = api
        at = AppTest.from_file(APP, default_timeout=60)
        at.session_state["client"] = client
        at.run()
        at.sidebar.radio(key="mode").set_value("Monitoring").run()
        assert not at.exception, at.exception
        shown = "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption]
                          + [e.value for e in at.error] + [e.label for e in at.expander])
        assert "Worker crashed" in shown
        assert "🔄 Worker restarted (a new worker process started)" in shown
        assert f"Run {run_id[:8]}: ✅ Research resumed from its checkpoint" in shown
        assert "ended unexpectedly" in shown
    finally:
        sup.request_stop()
        thread.join(60)
