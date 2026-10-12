"""Database readiness report against the REAL configured PostgreSQL (not run by pytest).

Usage: uv run python scripts/db_readiness.py [--upgrade]

Checks the connection, the Alembic revision, schema drift between the live database and the
SQLAlchemy models (excluding LangGraph's own checkpoint tables), initializes the checkpoint
tables through the app's own ``open_checkpointer`` (LangGraph ``setup()``), lists every
required table with row counts / keys / foreign keys / indexes from the system catalogs, and
round-trips isolated test rows inside a transaction that is ROLLED BACK. Never drops,
truncates or uses ``create_all()``. ``--upgrade`` runs ``alembic upgrade head`` if behind.
Exit 1 if anything required is missing.
"""

import argparse
import sys
from datetime import UTC, datetime
from uuid import uuid4
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from water_source_research.core.config import get_settings
from water_source_research.core.paths import REPO_ROOT
from water_source_research.db.models import Base, include_object_for
from water_source_research.db.session import get_engine
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs",
                     "checkpoint_migrations")
# table -> (component, needed for the first vertical slice)
REQUIRED = {
    "research_jobs": ("jobs / run status / attempts / result", True),
    "research_run_steps": ("execution steps per attempt", True),
    "source_documents": ("acquisition / document provenance", True),
    "evidence_chunks": ("evidence chunks", True),
    "field_claims": ("extracted claims", True),
    "claim_citations": ("claim provenance (quotes)", True),
    "run_field_states": ("field resolution / review requirement", True),
    "context_bundles": ("context bundle persistence / cache", True),
    "research_runs": ("run audit record", True),
    "run_events": ("trusted events incl. HITL decisions", True),
    "procedures": ("procedural memory", False),
    "procedure_versions": ("procedural memory", False),
    "procedure_origins": ("procedural memory", False),
    "procedure_usages": ("procedural memory", False),
    "trajectories": ("procedural memory", False),
    "distillation_jobs": ("procedural memory", False),
    **{t: ("LangGraph checkpoints (setup(), not Alembic)", True) for t in CHECKPOINT_TABLES},
}

