"""The API usage ledger writer (``provider_usage``): one row per logical external call.

The LLM router reports every finished call here (``record``), and a background thread writes
rows in batches, so a call never waits on the database and never fails because of it. The
queue is bounded: when it is full the row is dropped and counted (``stats()``), never
blocking. ``flush`` writes what is queued, bounded in time (called at process exit).

What the ledger does NOT have: rows queued in a process that is killed (at most the last
``flush_seconds`` of calls), and calls of processes started without ``configure_usage``.
Unknown measures stay NULL: tokens the provider didn't report, and cost for a model without a
price in ``config/llm_prices.yaml``.
"""

import atexit
import logging
import queue
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from sqlalchemy import Engine
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

COLUMNS = ("run_id", "category", "kind", "provider", "model", "task", "field_key",
           "prompt_version", "requests", "retries", "cache_hit", "attempt_errors", "error",
           "input_tokens", "output_tokens", "cached_tokens", "credits", "latency_ms",
           "cost_usd", "cost_basis", "trace_id", "created_at")

class UsageRecorder:
    def __init__(self, engine: Engine, max_queue: int = 10_000, batch_size: int = 200,
                 flush_seconds: float = 1.0) -> None:
        self.engine = engine
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)
        self.batch_size = batch_size
        self.flush_seconds = flush_seconds
        self.dropped = 0
        self.written = 0
        self.failed_batches = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="usage-ledger", daemon=True)
        self._thread.start()

    def record(self, row: Mapping[str, Any]) -> None:
        clean = {k: row.get(k) for k in COLUMNS}
        clean["created_at"] = clean.get("created_at") or datetime.now(UTC)
        try:
            self.queue.put_nowait(clean)
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            self._drain(wait=self.flush_seconds)

    def _drain(self, wait: float) -> None:
        batch: list[dict[str, Any]] = []
        try:
            batch.append(self.queue.get(timeout=wait))
        except queue.Empty:
            return
        while len(batch) < self.batch_size:
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        self._write(batch)

    def _write(self, batch: list[dict[str, Any]]) -> None:
        from water_source_research.db.observability_models import ProviderUsageRecord as U
        try:
            with Session(self.engine) as session, session.begin():
                session.add_all(U(**r) for r in batch)
            self.written += len(batch)
        except Exception as exc:  # noqa: BLE001 - the ledger never fails a call
            self.failed_batches += 1
            self.dropped += len(batch)
            log.warning("usage ledger: %d row(s) not written (%s)", len(batch),
                        type(exc).__name__)

    def flush(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not self.queue.empty() and time.monotonic() < deadline:
            self._drain(wait=0.01)

    def close(self, timeout: float = 5.0) -> None:
        self.flush(timeout)
        self._stop.set()
        self._thread.join(timeout=1.0)

    def stats(self) -> dict[str, int]:
        return {"usage_rows_written": self.written, "usage_rows_dropped": self.dropped,
                "usage_rows_queued": self.queue.qsize(),
                "usage_failed_batches": self.failed_batches}

_recorder: UsageRecorder | None = None
_lock = threading.Lock()

def configure_usage(engine: Engine | None, settings: Any = None) -> UsageRecorder | None:
    """Start recording this process's calls (once; ``OBSERVABILITY__USAGE_LEDGER``)."""
    global _recorder
    if engine is None:
        return None
    if settings is None:
        from water_source_research.core.config import get_settings
        settings = get_settings()
    if not settings.observability.usage_ledger:
        return None
    with _lock:
        if _recorder is None:
            _recorder = UsageRecorder(engine)
            atexit.register(_recorder.close)
        return _recorder

def recorder() -> UsageRecorder | None:
    return _recorder

def set_recorder(r: UsageRecorder | None) -> None:  # tests
    global _recorder
    _recorder = r

def record_usage(row: Mapping[str, Any]) -> None:
    r = _recorder
    if r is not None:
        try:
            r.record(row)
        except Exception:
            log.debug("usage not recorded", exc_info=True)
