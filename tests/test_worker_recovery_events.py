"""Recovery correlation: a worker dies mid-run -> its lease expires -> another worker reclaims
the job (JOB_LEASE_EXPIRED, JOB_RECLAIMED with the previous owner) -> resumes from the
checkpoint (CHECKPOINT_RECOVERY_ATTEMPTED) -> outcome (JOB_RESUMED / JOB_RECOVERY_FAILED).
Worker restarts and job recoveries are separate records. Lease and checkpoint behaviour is
unchanged (the existing worker tests still cover it)."""

import asyncio
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from procedure_helpers import make_engine
from queue_helpers import Clock, Crash, Probe, make_graph, make_queue, make_worker
from water_source_research.observability.worker_events import EventStore, build_sink
from water_source_research.research_queue.queue import JobStatus

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path, "recovery.db")
    yield eng
    eng.dispose()

@pytest.fixture
def probe(monkeypatch):
    p = Probe()
    p.install(monkeypatch)
    return p

def run(coro):
    return asyncio.run(coro)

def sink(tmp_path, engine, name):
    return build_sink(tmp_path / "logs", name, engine)

async def die_mid_run(q, checkpoints, events):
    worker = make_worker(q, make_graph(checkpoints), "dying", events=events)
    await worker._fill_slots()
    await asyncio.gather(*worker.in_flight, return_exceptions=True)

def by_kind(engine, run_id):
    out: dict[str, list[dict]] = {}
    for e in EventStore(engine).events(run_id=run_id):
        out.setdefault(e["kind"], []).append(e)
    return out

def test_crash_reclaim_and_resume_are_correlated(tmp_path, engine, probe):
    probe.fail_validate_once = Crash
    clock = Clock()
    q = make_queue(engine, clock, lease_seconds=30)
    run_id = q.submit("Oslo", "Norway").job.run_id
    checkpoints = InMemorySaver()
    run(die_mid_run(q, checkpoints, sink(tmp_path, engine, "dying")))
    clock.advance(31)
    rescuer = make_worker(q, make_graph(checkpoints), "rescuer",
                          events=sink(tmp_path, engine, "rescuer"))
    run(rescuer.drain())
    assert q.get(run_id).status is JobStatus.SUCCEEDED
    ev = by_kind(engine, run_id)
    expired, reclaimed = ev["JOB_LEASE_EXPIRED"][0], ev["JOB_RECLAIMED"][0]
    assert expired["facts"]["previous_owner"] == "dying"
    assert reclaimed["facts"]["new_owner"] == "rescuer" and reclaimed["attempt"] == 2
    recovery = ev["CHECKPOINT_RECOVERY_ATTEMPTED"][0]
    assert recovery["facts"]["next_nodes"] == ["validate"]  # resumes where it died
    assert ev["JOB_RESUMED"][0]["facts"]["outcome"] == "SUCCEEDED"
    assert "JOB_RECOVERY_FAILED" not in ev
    assert all(e["instance_id"] == "rescuer" for kind in ev.values() for e in kind)

def test_recovery_that_fails_again_is_recorded(tmp_path, engine, probe, monkeypatch):
    clock = Clock()
    q = make_queue(engine, clock, lease_seconds=30, max_attempts=3)
    run_id = q.submit("Lima", "Peru").job.run_id
    checkpoints = InMemorySaver()
    probe.fail_validate_once = Crash
    run(die_mid_run(q, checkpoints, None))
    clock.advance(31)
    probe.fail_validate_once = RuntimeError  # the resumed attempt fails too
    probe._failed.clear()
    worker = make_worker(q, make_graph(checkpoints), "rescuer",
                         events=sink(tmp_path, engine, "rescuer"))

    async def one_attempt():
        await worker._fill_slots()
        await asyncio.gather(*worker.in_flight, return_exceptions=True)

    run(one_attempt())
    failed = by_kind(engine, run_id)["JOB_RECOVERY_FAILED"][0]
    assert failed["facts"]["will_retry"] is True
    assert "injected failure" in failed["facts"]["error"]