def alembic_cfg(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg

def heading(title: str) -> None:
    print(f"\n== {title} " + "=" * max(0, 70 - len(title)))

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upgrade", action="store_true", help="alembic upgrade head if behind")
    args = parser.parse_args()
    settings = get_settings()
    engine = get_engine()
    url = engine.url.render_as_string(hide_password=False)
    problems: list[str] = []

    heading("Connection")
    print(f"DATABASE_URL: {engine.url.render_as_string(hide_password=True)}")
    try:
        with engine.connect() as conn:
            print("server:", conn.execute(text("SELECT version()")).scalar().split(",")[0])
            print("database:", conn.execute(text("SELECT current_database()")).scalar())
    except Exception as exc:  # noqa: BLE001
        print(f"NOT REACHABLE: {type(exc).__name__}: {str(exc).splitlines()[0]}")
        return 1

    heading("Alembic")
    head = ScriptDirectory.from_config(alembic_cfg(url)).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    print(f"current: {current} | head: {head}")
    if current != head:
        if args.upgrade:
            command.upgrade(alembic_cfg(url), "head")
            with engine.connect() as conn:
                current = MigrationContext.configure(conn).get_current_revision()
            print(f"upgraded -> {current}")
        else:
            problems.append(f"schema at {current}, head is {head} (rerun with --upgrade)")

    heading("Drift: live schema vs SQLAlchemy models")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={
            "include_object": include_object_for(conn.dialect.name)})
        diff = [d for d in compare_metadata(ctx, Base.metadata)
                if not any(t in str(d) for t in CHECKPOINT_TABLES + ("alembic_version",))]
    if diff:
        for d in diff:
            print("  DRIFT:", d)
        problems.append(f"{len(diff)} schema drift item(s)")
    else:
        print("none (every model table/column/index matches the migrated schema)")

    heading("LangGraph checkpointer setup()")
    async def init_checkpointer() -> str:
        async with open_checkpointer(settings, pool_size=1) as saver:
            return type(saver).__name__
    try:
        print("initialized with", run_async(init_checkpointer()))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"checkpointer setup failed: {exc}")
        print(f"FAILED: {type(exc).__name__}: {exc}")
    with engine.connect() as conn:
        version = conn.execute(text(
            "SELECT max(v) FROM checkpoint_migrations")).scalar() \
            if conn.execute(text("SELECT to_regclass('checkpoint_migrations')")).scalar() \
            else None
    print(f"checkpoint_migrations version: {version}")

    heading("Tables (system catalogs)")
    with engine.connect() as conn:
        existing = set(conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")).scalars())
        constraints: dict[str, list[str]] = {}
        for table, ctype, name, definition in conn.execute(text("""
                SELECT c.conrelid::regclass::text, c.contype, c.conname,
                       pg_get_constraintdef(c.oid)
                FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = current_schema() ORDER BY 1, 2, 3""")):
            kind = {"p": "PK", "u": "UNIQUE", "f": "FK", "c": "CHECK"}.get(ctype, ctype)
            constraints.setdefault(table, []).append(f"{kind} {definition}")
        indexes: dict[str, list[str]] = {}
        for table, name in conn.execute(text(
                "SELECT tablename, indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() ORDER BY 1, 2")):
            indexes.setdefault(table, []).append(name)
        print(f"{'table':<24}{'exists':<8}{'rows':>8}  {'slice':<6} component")
        for table, (component, needed) in REQUIRED.items():
            ok = table in existing
            rows = conn.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() if ok else "-"
            print(f"{table:<24}{'yes' if ok else 'NO':<8}{rows!s:>8}  "
                  f"{'yes' if needed else 'no':<6} {component}")
            if not ok and needed:
                problems.append(f"missing table {table}")
        print()
        for table in REQUIRED:
            if table in existing:
                print(f"{table}:")
                for c in constraints.get(table, []):
                    print(f"    {c}")
                print(f"    indexes: {', '.join(indexes.get(table, []))}")

    heading("Write/read round trip (isolated rows, rolled back)")
    run_id = f"readiness-{uuid4().hex[:12]}"
    now = datetime.now(UTC)
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.execute(text(
                    "INSERT INTO research_jobs (run_id, city_name, country, city_key, status, "
                    "priority, attempts, max_attempts, available_at, cancel_requested, resumed, "
                    "created_at, updated_at, field_keys) VALUES (:r, 'Readiness City', NULL, "
                    ":k, 'CANCELLED', 0, 0, 1, :n, false, false, :n, :n, "
                    "CAST(:f AS JSONB))"),
                    {"r": run_id, "k": run_id, "n": now, "f": '["utility_name"]'})
                doc_id = conn.execute(text(
                    "INSERT INTO source_documents (run_id, round, requested_url, canonical_url,"
                    " status, chunk_count, created_at) VALUES (:r, 1, 'https://example.org/x',"
                    " 'https://example.org/x', 'OK', 1, :n) RETURNING id"),
                    {"r": run_id, "n": now}).scalar()
                conn.execute(text(
                    "INSERT INTO evidence_chunks (run_id, chunk_id, document_id, url, ordinal,"
                    " text, char_count, created_at) VALUES (:r, 'c1', :d, "
                    "'https://example.org/x', 0, 'readiness text', 14, :n)"),
                    {"r": run_id, "d": doc_id, "n": now})
                claim_id = conn.execute(text(
                    "INSERT INTO field_claims (run_id, field_key, value, value_status, "
                    "value_origin, confidence, is_selected, created_at) VALUES (:r, "
                    "'utility_name', CAST('\"X\"' AS JSONB), 'KNOWN', 'REPORTED', 0.5, false,"
                    " :n) RETURNING id"), {"r": run_id, "n": now}).scalar()
                conn.execute(text(
                    "INSERT INTO claim_citations (claim_id, run_id, document_id, chunk_id, url,"
                    " quote) VALUES (:c, :r, :d, 'c1', 'https://example.org/x', 'readiness')"),
                    {"c": claim_id, "r": run_id, "d": doc_id})
                conn.execute(text(
                    "INSERT INTO run_field_states (run_id, field_key, status, review_required,"
                    " needs_evidence, selected_claim_id, updated_at) VALUES (:r, "
                    "'utility_name', 'CANDIDATE', true, false, NULL, :n)"),
                    {"r": run_id, "n": now})
                got = conn.execute(text(
                    "SELECT j.field_keys, d.status, e.text, c.value, q.quote, s.status "
                    "FROM research_jobs j JOIN source_documents d ON d.run_id = j.run_id "
                    "JOIN evidence_chunks e ON e.document_id = d.id "
                    "JOIN field_claims c ON c.run_id = j.run_id "
                    "JOIN claim_citations q ON q.claim_id = c.id "
                    "JOIN run_field_states s ON s.run_id = j.run_id WHERE j.run_id = :r"),
                    {"r": run_id}).one()
                print("read back:", tuple(got))
                # Foreign keys are enforced: a chunk for a nonexistent document must fail.
                sp = conn.begin_nested()
                try:
                    conn.execute(text(
                        "INSERT INTO evidence_chunks (run_id, chunk_id, document_id, url, "
                        "ordinal, text, char_count, created_at) VALUES (:r, 'c2', -1, 'u', 0, "
                        "'t', 1, :n)"), {"r": run_id, "n": now})
                    problems.append("FK evidence_chunks.document_id not enforced")
                    sp.rollback()
                except IntegrityError:
                    sp.rollback()
                    print("FK evidence_chunks.document_id -> source_documents.id: enforced")
                # Unique (run_id, chunk_id) enforced.
                sp = conn.begin_nested()
                try:
                    conn.execute(text(
                        "INSERT INTO evidence_chunks (run_id, chunk_id, document_id, url, "
                        "ordinal, text, char_count, created_at) VALUES (:r, 'c1', :d, 'u', 1, "
                        "'t', 1, :n)"), {"r": run_id, "d": doc_id, "n": now})
                    problems.append("UNIQUE (run_id, chunk_id) not enforced")
                    sp.rollback()
                except IntegrityError:
                    sp.rollback()
                    print("UNIQUE evidence_chunks(run_id, chunk_id): enforced")
            finally:
                tx.rollback()
        with engine.connect() as conn:
            left = conn.execute(text("SELECT count(*) FROM research_jobs WHERE run_id = :r"),
                                {"r": run_id}).scalar()
        print(f"rolled back: {left == 0} (rows left for {run_id}: {left})")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"round trip failed: {type(exc).__name__}: {exc}")
        print(f"FAILED: {type(exc).__name__}: {str(exc)[:300]}")

    heading("Result")
    if problems:
        for p in problems:
            print("  PROBLEM:", p)
        return 1
    print("READY: all required tables, constraints and the checkpointer are in place.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
