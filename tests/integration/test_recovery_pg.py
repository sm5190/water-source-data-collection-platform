"""Crash/resume of a vertical-slice run with the REAL Postgres checkpointer and real Postgres
business tables. External services (discovery, website, VT) are mocked here; the live drill is
scripts/recovery_drill.py. Separates checkpoint persistence from business-data persistence."""

import asyncio
import pytest
from acquisition_helpers import make_acquisition
from conftest import URL
from queue_helpers import Crash, make_queue, make_worker
from slice_helpers import (
    PDF_URL, TARGETS, UTIL_URL, ModelScript, make_discovery, make_gateway, make_site,
)
from sqlalchemy import text
from water_source_research.agents.graph import build_graph
from water_source_research.context_manager import ContextBundleStore, ContextService
from water_source_research.core.config import Settings
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.queue import JobStatus
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.run_recorder import RunRecorder

@pytest.fixture
def drop_checkpoints(pg_engine):
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                          "checkpoints, checkpoint_migrations"))

class CrashOnce:
    """Gateway wrapper: the first extraction kills the 'process' (escapes except Exception)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.crashed = False

    async def extract(self, bundle, *, run_id, field_key):
        if not self.crashed:
            self.crashed = True
            raise Crash("worker process killed during extraction")
        return await self.inner.extract(bundle, run_id=run_id, field_key=field_key)

def checkpoint_rows(engine, run_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id = :r"),
                            {"r": run_id}).scalar()

def test_crash_after_acquire_resumes_from_postgres_checkpoint(pg_engine, drop_checkpoints):
    settings = Settings(database_url=URL)
    q = make_queue(pg_engine, lease_seconds=0.5, heartbeat_seconds=0.1, keep_checkpoints=True)
    run_id = q.submit("Toronto", "Canada", field_keys=TARGETS).job.run_id
    site = make_site()
    inner, _ = make_gateway(ModelScript(), structured_max_attempts=1)
    gateway = CrashOnce(inner)
    repo = EvidenceRepository(pg_engine)

    def graph(saver):
        return build_graph(
            gateway=gateway, discovery=make_discovery(), acquisition=make_acquisition(site),
            evidence_store=repo, context_service=ContextService(store=ContextBundleStore(pg_engine)),
            run_recorder=RunRecorder(pg_engine, enqueue_distillation_jobs=False),
            max_discovery_rounds=2, checkpointer=saver)

    async def dying_process():
        async with open_checkpointer(settings, pool_size=2) as saver:
            worker = make_worker(q, graph(saver), "dying")
            await worker._fill_slots()
            await asyncio.gather(*worker.in_flight, return_exceptions=True)

    run_async(dying_process())
    # Interrupted: job still RUNNING under a lease that will expire; plan/discover/acquire
    # are checkpointed, and acquire already wrote its business rows.
    assert q.get(run_id).status is JobStatus.RUNNING
    assert checkpoint_rows(pg_engine, run_id) > 0
    fetched_before = list(site.fetched())
    docs_before = repo.documents(run_id)
    assert {UTIL_URL, PDF_URL} <= {d["requested_url"] for d in docs_before}
    assert repo.counts(run_id)["claims"] == 0  # nothing extracted yet
    asyncio.run(asyncio.sleep(0.7))  # lease expires

    async def new_process():
        async with open_checkpointer(settings, pool_size=2) as saver:  # fresh connections
            await make_worker(q, graph(saver), "rescuer").drain()

    run_async(new_process())
    job = q.get(run_id)
    assert (job.status, job.attempts, job.resumed) == (JobStatus.SUCCEEDED, 2, True)
    assert site.fetched() == fetched_before  # acquisition was not repeated
    attempt2 = [s["node"] for s in q.steps(run_id) if s["attempt"] == 2]
    assert attempt2[0] == "extract" and "acquire" not in attempt2[:1]
    assert "plan" not in attempt2 and attempt2.count("discover") == 0

    result = q.result(run_id)
    assert result["discovery_candidates"] and result["acquired_documents"]
    assert len(repo.documents(run_id)) == len(docs_before)  # no duplicate documents
    counts = repo.counts(run_id)
    state_claims = sum(len(f["candidate_claims"]) for f in
                       result["research_state"]["fields"].values())
    assert counts["claims"] == state_claims == 4
    assert checkpoint_rows(pg_engine, run_id) > 0  # kept (keep_checkpoints=True)
