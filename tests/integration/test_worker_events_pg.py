"""Worker lifecycle events on real Postgres: lease reclaim + checkpoint recovery with the
real AsyncPostgresSaver, concurrent reclaim, final-attempt reap, and the JSONL -> Postgres
sync after an outage (fixtures/skip logic in conftest.py)."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from conftest import URL
from queue_helpers import Crash, Probe, make_queue, make_worker
from sqlalchemy import create_engine, text
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import Settings
from water_source_research.observability.worker_events import (
    EventLog, EventStore, EventSync, WorkerEvent, build_sink,
)
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.queue import JobStatus

@pytest.fixture
def probe(monkeypatch):
    p = Probe()
    p.install(monkeypatch)
    return p

@pytest.fixture
def drop_checkpoints(pg_engine):
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                          "checkpoints, checkpoint_migrations"))

def kinds(engine, run_id):
    out: dict[str, list[dict]] = {}
    for e in EventStore(engine).events(run_id=run_id):
        out.setdefault(e["kind"], []).append(e)
    return out

def test_crash_reclaim_and_postgres_checkpoint_recovery(pg_engine, probe, drop_checkpoints,
                                                       tmp_path):
    probe.fail_validate_once = Crash
    settings = Settings(database_url=URL, _env_file=None)
    q = make_queue(pg_engine, lease_seconds=0.5, heartbeat_seconds=0.1)
    run_id = q.submit("Oslo", "Norway").job.run_id

    async def dying():
        async with open_checkpointer(settings, pool_size=2) as saver:
            w = make_worker(q, build_graph(max_discovery_rounds=1, checkpointer=saver),
                            "dying", events=build_sink(tmp_path, "dying", pg_engine))
            await w._fill_slots()
            await asyncio.gather(*w.in_flight, return_exceptions=True)

    run_async(dying())
    assert q.get(run_id).status is JobStatus.RUNNING  # nothing recorded: the process "died"
    asyncio.run(asyncio.sleep(0.7))  # lease expires

    async def rescuer():
        async with open_checkpointer(settings, pool_size=2) as saver:
            w = make_worker(q, build_graph(max_discovery_rounds=1, checkpointer=saver),
                            "rescuer", events=build_sink(tmp_path, "rescuer", pg_engine))
            await w.drain()

    run_async(rescuer())
    job = q.get(run_id)
    assert (job.status, job.resumed) == (JobStatus.SUCCEEDED, True)
    assert probe.acquire_calls[run_id] == 1  # resumed, not re-run
    ev = kinds(pg_engine, run_id)
    assert ev["JOB_LEASE_EXPIRED"][0]["facts"]["previous_owner"] == "dying"
    assert ev["JOB_RECLAIMED"][0]["facts"]["new_owner"] == "rescuer"
    assert ev["CHECKPOINT_RECOVERY_ATTEMPTED"][0]["facts"]["next_nodes"] == ["validate"]
    assert ev["CHECKPOINT_RECOVERY_ATTEMPTED"][0]["facts"]["checkpoint_id"]
    assert ev["JOB_RESUMED"][0]["facts"]["outcome"] == "SUCCEEDED"

def test_concurrent_reclaim_records_one_reclaim(pg_engine, tmp_path):
    from water_source_research.db.models import utcnow
    from datetime import timedelta
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from water_source_research.db.research_models import ResearchJobRecord as J
    q = make_queue(pg_engine)
    run_id = q.submit("Lima", "Peru").job.run_id
    assert q.claim("dead-worker").run_id == run_id
    with Session(pg_engine) as s, s.begin():
        s.execute(update(J).where(J.run_id == run_id)
                  .values(lease_until=utcnow() - timedelta(seconds=5)))
    claims, lock = [], threading.Lock()
    barrier = threading.Barrier(6)

    def claim(i):
        barrier.wait()
        job = q.claim(f"w{i}")
        if job is not None:
            with lock:
                claims.append(job)

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(claim, range(6)))
    assert len(claims) == 1
    assert claims[0].reclaimed_from == "dead-worker"

def test_reap_on_final_attempt_reports_the_runs(pg_engine):
    q = make_queue(pg_engine, max_attempts=1, lease_seconds=0.2)
    run_id = q.submit("Quito", "Ecuador").job.run_id
    assert q.claim("dead").run_id == run_id
    asyncio.run(asyncio.sleep(0.3))
    assert q.reap_expired_runs() == [(run_id, "dead", 1)]
    assert q.get(run_id).status is JobStatus.FAILED
    assert q.reap_expired_runs() == []

def test_event_sync_after_a_postgres_outage(pg_engine, tmp_path):
    events_dir = tmp_path / "events"
    log = EventLog(events_dir, "sup-1")
    for i in range(5):
        log.append(WorkerEvent(kind="WORKER_EXITED_UNEXPECTEDLY", source="supervisor",
                               instance_id=f"w{i}", facts={"exit_code": 127},
                               inferred_cause="UNKNOWN_TERMINATION",
                               cause_confidence="UNKNOWN"))
    down = create_engine("postgresql+psycopg://water:water@localhost:1/none",
                         connect_args={"connect_timeout": 1})
    assert EventSync(events_dir, EventStore(down)).sync_pending() == 0
    sync = EventSync(events_dir, EventStore(pg_engine))
    assert sync.sync_pending() == 5
    (events_dir / "sync-state.json").unlink()
    assert sync.sync_pending() == 0  # idempotent by event_id
    stored = EventStore(pg_engine).events()
    assert len(stored) == 5 and stored[0]["facts"] == {"exit_code": 127}
    assert stored[0]["inferred_cause"] == "UNKNOWN_TERMINATION"