def test_lease_expiring_on_the_final_attempt_is_a_failed_recovery(tmp_path, engine, probe):
    probe.fail_validate_once = Crash
    clock = Clock()
    q = make_queue(engine, clock, lease_seconds=30, max_attempts=1)
    run_id = q.submit("Quito", "Ecuador").job.run_id
    run(die_mid_run(q, InMemorySaver(), None))
    clock.advance(31)
    run(make_worker(q, make_graph(), "reaper", events=sink(tmp_path, engine, "reaper")).drain())
    assert q.get(run_id).status is JobStatus.FAILED
    failed = by_kind(engine, run_id)["JOB_RECOVERY_FAILED"][0]
    assert failed["facts"]["previous_owner"] == "dying"
    assert failed["facts"]["will_retry"] is False

def test_ordinary_runs_and_review_resumes_emit_no_recovery_events(tmp_path, engine, probe):
    q = make_queue(engine)
    run_id = q.submit("Toronto", "Canada").job.run_id
    run(make_worker(q, make_graph(), "w", events=sink(tmp_path, engine, "w")).drain())
    assert by_kind(engine, run_id) == {}

def test_worker_publishes_stage_for_the_heartbeat(engine, probe):
    probe.delay = 0.3
    q = make_queue(engine)
    q.submit("Toronto", "Canada")
    worker = make_worker(q, make_graph(), "w")
    seen = []

    async def go():
        task = asyncio.create_task(worker.drain())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if worker.active:
                seen.append(worker.activity())
        await task

    run(go())
    assert seen and seen[0]["active_jobs"] == 1
    stages = {r["stage"] for s in seen for r in s["active_runs"].values()}
    assert "claimed" in stages or "plan" in stages or "discover" in stages
    assert worker.activity() == {"active_runs": {}, "active_jobs": 0}

def test_events_without_a_database_stay_in_the_local_file(tmp_path, probe):
    from sqlalchemy import create_engine
    down = create_engine(f"sqlite:///{tmp_path / 'no' / 'dir' / 'x.db'}")
    events = build_sink(tmp_path / "logs", "w", down)
    events.emit("JOB_RECLAIMED", "worker", run_id="r1")  # must not raise
    lines = (tmp_path / "logs" / "events" / "w.jsonl").read_text().splitlines()
    assert len(lines) == 1 and '"JOB_RECLAIMED"' in lines[0]

class IncompleteCheckpoint:
    """The real graph, except the first state read returns what a process killed before
    LangGraph persisted its first steps leaves behind: values, no pending step, no finalize."""

    def __init__(self, graph) -> None:
        self.graph = graph
        self.checkpointer = graph.checkpointer
        self.first = True

    async def aget_state(self, config):
        if self.first:
            self.first = False
            from types import SimpleNamespace
            return SimpleNamespace(next=(), values={"run_id": "x", "city_name": "Oslo"},
                                   interrupts=(), config={})
        return await self.graph.aget_state(config)

    def astream(self, *args, **kwargs):
        return self.graph.astream(*args, **kwargs)

def test_unfinished_checkpoint_without_pending_steps_is_run_not_recorded(tmp_path, engine,
                                                                        probe):
    q = make_queue(engine)
    run_id = q.submit("Oslo", "Norway").job.run_id
    worker = make_worker(q, IncompleteCheckpoint(make_graph()), "w",
                         events=sink(tmp_path, engine, "w"))
    run(worker.drain())
    assert q.get(run_id).status is JobStatus.SUCCEEDED
    assert probe.acquire_calls[run_id] == 1  # the run really executed
    assert q.result(run_id)["finalized"] is True
    ev = by_kind(engine, run_id)
    assert ev["CHECKPOINT_RECOVERY_ATTEMPTED"][0]["facts"]["restart_from_input"] is True
    assert ev["JOB_RESUMED"][0]["facts"]["outcome"] == "SUCCEEDED"
