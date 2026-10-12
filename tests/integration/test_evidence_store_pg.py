"""A full vertical-slice run on real Postgres (Alembic schema, Postgres checkpointer, JSONB):
queued -> worker -> evidence/claims rows. Mocked discovery/website/VT (fixtures in conftest)."""

import pytest
from acquisition_helpers import MemoryArtifacts, make_acquisition
from conftest import URL
from queue_helpers import make_queue, make_worker
from sqlalchemy import text
from slice_helpers import PDF_URL, TARGETS, ModelScript, make_discovery, make_gateway, make_site
from water_source_research.agents.graph import build_graph
from water_source_research.context_manager import ContextBundleStore, ContextService
from water_source_research.core.config import Settings
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.queue import JobStatus
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.run_recorder import RunRecorder

@pytest.fixture
def drop_checkpoints(pg_engine):
    """The Postgres checkpointer creates its own tables; drop them so the Alembic drift
    test (which may run after this module) sees only migrated tables."""
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                          "checkpoints, checkpoint_migrations"))

def test_queued_slice_run_persists_evidence_on_postgres(pg_engine, drop_checkpoints):
    q = make_queue(pg_engine)
    run_id = q.submit("Toronto", "Canada", field_keys=TARGETS).job.run_id
    assert q.get(run_id).field_keys == TARGETS
    artifacts = MemoryArtifacts()

    async def process():
        async with open_checkpointer(Settings(database_url=URL), pool_size=2) as saver:
            gateway, _ = make_gateway(ModelScript(), structured_max_attempts=1)
            graph = build_graph(
                gateway=gateway, discovery=make_discovery(),
                acquisition=make_acquisition(make_site(), artifact_store=artifacts),
                evidence_store=EvidenceRepository(pg_engine),
                context_service=ContextService(store=ContextBundleStore(pg_engine)),
                run_recorder=RunRecorder(pg_engine, enqueue_distillation_jobs=False),
                max_discovery_rounds=2, checkpointer=saver)
            await make_worker(q, graph).drain()

    run_async(process())
    assert q.get(run_id).status is JobStatus.SUCCEEDED
    repo = EvidenceRepository(pg_engine)
    counts = repo.counts(run_id)
    assert counts["claims"] == 4 and counts["citations"] == 4 and counts["field_states"] == 3
    docs = {d["requested_url"]: d for d in repo.documents(run_id)}
    assert docs[PDF_URL]["raw_object_key"] in artifacts.objects
    withdrawal = next(c for c in repo.claims(run_id)
                      if c["field_key"] == "total_city_water_withdrawals_m3_day")
    assert withdrawal["value"] == 980000
    assert withdrawal["citations"][0]["page_number"] == 2
    assert withdrawal["citations"][0]["chunk_id"]
