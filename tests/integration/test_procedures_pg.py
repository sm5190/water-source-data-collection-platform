"""Procedural memory on Postgres + pgvector (fixtures and skip logic in conftest.py)."""

import threading
from concurrent.futures import ThreadPoolExecutor

def test_migrations_have_no_drift_on_postgres(pg_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import text
    from water_source_research.db.models import Base, include_object_for
    from water_source_research.db.session import init_db  # noqa: F401 - registers tables on Base.metadata
    with pg_engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"include_object": include_object_for(conn.dialect.name)})
        assert compare_metadata(ctx, Base.metadata) == []
        indexes = dict(conn.execute(text(
            "select indexname, indexdef from pg_indexes where tablename='procedure_versions'")).all())
    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_procedure_versions_embedding_hnsw"]
    assert "WHERE" in indexes["uq_procedure_versions_one_active"]

def test_pgvector_similarity_inside_filtered_set(pg_engine):
    from procedure_helpers import FakeEmbedder, recipe, seed_version, settings
    from water_source_research.core.versions import WORKFLOW_VERSION
    from water_source_research.procedures.repository import ProcedureRepository
    from water_source_research.procedures.retrieval import ProcedureMemory
    from water_source_research.schemas.procedures import RetrievalRequest
    repo, emb = ProcedureRepository(pg_engine), FakeEmbedder()
    wanted = seed_version(repo, recipe(), embedder=emb)
    seed_version(repo, recipe("water_quality", sources=("regulatory_report",)), embedder=emb)
    result = ProcedureMemory(repo, emb, settings()).retrieve(RetrievalRequest(
        task_type="withdrawal_research", field_type="withdrawal",
        workflow_version=WORKFLOW_VERSION))
    assert not result.vector_degraded
    assert [m.procedure_version_id for m in result.matches] == [wanted]
    assert result.matches[0].similarity_score is not None

def test_concurrent_equivalent_distillation_on_postgres(pg_engine):
    from procedure_helpers import make_worker, persist_run, run_state
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    from water_source_research.db.models import utcnow
    from water_source_research.db.procedure_models import ProcedureVersionRecord
    from water_source_research.procedures.repository import ProcedureRepository
    repo = ProcedureRepository(pg_engine)
    keys = []
    for i, city in enumerate(["Toronto", "Madrid", "Lisbon", "Nairobi", "Lima", "Oslo"]):
        ks = persist_run(pg_engine, run_state(f"pg-{i}", city))
        keys.append(next(k for k in ks if repo.get_job(k)["field_type"] == "withdrawal"))
    barrier = threading.Barrier(len(keys))

    def work(key):
        worker = make_worker(ProcedureRepository(pg_engine))
        assert worker.repo.claim_job(key, utcnow(), 300, 3)
        barrier.wait()
        return worker.process(key).outcome.value

    with ThreadPoolExecutor(len(keys)) as pool:
        outcomes = list(pool.map(work, keys))
    assert outcomes.count("created") == 1
    with Session(pg_engine) as s:
        assert s.scalar(select(func.count()).select_from(ProcedureVersionRecord)) == 1

def test_partial_unique_index_allows_one_active_version(pg_engine):
    from procedure_helpers import recipe, seed_version
    from water_source_research.procedures.repository import ProcedureRepository
    from water_source_research.schemas.procedures import ProcedureStatus as S
    repo = ProcedureRepository(pg_engine)
    ids = [seed_version(repo, recipe(sources=(f"s{i}",)), procedure_tag="same",
                        status=S.CANDIDATE) for i in range(5)]
    barrier = threading.Barrier(len(ids))

    def promote(vid):
        barrier.wait()
        return ProcedureRepository(pg_engine).promote(vid, {S.CANDIDATE})

    with ThreadPoolExecutor(len(ids)) as pool:
        list(pool.map(promote, ids))
    statuses = [v.status for v in repo.versions_for_procedure(repo.get_version(ids[0]).procedure_id)]
    assert statuses.count(S.ACTIVE) == 1
