"""Worker lifecycle and crash events: local append-only files first, Postgres second.

Every event is written to the emitting process's own JSONL file (``<log_dir>/events/
<writer>.jsonl``: one writer per file, so lines never interleave), flushed and fsynced, and
only then offered to Postgres. When Postgres is unavailable the line stays in the file and
``sync_pending`` (run periodically by the supervisor) inserts it later. Inserts are
insert-or-skip by ``event_id``, so re-reading any file any number of times is safe.

Sync progress is a per-file byte offset in ``sync-state.json`` (replaced atomically), advanced
only after the database commit. Event files rotate at ``events_max_mb`` into dated files that
are kept; a rotated file is deleted only when it is fully synced and the directory exceeds
``events_retain_mb``, and every deletion is itself an event. Unsynced evidence is never
deleted.
"""

import json
import logging
import os
import socket
import tempfile
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

class EventKind(StrEnum):
    WORKER_STARTED = "WORKER_STARTED"
    WORKER_STOPPED = "WORKER_STOPPED"
    WORKER_EXITED_UNEXPECTEDLY = "WORKER_EXITED_UNEXPECTEDLY"
    JOB_INTERRUPTED = "JOB_INTERRUPTED"  # one per run active in a worker that died
    HEARTBEAT_MISSING = "HEARTBEAT_MISSING"
    HEARTBEAT_RESTORED = "HEARTBEAT_RESTORED"
    RESTART_SCHEDULED = "RESTART_SCHEDULED"
    WORKER_RESTARTED = "WORKER_RESTARTED"
    RESTART_FAILED = "RESTART_FAILED"
    JOB_LEASE_EXPIRED = "JOB_LEASE_EXPIRED"
    JOB_RECLAIMED = "JOB_RECLAIMED"
    CHECKPOINT_RECOVERY_ATTEMPTED = "CHECKPOINT_RECOVERY_ATTEMPTED"
    JOB_RESUMED = "JOB_RESUMED"
    JOB_RECOVERY_FAILED = "JOB_RECOVERY_FAILED"
    SUPERVISOR_STARTED = "SUPERVISOR_STARTED"
    SUPERVISOR_STOPPED = "SUPERVISOR_STOPPED"
    EVENT_FILES_PRUNED = "EVENT_FILES_PRUNED"

def now_utc() -> datetime:
    return datetime.now(UTC)

@dataclass
class WorkerEvent:
    kind: str
    source: str  # supervisor | worker
    instance_id: str | None = None
    supervisor_id: str | None = None
    pid: int | None = None
    run_id: str | None = None
    attempt: int | None = None
    trace_id: str | None = None
    span_id: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)  # observed
    inferred_cause: str | None = None  # interpretation, kept apart from the facts
    cause_confidence: str | None = None
    rationale: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: now_utc().isoformat())

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkerEvent":
        names = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in names})

def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON so readers see the old or the new file, never a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):  # Windows: a reader may hold the target open briefly
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                threading.Event().wait(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

class EventLog:
    """This process's append-only event file (one writer per file)."""

    def __init__(self, events_dir: Path, writer: str, max_bytes: int = 20 * 2**20) -> None:
        self.dir = events_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.writer = safe_name(writer)
        self.path = self.dir / f"{self.writer}.jsonl"
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def append(self, event: WorkerEvent) -> None:
        line = event.to_json() + "\n"
        with self._lock:
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def _rotate_if_needed(self) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size < self.max_bytes:
            return
        stamp = now_utc().strftime("%Y%m%dT%H%M%S%fZ")
        # unique suffix: two rotations within the clock resolution must never collide
        self.path.rename(self.dir / f"{self.writer}{ROTATED}{stamp}-{uuid.uuid4().hex[:6]}.jsonl")

ROTATED = ".rot-"  # <writer>.rot-<stamp>.jsonl
IDLE_PRUNABLE_SECONDS = 7 * 86400  # a live file untouched this long belongs to a gone writer

def safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120]

