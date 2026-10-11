import io
from contextlib import redirect_stdout
from pathlib import Path
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from water_source_research.db.models import Base, include_object_for
from water_source_research.db.session import init_db  # noqa: F401 - registers tables on Base.metadata

ROOT = Path(__file__).resolve().parents[1]

def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg

def test_migrations_match_models_and_round_trip(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"include_object": include_object_for(conn.dialect.name)})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []
    tables = set(inspect(engine).get_table_names())
    assert {"context_bundles", "research_runs", "run_events", "trajectories",
            "distillation_jobs", "procedures", "procedure_versions", "procedure_origins",
            "procedure_usages"} <= tables
    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()

def test_postgres_migration_sql_includes_pgvector_and_one_active_index():
    cfg = alembic_config("postgresql+psycopg://user:pw@localhost/db")
    buf = io.StringIO()
    cfg.output_buffer = buf
    with redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)
    sql = buf.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "VECTOR(1536)" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "uq_procedure_versions_one_active ON procedure_versions (procedure_id) " \
           "WHERE status = 'ACTIVE'" in sql
