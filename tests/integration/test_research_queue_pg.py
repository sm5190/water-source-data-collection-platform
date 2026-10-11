"""City-run queue on real Postgres: SKIP LOCKED, partial unique index, advisory-lock cap,
and crash-resume through the Postgres checkpointer (fixtures/skip logic in conftest.py)."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from conftest import URL
from queue_helpers import Crash, Probe, make_queue, make_worker
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import Settings
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.queue import JobStatus

def test_skip_locked_claims_are_exclusive(pg_engine):
    q = make_queue(pg_engine)
    for i in range(40):
        q.submit(f"City {i}", "X")
    claimed, lock = [], threading.Lock()
    barrier = threading.Barrier(12)

    def worker(i):
        barrier.wait()
        while (job := q.claim(f"w{i}")) is not None:
            with lock:
                claimed.append(job.run_id)

    with ThreadPoolExecutor(12) as pool:
        list(pool.map(worker, range(12)))
    assert len(claimed) == 40 == len(set(claimed))

def test_duplicate_submissions_race_on_partial_unique_index(pg_engine):
    q = make_queue(pg_engine)
    barrier = threading.Barrier(16)

    def submit(_):
        barrier.wait()
        return q.submit("Nairobi", "Kenya")

    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(submit, range(16)))
    assert len({r.job.run_id for r in results}) == 1
    assert sum(r.created for r in results) == 1

def test_global_cap_holds_under_concurrent_claims(pg_engine):
    q = make_queue(pg_engine, max_running_global=3)
    for i in range(10):
        q.submit(f"City {i}", None)
    barrier = threading.Barrier(10)

    def claim(i):
        barrier.wait()
        return q.claim(f"w{i}")

    with ThreadPoolExecutor(10) as pool:
        got = [j for j in pool.map(claim, range(10)) if j]
    assert len(got) == 3

def test_crash_resume_through_postgres_checkpointer(pg_engine, monkeypatch):
    probe = Probe()
    probe.install(monkeypatch)
    probe.fail_validate_once = Crash
    settings = Settings(database_url=URL)
    q = make_queue(pg_engine, lease_seconds=0.5, heartbeat_seconds=0.1)
    run_id = q.submit("Lima", "Peru").job.run_id

    async def dying_process():
        async with open_checkpointer(settings, pool_size=2) as saver:
            worker = make_worker(q, build_graph(max_discovery_rounds=1, checkpointer=saver),
                                 "dying")
            await worker._fill_slots()
            await asyncio.gather(*worker.in_flight, return_exceptions=True)

    async def new_process():
        await asyncio.sleep(0.6)  # let the dead worker's lease expire
        async with open_checkpointer(settings, pool_size=2) as saver:  # fresh connections
            worker = make_worker(q, build_graph(max_discovery_rounds=1, checkpointer=saver),
                                 "rescuer")
            await worker.drain()

    run_async(dying_process())
    assert q.get(run_id).status is JobStatus.RUNNING
    run_async(new_process())
    job = q.get(run_id)
    assert (job.status, job.attempts, job.resumed) == (JobStatus.SUCCEEDED, 2, True)
    assert probe.acquire_calls[run_id] == 1  # steps before the crash were not redone
    assert q.result(run_id)["finalized"] is True

@pytest.mark.parametrize("workers", [3])
def test_multiple_worker_processes_drain_queue_once(pg_engine, monkeypatch, workers):
    probe = Probe(delay=0.1)
    probe.install(monkeypatch)
    settings = Settings(database_url=URL)
    q = make_queue(pg_engine, worker_concurrency=2)
    runs = [q.submit(f"City {i}", None).job.run_id for i in range(12)]

    async def process(name):
        async with open_checkpointer(settings, pool_size=3) as saver:
            graph = build_graph(max_discovery_rounds=1, checkpointer=saver)
            return await make_worker(q, graph, name).drain()

    async def all_workers():
        return await asyncio.gather(*(process(f"p{i}") for i in range(workers)))

    counts = run_async(all_workers())
    assert sum(counts) == 12
    assert all(probe.acquire_calls[r] == 1 for r in runs)
    assert all(q.get(r).status is JobStatus.SUCCEEDED for r in runs)
