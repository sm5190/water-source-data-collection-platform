"""Human review on real Postgres: the run pauses in one worker process, decisions arrive
through the API while no worker holds it, and a new process (fresh checkpointer connection
pool) resumes it from the Postgres checkpoint. External services are mocked."""

from uuid import uuid4
import pytest
from acquisition_helpers import make_acquisition
from conftest import URL
from fastapi.testclient import TestClient
from queue_helpers import make_queue, make_worker
from slice_helpers import ModelScript, make_discovery, make_gateway, make_site
from sqlalchemy import text
from test_human_review import ANSWERS, FIELDS, W
from water_source_research.agents.graph import build_graph
from water_source_research.api.main import create_app
from water_source_research.core.config import Settings
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.queue import JobStatus
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.review import ReviewRepository

@pytest.fixture
def drop_checkpoints(pg_engine):
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                          "checkpoints, checkpoint_migrations"))

def test_review_pause_and_resume_across_processes(pg_engine, drop_checkpoints):
    settings = Settings(database_url=URL)
    queue = make_queue(pg_engine)
    evidence, reviews = EvidenceRepository(pg_engine), ReviewRepository(pg_engine)
    client = TestClient(create_app(queue=queue, evidence=evidence, reviews=reviews))
    run_id = client.post("/research/city", json={"city_name": "Toronto", "country": "Canada",
                                                 "field_keys": FIELDS}).json()["run_id"]

    def process(worker_id: str) -> None:
        async def main():
            async with open_checkpointer(settings, pool_size=2) as saver:
                gateway, _ = make_gateway(ModelScript(answers=ANSWERS),
                                          structured_max_attempts=1)
                graph = build_graph(gateway=gateway, discovery=make_discovery(),
                                    acquisition=make_acquisition(make_site()),
                                    max_discovery_rounds=1, evidence_store=evidence,
                                    review_store=reviews, checkpointer=saver)
                await make_worker(queue, graph, worker_id).drain()
        run_async(main())

    process("worker-1")
    job = queue.get(run_id)
    assert job.status is JobStatus.AWAITING_REVIEW and job.review_round == 1
    with pg_engine.connect() as conn:  # the paused state lives in Postgres, not the worker
        assert conn.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id = :r"),
                            {"r": run_id}).scalar() > 0

    items = {i["field_key"]: i for i in client.get(
        f"/research/runs/{run_id}/review/pending").json()["items"]}
    cite = items[W]["payload"]["candidates"][0]["citations"][0]
    r = client.post(f"/research/runs/{run_id}/review/decisions", json={
        "reviewer": "analyst@example.org", "decisions": [
            {"decision_key": uuid4().hex, "item_id": items[W]["id"], "round": 1,
             "decision": "CORRECT", "value": 980000, "reason": "the quote says 980,000",
             "claim_uids": [items[W]["payload"]["candidates"][0]["claim_uid"]],
             "evidence": {"chunk_id": cite["chunk_id"], "quote": "980,000 cubic metres"}},
            {"decision_key": uuid4().hex, "item_id": items["water_type"]["id"], "round": 1,
             "decision": "MARK_UNKNOWN", "reason": "not stated in any acquired source"}]})
    assert r.status_code == 200, r.text
    assert client.post(f"/research/runs/{run_id}/review/resume",
                       json={"reviewer": "analyst@example.org"}).status_code == 202

    process("worker-2")  # a different process picks it up
    job = queue.get(run_id)
    assert job.status is JobStatus.SUCCEEDED
    states = {s["field_key"]: s for s in evidence.field_states(run_id)}
    assert states[W]["status"] == "RESOLVED" and states["water_type"]["status"] == "UNAVAILABLE"
    reviewer_rows = [x for x in evidence.resolutions(run_id) if x["decided_by"] == "REVIEWER"]
    assert sorted(x["field_key"] for x in reviewer_rows) == [W, "water_type"]
    assert all(x["review_decision_id"] for x in reviewer_rows)
