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
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()
        yield saver
    finally:
        await pool.close()

def run_async[T](coro: Awaitable[T]) -> T:
    """asyncio.run with a selector loop on Windows (required by psycopg async)."""
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]
    return asyncio.run(coro)  # type: ignore[arg-type]
