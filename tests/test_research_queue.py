"""Queue semantics: idempotent submission, exclusive claims, leases/fencing, retries, cancel."""

import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from procedure_helpers import make_engine
from queue_helpers import Clock, make_queue
from water_source_research.research_queue.config import ResearchQueueSettings
from water_source_research.research_queue.queue import JobStatus, city_key

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path, "queue.db")
    yield eng
    eng.dispose()

@pytest.fixture
def clock():
    return Clock()

# --- submission -----------------------------------------------------------------------

def test_submission_is_idempotent_per_city(engine):
    q = make_queue(engine)
    first = q.submit("Toronto", "Canada")
    again = q.submit("  toronto ", "CANADA")
    other = q.submit("Toronto", "United States")
    assert first.created and not again.created and other.created
    assert again.job.run_id == first.job.run_id != other.job.run_id
    assert city_key("São  Paulo", None) == "são paulo|"

def test_new_run_allowed_after_previous_finishes(engine):
    q = make_queue(engine)
    first = q.submit("Toronto", "Canada").job
    job = q.claim("w1")
    assert q.complete(job.run_id, "w1", {"finalized": True}, resumed=False)
    second = q.submit("Toronto", "Canada")
    assert second.created and second.job.run_id != first.run_id

def test_concurrent_submissions_create_one_job(engine):
    q = make_queue(engine)
    barrier = threading.Barrier(12)

    def submit(_):
        barrier.wait()
        return q.submit("Madrid", "Spain")

    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(submit, range(12)))
    assert len({r.job.run_id for r in results}) == 1
    assert sum(r.created for r in results) == 1

# --- claiming -------------------------------------------------------------------------

def test_claims_are_exclusive_under_concurrency(engine):
    q = make_queue(engine)
    for i in range(30):
        q.submit(f"City {i}", "X")
    claimed, lock = [], threading.Lock()
    barrier = threading.Barrier(10)

    def worker(i):
        barrier.wait()
        while (job := q.claim(f"w{i}")) is not None:
            with lock:
                claimed.append(job.run_id)

    with ThreadPoolExecutor(10) as pool:
        list(pool.map(worker, range(10)))
    assert len(claimed) == 30 == len(set(claimed))

def test_priority_then_fifo(engine, clock):
    q = make_queue(engine, clock)
    low = q.submit("A", None).job
    clock.advance(1)
    high = q.submit("B", None, priority=5).job
    clock.advance(1)
    low2 = q.submit("C", None).job
    assert [q.claim("w").run_id for _ in range(3)] == [high.run_id, low.run_id, low2.run_id]

def test_global_running_cap(engine):
    q = make_queue(engine, max_running_global=2)
    for i in range(4):
        q.submit(f"City {i}", None)
    assert q.claim("w1") and q.claim("w2")
    assert q.claim("w3") is None

# --- leases and fencing ---------------------------------------------------------------

def test_expired_lease_is_reclaimed_and_old_worker_is_fenced_out(engine, clock):
    q = make_queue(engine, clock, lease_seconds=30)
    run_id = q.submit("Lima", "Peru").job.run_id
    assert q.claim("w1").run_id == run_id
    assert q.claim("w2") is None  # lease still valid
    clock.advance(31)
    stolen = q.claim("w2")
    assert stolen.run_id == run_id and stolen.attempts == 2
    assert not q.heartbeat(run_id, "w1").lease_held
    assert not q.complete(run_id, "w1", {"stale": True}, resumed=False)
    assert q.fail(run_id, "w1", "late") is None
    assert q.complete(run_id, "w2", {"ok": True}, resumed=True)
    assert q.result(run_id) == {"ok": True}

def test_heartbeat_extends_lease(engine, clock):
    q = make_queue(engine, clock, lease_seconds=30)
    run_id = q.submit("Oslo", "Norway").job.run_id
    q.claim("w1")
    for _ in range(5):
        clock.advance(20)
        assert q.heartbeat(run_id, "w1").lease_held
    assert q.claim("w2") is None

def test_retry_with_backoff_then_final_failure(engine, clock):
    q = make_queue(engine, clock, max_attempts=3, backoff_base_seconds=30,
                   backoff_max_seconds=900)
    run_id = q.submit("Nairobi", "Kenya").job.run_id
    q.claim("w")
    assert q.fail(run_id, "w", "boom 1") is JobStatus.QUEUED
    assert q.claim("w") is None  # backing off 30s
    clock.advance(30)
    q.claim("w")
    assert q.fail(run_id, "w", "boom 2") is JobStatus.QUEUED
    clock.advance(59)
    assert q.claim("w") is None  # second backoff is 60s
    clock.advance(1)
    q.claim("w")
    assert q.fail(run_id, "w", "boom 3") is JobStatus.FAILED
    job = q.get(run_id)
    assert (job.status, job.attempts, job.last_error) == (JobStatus.FAILED, 3, "boom 3")

def test_non_retryable_failure_is_final(engine):
    q = make_queue(engine)
    run_id = q.submit("Quito", None).job.run_id
    q.claim("w")
    assert q.fail(run_id, "w", "bad input", retryable=False) is JobStatus.FAILED

def test_expired_final_attempt_is_reaped(engine, clock):
    q = make_queue(engine, clock, max_attempts=1, lease_seconds=10)
    run_id = q.submit("Lisbon", "Portugal").job.run_id
    q.claim("w")
    clock.advance(11)
    assert q.claim("w2") is None  # no attempts left to reclaim with
    assert q.reap_expired() == 1
    assert q.get(run_id).status is JobStatus.FAILED

def test_release_returns_job_without_consuming_attempt(engine):
    q = make_queue(engine)
    run_id = q.submit("Cairo", "Egypt").job.run_id
    q.claim("w")
    assert q.release(run_id, "w")
    job = q.get(run_id)
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 0)

# --- cancellation ---------------------------------------------------------------------

def test_cancel_queued_and_running(engine):
    q = make_queue(engine)
    running = q.submit("Dakar", "Senegal").job.run_id
    assert q.claim("w").run_id == running
    queued = q.submit("Lagos", "Nigeria").job.run_id
    assert q.cancel(queued).status is JobStatus.CANCELLED  # immediate
    assert q.cancel(running).cancel_requested
    assert q.heartbeat(running, "w").cancel_requested
    assert q.mark_cancelled(running, "w")
    assert q.get(running).status is JobStatus.CANCELLED

def test_settings_validation():
    with pytest.raises(ValueError, match="half of lease"):
        ResearchQueueSettings(lease_seconds=60, heartbeat_seconds=45)
