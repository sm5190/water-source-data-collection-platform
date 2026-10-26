"""Usage ledger and monitoring queries on real Postgres (JSONB, timestamps with time zones,
the real queue and worker writing steps)."""

import asyncio
from fastapi.testclient import TestClient
from llm_helpers import Server, chat_json, make_llm
from pydantic import BaseModel
from queue_helpers import Probe, make_graph, make_queue, make_worker
import pytest
from water_source_research.api.main import create_app
from water_source_research.observability import monitoring, usage
from water_source_research.observability.llm_telemetry import llm_call_context

class Answer(BaseModel):
    value: str

@pytest.fixture
def probe(monkeypatch):
    p = Probe()
    p.install(monkeypatch)
    return p

def test_ledger_rows_land_in_postgres(pg_engine):
    rec = usage.UsageRecorder(pg_engine, flush_seconds=0.05)
    usage.set_recorder(rec)
    try:
        with llm_call_context(run_id="r-pg", field_key="water_source"):
            make_llm(Server(chat_json({"value": "x"}, prompt_tokens=10, completion_tokens=2)),
                     task="extract").structured("s", "u", Answer)
        rec.flush()
    finally:
        usage.set_recorder(None)
        rec.close()
    u = monitoring.usage(pg_engine, hours=1, group_by="run")
    (g,) = u["groups"]
    assert (g["group"], g["input_tokens"], g["output_tokens"], g["cost_status"]) == (
        "r-pg", 10, 2, "UNKNOWN")

def test_monitoring_of_a_real_queued_run(pg_engine, probe):
    q = make_queue(pg_engine)
    run_id = q.submit("Oslo", "Norway").job.run_id
    asyncio.run(make_worker(q, make_graph(), "w-pg").drain())
    runs = {r["run_id"]: r for r in monitoring.runs(pg_engine)}
    r = runs[run_id]
    assert r["status"] == "SUCCEEDED" and r["active_s"] is not None
    assert r["queue_wait_s"] is not None and r["wall_s"] >= r["active_s"]
    detail = TestClient(create_app(queue=q)).get(f"/monitoring/runs/{run_id}").json()
    assert detail["stages"][0]["node"] == "plan"
    assert detail["progress"][0] == {"node": "plan", "done": True}
    assert detail["llm"] is None  # no model calls and no ledger rows: nothing, not zeros
    overview = TestClient(create_app(queue=q)).get("/monitoring/overview").json()
    assert overview["runs"]["finished_in_window"].get("SUCCEEDED") == 1
