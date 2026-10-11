"""Async worker behaviour: concurrency, resume-after-crash, timeouts, cancel, shutdown, API."""

import asyncio
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from procedure_helpers import make_engine
from queue_helpers import Clock, Crash, Probe, make_graph, make_queue, make_worker
from sqlalchemy import update
from sqlalchemy.orm import Session
from water_source_research.api.main import create_app
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.research_queue.queue import JobStatus

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path, "worker.db")
    yield eng
    eng.dispose()

@pytest.fixture
def probe(monkeypatch):
    p = Probe()
    p.install(monkeypatch)
    return p

def run(coro):
    return asyncio.run(coro)

async def wait_for_status(queue, run_id, status, timeout=5.0):
    for _ in range(int(timeout / 0.01)):
        if queue.get(run_id).status is status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{run_id} never reached {status}")

# --- execution and concurrency --------------------------------------------------------

def test_worker_executes_a_queued_city_run(engine, probe):
    q = make_queue(engine)
    run_id = q.submit("Toronto", "Canada").job.run_id
    assert run(make_worker(q, make_graph()).drain()) == 1
    job = q.get(run_id)
    assert (job.status, job.attempts, job.resumed) == (JobStatus.SUCCEEDED, 1, False)
    result = q.result(run_id)
    assert result["finalized"] is True
    assert result["research_state"]["city_name"] == "Toronto"
    assert "utility_name" in result["fields_needing_evidence"]  # stubs find no evidence yet

def test_worker_concurrency_limit_is_respected(engine, probe):
    probe.delay = 0.3
    q = make_queue(engine, worker_concurrency=3)
    runs = [q.submit(f"City {i}", None).job.run_id for i in range(8)]
    worker = make_worker(q, make_graph())
    assert run(worker.drain()) == 8
    assert worker.max_in_flight_seen == 3  # never more than the limit...
    assert probe.max_in_flight == 3  # ...and the limit is actually used
    assert all(q.get(r).status is JobStatus.SUCCEEDED for r in runs)

def test_two_workers_share_queue_without_duplicate_execution(engine, probe):
    probe.delay = 0.05
    q = make_queue(engine, worker_concurrency=2)
    runs = [q.submit(f"City {i}", None).job.run_id for i in range(10)]
    graph = make_graph()

    async def both():
        return await asyncio.gather(make_worker(q, graph, "A").drain(),
                                    make_worker(q, graph, "B").drain())

    counts = run(both())
    assert sum(counts) == 10 and all(c > 0 for c in counts)
    assert all(probe.acquire_calls[r] == 1 for r in runs)  # each run executed exactly once

# --- recovery -------------------------------------------------------------------------

def test_failed_attempt_resumes_from_checkpoint(engine, probe):
    probe.fail_validate_once = RuntimeError
    q = make_queue(engine)
    run_id = q.submit("Lima", "Peru").job.run_id
    run(make_worker(q, make_graph()).drain())  # attempt 1 fails in validate, attempt 2 resumes
    job = q.get(run_id)
    assert (job.status, job.attempts, job.resumed) == (JobStatus.SUCCEEDED, 2, True)
    assert probe.acquire_calls[run_id] == 1  # completed steps were not re-executed
    assert probe.validate_calls[run_id] == 2

def test_worker_crash_is_recovered_by_another_worker(engine, probe):
    probe.fail_validate_once = Crash  # escapes the worker entirely, like a killed process
    clock = Clock()
    q = make_queue(engine, clock, lease_seconds=30)
    run_id = q.submit("Oslo", "Norway").job.run_id
    checkpoints = InMemorySaver()  # stands in for the shared Postgres checkpointer

    async def crashing_worker():
        worker = make_worker(q, make_graph(checkpoints), "dying")
        await worker._fill_slots()
        await asyncio.gather(*worker.in_flight, return_exceptions=True)

    run(crashing_worker())
    assert q.get(run_id).status is JobStatus.RUNNING  # nothing recorded: the worker "died"
    clock.advance(31)  # lease expires
    run(make_worker(q, make_graph(checkpoints), "rescuer").drain())
    job = q.get(run_id)
    assert (job.status, job.attempts, job.resumed) == (JobStatus.SUCCEEDED, 2, True)
    assert probe.acquire_calls[run_id] == 1

def test_finished_but_unrecorded_run_is_recorded_without_rerunning(engine, probe):
    clock = Clock()
    q = make_queue(engine, clock, lease_seconds=30)
    run_id = q.submit("Cairo", "Egypt").job.run_id
    checkpoints = InMemorySaver()
    original_complete = q.complete

    def crash_on_complete(*args, **kwargs):
        raise Crash("died after the graph finished")

    q.complete = crash_on_complete

    async def first():
        worker = make_worker(q, make_graph(checkpoints), "dying")
        await worker._fill_slots()
        await asyncio.gather(*worker.in_flight, return_exceptions=True)

    run(first())
    q.complete = original_complete
    clock.advance(31)
    run(make_worker(q, make_graph(checkpoints), "rescuer").drain())
    assert q.get(run_id).status is JobStatus.SUCCEEDED
    assert probe.acquire_calls[run_id] == 1 and probe.validate_calls[run_id] == 1