class EventStore:
    """Postgres side: insert-or-skip events, keep worker_instances current."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert(self, events: Iterable[WorkerEvent]) -> int:
        from water_source_research.db.observability_models import WorkerEventRecord as R
        events = list(events)
        if not events:
            return 0
        inserted = 0
        with Session(self.engine) as session, session.begin():
            ids = [e.event_id for e in events]
            known = set(session.scalars(select(R.event_id).where(R.event_id.in_(ids))))
            for e in events:
                if e.event_id in known:
                    continue
                known.add(e.event_id)
                session.add(R(event_id=e.event_id, kind=e.kind,
                              occurred_at=_dt(e.occurred_at), source=e.source,
                              instance_id=e.instance_id, supervisor_id=e.supervisor_id,
                              pid=e.pid, run_id=e.run_id, attempt=e.attempt,
                              trace_id=e.trace_id, span_id=e.span_id, facts=e.facts or {},
                              inferred_cause=e.inferred_cause,
                              cause_confidence=e.cause_confidence,
                              rationale=(e.rationale or None) and e.rationale[:2000]))
                inserted += 1
        return inserted

    def insert_one(self, event: WorkerEvent) -> None:
        try:
            self.insert([event])
        except IntegrityError:  # a concurrent sync inserted it first
            pass

    def upsert_instance(self, instance_id: str, **values: Any) -> None:
        from water_source_research.db.observability_models import WorkerInstanceRecord as W
        values = {k: v for k, v in values.items() if v is not None}
        with Session(self.engine) as session, session.begin():
            n = session.execute(update(W).where(W.instance_id == instance_id)
                                .values(**values, updated_at=now_utc())).rowcount
            if not n:
                session.add(W(instance_id=instance_id, updated_at=now_utc(), **values))

    def leased_runs(self, instance_id: str) -> list[str]:
        from water_source_research.db.research_models import ResearchJobRecord as J
        with Session(self.engine) as session:
            return list(session.scalars(select(J.run_id).where(J.lease_owner == instance_id,
                                                               J.status == "RUNNING")))

    def events(self, *, run_id: str | None = None, instance_id: str | None = None,
               limit: int = 500) -> list[dict[str, Any]]:
        from water_source_research.db.observability_models import WorkerEventRecord as R
        q = select(R).order_by(R.occurred_at, R.event_id).limit(limit)
        if run_id:
            q = q.where(R.run_id == run_id)
        if instance_id:
            q = q.where(R.instance_id == instance_id)
        with Session(self.engine) as session:
            return [{c.name: getattr(r, c.name) for c in R.__table__.columns}
                    for r in session.scalars(q)]

def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)

class EventSink:
    """Emit = local file (always, first) + Postgres (best effort). Never raises."""

    def __init__(self, log_file: EventLog, store: EventStore | None = None) -> None:
        self.file = log_file
        self.store = store

    def emit(self, kind: str, source: str, **values: Any) -> WorkerEvent:
        event = WorkerEvent(kind=str(kind), source=source, **values)
        try:
            self.file.append(event)
        except Exception:
            log.exception("could not write event %s to %s", event.kind, self.file.path)
        if self.store is not None:
            try:
                self.store.insert_one(event)
            except Exception as exc:  # noqa: BLE001 - kept in the file; synced later
                log.warning("event %s not stored in the database yet (%s); kept in %s",
                            event.kind, type(exc).__name__, self.file.path)
        return event

    def instance(self, instance_id: str, **values: Any) -> None:
        if self.store is None:
            return
        try:
            self.store.upsert_instance(instance_id, **values)
        except Exception as exc:  # noqa: BLE001 - informational row
            log.warning("worker_instances update failed (%s)", type(exc).__name__)

class EventSync:
    """Copies every event file's unsynced lines into Postgres (idempotent, resumable)."""

    def __init__(self, events_dir: Path, store: EventStore, *, retain_bytes: int = 500 * 2**20,
                 sink: EventSink | None = None) -> None:
        self.dir = events_dir
        self.store = store
        self.state_path = events_dir / "sync-state.json"
        self.retain_bytes = retain_bytes
        self.sink = sink  # to record pruning
        self.outage = False

    def sync_pending(self) -> int:
        """Insert unsynced lines from every file. Returns lines inserted; never raises."""
        state: dict[str, int] = read_json(self.state_path) or {}
        inserted = 0
        try:
            for path in sorted(self.dir.glob("*.jsonl")):
                offset = state.get(path.name, 0)
                size = path.stat().st_size
                if size < offset:  # replaced file: start over (inserts are idempotent)
                    offset = 0
                if size == offset:
                    continue
                events, end = _read_complete_lines(path, offset)
                if events:
                    inserted += self.store.insert(events)
                state[path.name] = end
                atomic_write_json(self.state_path, state)
        except Exception as exc:  # noqa: BLE001 - outage: offsets kept, retried next time
            if not self.outage:
                log.warning("event sync paused: database unavailable (%s)", type(exc).__name__)
            self.outage = True
            return inserted
        if self.outage:
            log.info("event sync resumed")
        self.outage = False
        self._prune(state)
        return inserted

    def unsynced_files(self) -> list[str]:
        state: dict[str, int] = read_json(self.state_path) or {}
        return [p.name for p in sorted(self.dir.glob("*.jsonl"))
                if p.stat().st_size > state.get(p.name, 0)]

    def _prune(self, state: dict[str, int]) -> None:
        files = sorted(self.dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        if total <= self.retain_bytes:
            return
        removed = []
        for p in files:
            if total <= self.retain_bytes:
                break
            closed = ROTATED in p.name or (
                now_utc().timestamp() - p.stat().st_mtime > IDLE_PRUNABLE_SECONDS)
            if not closed or state.get(p.name, -1) != p.stat().st_size:
                continue  # possibly still written, or not fully synced: keep
            size = p.stat().st_size
            p.unlink()
            state.pop(p.name, None)
            removed.append({"file": p.name, "bytes": size})
            total -= size
        if removed:
            atomic_write_json(self.state_path, state)
            if self.sink:
                self.sink.emit(EventKind.EVENT_FILES_PRUNED, "supervisor",
                               facts={"files": removed, "remaining_bytes": total,
                                      "reason": "fully synced to Postgres; over retention"})

def _read_complete_lines(path: Path, offset: int) -> tuple[list[WorkerEvent], int]:
    """Events from complete lines after ``offset`` (a torn last line is left for later)."""
    events: list[WorkerEvent] = []
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = offset
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            break
        end += len(raw)
        try:
            events.append(WorkerEvent.from_dict(json.loads(raw)))
        except (ValueError, TypeError):
            log.warning("skipping unreadable event line in %s", path.name)
    return events, end

def host() -> str:
    return socket.gethostname()

def build_sink(log_dir: Path, writer: str, engine: Engine | None,
               max_mb: float = 20.0) -> EventSink:
    return EventSink(EventLog(log_dir / "events", writer, int(max_mb * 2**20)),
                     EventStore(engine) if engine is not None else None)
