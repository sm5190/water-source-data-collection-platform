"""Shared fixtures for Postgres + pgvector integration tests.

Skipped unless TEST_DATABASE_URL points at a disposable Postgres with pgvector, e.g.:
    docker compose up -d postgres
    docker compose exec postgres psql -U water -d water_source -c "CREATE DATABASE water_source_test"
    TEST_DATABASE_URL=postgresql+psycopg://water:water@localhost:5432/water_source_test \
        uv run pytest tests/integration -q
The database is migrated with Alembic and downgraded to empty afterwards.
"""

import os
import sys
from pathlib import Path
import pytest

URL = os.environ.get("TEST_DATABASE_URL")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def pytest_collection_modifyitems(config, items):
    if URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set (needs Postgres)")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)

@pytest.fixture(scope="session")
def pg_engine():
    from alembic import command
    from sqlalchemy import create_engine
    from test_migrations import alembic_config
    cfg = alembic_config(URL)
    command.upgrade(cfg, "head")
    eng = create_engine(URL, pool_size=20, max_overflow=10)
    yield eng
    with eng.begin() as conn:  # LangGraph's checkpointer creates these itself (not Alembic)
        from sqlalchemy import text
        conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                          "checkpoints, checkpoint_migrations"))
    eng.dispose()
    command.downgrade(cfg, "base")

@pytest.fixture(autouse=True)
def clean(request):
    if "pg_engine" not in request.fixturenames:
        yield
        return
    from sqlalchemy import text
    engine = request.getfixturevalue("pg_engine")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE procedure_usages, procedure_origins, procedure_versions, "
                          "procedures, distillation_jobs, trajectories, run_events, "
                          "research_runs, context_bundles, research_jobs, claim_citations, "
                          "field_claims, run_field_states, evidence_chunks, source_documents "
                          "CASCADE"))
    yield