def test_timeout_retries_then_fails(engine, probe):
    probe.delay = 1.0
    q = make_queue(engine, max_run_seconds=0.1, max_attempts=2)
    run_id = q.submit("Quito", "Ecuador").job.run_id
    run(make_worker(q, make_graph()).drain())
    job = q.get(run_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 2)
    assert "exceeded" in job.last_error

# --- cancellation, fencing, shutdown --------------------------------------------------

def test_cancel_running_job(engine, probe):
    probe.delay = 5.0
    q = make_queue(engine)
    run_id = q.submit("Accra", "Ghana").job.run_id

    async def scenario():
        drain = asyncio.create_task(make_worker(q, make_graph()).drain())
        await wait_for_status(q, run_id, JobStatus.RUNNING)
        q.cancel(run_id)
        await asyncio.wait_for(drain, timeout=3)  # stops well before the 5s of "work"

    run(scenario())
    assert q.get(run_id).status is JobStatus.CANCELLED

def test_lost_lease_stops_run_without_overwriting(engine, probe):
    probe.delay = 5.0
    q = make_queue(engine)
    run_id = q.submit("Dakar", "Senegal").job.run_id

    async def scenario():
        drain = asyncio.create_task(make_worker(q, make_graph(), "original").drain())
        await wait_for_status(q, run_id, JobStatus.RUNNING)
        with Session(engine) as s, s.begin():  # another worker took over the job
            s.execute(update(ResearchJobRecord).where(ResearchJobRecord.run_id == run_id)
                      .values(lease_owner="usurper"))
        await asyncio.wait_for(drain, timeout=3)

    run(scenario())
    job = q.get(run_id)
    assert (job.status, job.lease_owner) == (JobStatus.RUNNING, "usurper")
    assert q.result(run_id) is None

def test_graceful_shutdown_releases_in_flight_runs(engine, probe):
    probe.delay = 5.0
    q = make_queue(engine, shutdown_grace_seconds=0)
    run_id = q.submit("Lagos", "Nigeria").job.run_id

    async def scenario():
        stop = asyncio.Event()
        loop = asyncio.create_task(make_worker(q, make_graph()).run(stop))
        await wait_for_status(q, run_id, JobStatus.RUNNING)
        stop.set()
        await asyncio.wait_for(loop, timeout=3)

    run(scenario())
    job = q.get(run_id)
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 0)

def test_checkpoints_are_deleted_after_success_unless_kept(engine, probe):
    async def checkpoint_after(keep):
        q = make_queue(engine, keep_checkpoints=keep)
        run_id = q.submit(f"City keep={keep}", None).job.run_id
        graph = make_graph()
        await make_worker(q, graph).drain()
        return (await graph.aget_state({"configurable": {"thread_id": run_id}})).values

    assert run(checkpoint_after(False)) == {}
    assert run(checkpoint_after(True))["finalized"] is True

def test_post_run_hook_failure_does_not_fail_the_run(engine, probe):
    q = make_queue(engine)
    run_id = q.submit("Lisbon", "Portugal").job.run_id

    def broken_hook(run_id):
        raise RuntimeError("distillation down")

    run(make_worker(q, make_graph(), on_success=broken_hook).drain())
    assert q.get(run_id).status is JobStatus.SUCCEEDED

def test_run_span_and_submit_span(engine, probe, spans):
    q = make_queue(engine)
    run_id = q.submit("Toronto", "Canada").job.run_id
    run(make_worker(q, make_graph()).drain())
    finished = {s.name: s for s in spans.get_finished_spans()}
    assert finished["research_queue.submit"].attributes["research.run_id"] == run_id
    run_span = finished["research.run"]
    assert run_span.attributes["research_queue.outcome"] == "succeeded"
    assert run_span.attributes["research_queue.attempt"] == 1

# --- API ------------------------------------------------------------------------------

def test_api_submits_asynchronously_and_reports_status(engine, probe):
    q = make_queue(engine)
    client = TestClient(create_app(queue=q))
    created = client.post("/research/city", json={"city_name": "Toronto", "country": "Canada"})
    assert created.status_code == 202
    run_id = created.json()["run_id"]
    assert created.json()["status"] == "QUEUED"
    duplicate = client.post("/research/city", json={"city_name": "toronto", "country": "canada"})
    assert duplicate.status_code == 200 and duplicate.json()["run_id"] == run_id
    assert client.get(f"/research/runs/{run_id}/result").status_code == 409
    assert client.get("/research/queue/stats").json()["by_status"] == {"QUEUED": 1}

    run(make_worker(q, make_graph()).drain())

    status = client.get(f"/research/runs/{run_id}").json()
    assert status["status"] == "SUCCEEDED" and status["attempts"] == 1
    result = client.get(f"/research/runs/{run_id}/result").json()
    assert result["finalized"] and result["research_state"]["city_name"] == "Toronto"
    assert [r["run_id"] for r in client.get("/research/runs").json()] == [run_id]
    assert client.get("/research/runs/nope").status_code == 404

def test_api_cancel(engine):
    q = make_queue(engine)
    client = TestClient(create_app(queue=q))
    run_id = client.post("/research/city", json={"city_name": "Oslo"}).json()["run_id"]
    assert client.post(f"/research/runs/{run_id}/cancel").json()["status"] == "CANCELLED"
    # A cancelled run frees the city for a new submission.
    assert client.post("/research/city", json={"city_name": "Oslo"}).status_code == 202
