"""Graph checkpointer selection.

Postgres (``langgraph-checkpoint-postgres``) persists every graph step under
thread_id = run_id, so a retried run resumes after a worker crash — even on another host.
LangGraph manages its own checkpoint tables via ``setup()`` (not Alembic).
The in-memory saver only resumes within one process (dev/tests).

Windows: psycopg's async driver needs a selector event loop; ``run_async`` handles it.
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.engine import make_url
from water_source_research.core.config import Settings

def uses_postgres(settings: Settings) -> bool:
    mode = settings.research_queue.checkpointer
    if mode == "memory":
        return False
    if mode == "postgres":
        return True
    return make_url(settings.database_url).get_backend_name() == "postgresql"

def psycopg_conninfo(database_url: str) -> str:
    """SQLAlchemy URL (postgresql+psycopg://…) -> libpq URL for psycopg."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False)

@asynccontextmanager
async def open_checkpointer(settings: Settings,
                            pool_size: int | None = None) -> AsyncIterator[BaseCheckpointSaver]:
    if not uses_postgres(settings):
        yield InMemorySaver()
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    size = pool_size or settings.research_queue.worker_concurrency + 2
    pool = AsyncConnectionPool(
        psycopg_conninfo(settings.database_url), min_size=1, max_size=size, open=False,
        # Validate a connection before handing it out: after the host sleeps (or the
        # database restarts) pooled connections are dead.
        check=AsyncConnectionPool.check_connection,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()
        yield saver
    finally:
        await pool.close()

class CheckpointStats:
    """Serialized bytes the checkpointer writes (diagnostics: checkpoint growth per step)."""

    def __init__(self) -> None:
        self.writes = 0
        self.last_bytes = 0
        self.peak_bytes = 0
        self.total_bytes = 0
        self.errors = 0

    def add(self, size: int) -> None:
        self.writes += 1
        self.last_bytes = size
        self.peak_bytes = max(self.peak_bytes, size)
        self.total_bytes += size

    def snapshot(self) -> dict[str, int]:
        return {"checkpoint_writes": self.writes, "checkpoint_last_bytes": self.last_bytes,
                "checkpoint_peak_bytes": self.peak_bytes,
                "checkpoint_total_bytes": self.total_bytes}

def measure_checkpoint_writes(saver: BaseCheckpointSaver) -> CheckpointStats:
    """Count the serialized size of what ``saver`` writes, without changing what it does.

    Wraps the instance's ``aput`` (channel values with new versions) and ``aput_writes``
    (pending writes); sizes come from the saver's own serializer. Measuring never fails a
    write: errors are counted and the write proceeds."""
    stats = CheckpointStats()
    serde = saver.serde
    original_put, original_writes = saver.aput, saver.aput_writes

    def size_of(value: object) -> int:
        try:
            return len(serde.dumps_typed(value)[1])
        except Exception:  # noqa: BLE001
            stats.errors += 1
            return 0

    async def aput(config, checkpoint, metadata, new_versions):
        values = checkpoint.get("channel_values") or {}
        stats.add(sum(size_of(values[k]) for k in new_versions if k in values))
        return await original_put(config, checkpoint, metadata, new_versions)

    async def aput_writes(config, writes, task_id, task_path=""):
        stats.add(sum(size_of(v) for _, v in writes))
        return await original_writes(config, writes, task_id, task_path)

    saver.aput = aput  # type: ignore[method-assign]
    saver.aput_writes = aput_writes  # type: ignore[method-assign]
    return stats

def run_async[T](coro: Awaitable[T]) -> T:
    """asyncio.run with a selector loop on Windows (required by psycopg async)."""
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]
    return asyncio.run(coro)  # type: ignore[arg-type]
