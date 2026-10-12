"""Postgres-backed queue of city runs. All coordination happens in the database.

- submit: idempotent per city — a partial unique index allows one QUEUED/RUNNING job per
  normalized (city, country); a duplicate submission returns the active job.
- claim: ``SELECT … FOR UPDATE SKIP LOCKED`` picks a job without workers blocking each other,
  then a conditional UPDATE takes a time-limited lease. Expired leases are reclaimable.
- heartbeat / complete / fail / release are fenced by ``lease_owner``: a worker that lost its
  lease (e.g. stalled past the deadline) can no longer change the job.
- fail retries with exponential backoff until ``max_attempts``, then FAILED.

Lease timing uses the application clock, so worker hosts need synchronized clocks (NTP).
"""

from __future__ import annotations  # the class defines a `list` method

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4
from sqlalchemy import Engine, and_, case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from water_source_research.db.models import utcnow
from water_source_research.db.research_models import ResearchJobRecord, ResearchRunStepRecord
from water_source_research.observability.otel import get_tracer
from . import telemetry
from .config import ResearchQueueSettings

J = ResearchJobRecord
_CLAIM_CANDIDATES = 8
_GLOBAL_CAP_LOCK = 0x5754_5251  # arbitrary advisory-lock key for the global running cap

class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

ACTIVE = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)

@dataclass(frozen=True, slots=True)
class Job:
    run_id: str
    city_name: str
    country: str | None
    status: JobStatus
    priority: int
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_until: datetime | None
    cancel_requested: bool
    resumed: bool
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    field_keys: list[str] | None = None  # field subset for this run; None = every field

    def public(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "city_name": self.city_name, "country": self.country,
                "status": self.status.value, "attempts": self.attempts,
                "max_attempts": self.max_attempts, "cancel_requested": self.cancel_requested,
                "resumed": self.resumed, "last_error": self.last_error,
                "created_at": _iso(self.created_at), "started_at": _iso(self.started_at),
                "finished_at": _iso(self.finished_at), "field_keys": self.field_keys}

@dataclass(frozen=True, slots=True)
class SubmitResult:
    job: Job
    created: bool  # False: an active run for this city already existed and was returned

@dataclass(frozen=True, slots=True)
class Heartbeat:
    lease_held: bool
    cancel_requested: bool = False

def city_key(city_name: str, country: str | None) -> str:
    def norm(value: str | None) -> str:
        return " ".join((value or "").casefold().split())
    return f"{norm(city_name)}|{norm(country)}"

class ResearchQueue:
    def __init__(self, engine: Engine, settings: ResearchQueueSettings,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.engine = engine
        self.s = settings
        self.clock = clock

    # --- producers ----------------------------------------------------------------------

    def submit(self, city_name: str, country: str | None = None, priority: int = 0,
               field_keys: list[str] | None = None) -> SubmitResult:
        """Enqueue a city run (``field_keys``: optional, already-validated field subset)."""
        key = city_key(city_name, country)
        with get_tracer().start_as_current_span("research_queue.submit") as span:
            existing = self._active_for(key)
            if existing is None:
                now = self.clock()
                run_id = str(uuid4())
                try:
                    with Session(self.engine) as session, session.begin():
                        session.add(J(run_id=run_id, city_name=city_name, country=country,
                                      city_key=key, status=JobStatus.QUEUED.value,
                                      priority=priority, max_attempts=self.s.max_attempts,
                                      available_at=now, created_at=now, updated_at=now,
                                      field_keys=list(field_keys) if field_keys else None))
                    job = self.get(run_id)
                    assert job is not None
                    result = SubmitResult(job, True)
                except IntegrityError:
                    # A concurrent submission for the same city won the unique index.
                    existing = self._active_for(key)
                    if existing is None:
                        raise
                    result = SubmitResult(existing, False)
            else:
                result = SubmitResult(existing, False)
            span.set_attribute("research.run_id", result.job.run_id)
            span.set_attribute("research_queue.created", result.created)
            telemetry.jobs_submitted.add(1, {"created": str(result.created).lower()})
            return result

    def cancel(self, run_id: str) -> Job | None:
        """QUEUED -> CANCELLED immediately; RUNNING -> cancel requested (worker stops it)."""
        now = self.clock()
        with Session(self.engine) as session, session.begin():
            session.execute(update(J).where(J.run_id == run_id,
                                            J.status == JobStatus.QUEUED.value)
                            .values(status=JobStatus.CANCELLED.value, finished_at=now,
                                    updated_at=now))
            session.execute(update(J).where(J.run_id == run_id,
                                            J.status == JobStatus.RUNNING.value)
                            .values(cancel_requested=True, updated_at=now))
        return self.get(run_id)

    # --- workers ------------------------------------------------------------------------

    def claim(self, worker_id: str) -> Job | None:
        now = self.clock()
        claimable = and_(
            J.attempts < J.max_attempts,
            or_(and_(J.status == JobStatus.QUEUED.value, J.available_at <= now),
                and_(J.status == JobStatus.RUNNING.value, J.lease_until < now)),
        )
        with Session(self.engine) as session, session.begin():
            if self.s.max_running_global is not None:
                if self.engine.dialect.name == "postgresql":
                    # Serialize cap checks so two workers can't both take the last slot.
                    session.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                                    {"k": _GLOBAL_CAP_LOCK})
                running = session.scalar(
                    select(func.count()).select_from(J)
                    .where(J.status == JobStatus.RUNNING.value, J.lease_until >= now)) or 0
                if running >= self.s.max_running_global:
                    return None
            # Candidates in priority order. SKIP LOCKED makes concurrent workers pick different
            # rows on Postgres; elsewhere the conditional UPDATE below is the arbiter and a
            # lost race just moves on to the next candidate (contention != empty queue).
            candidates = session.scalars(
                select(J.run_id).where(claimable)
                .order_by(J.priority.desc(), J.created_at, J.run_id)
                .limit(_CLAIM_CANDIDATES).with_for_update(skip_locked=True)).all()
            run_id = None
            for candidate in candidates:
                n = session.execute(
                    update(J).where(J.run_id == candidate, claimable).values(
                        status=JobStatus.RUNNING.value, lease_owner=worker_id,
                        lease_until=now + timedelta(seconds=self.s.lease_seconds),
                        heartbeat_at=now, attempts=J.attempts + 1, cancel_requested=False,
                        started_at=func.coalesce(J.started_at, now), updated_at=now)
                ).rowcount
                if n:
                    run_id = candidate
                    break
            if run_id is None:
                return None
            row = session.execute(select(J).where(J.run_id == run_id)).scalar_one()
            job = _job(row)
        telemetry.jobs_claimed.add(1)
        telemetry.queue_wait_seconds.record(
            max(0.0, (_aware(now) - _aware(job.created_at)).total_seconds()))
        return job

    def heartbeat(self, run_id: str, worker_id: str) -> Heartbeat:
        now = self.clock()
        with Session(self.engine) as session, session.begin():
            n = session.execute(
                update(J).where(*self._owned(run_id, worker_id)).values(
                    lease_until=now + timedelta(seconds=self.s.lease_seconds),
                    heartbeat_at=now, updated_at=now)
            ).rowcount
            if not n:
                return Heartbeat(False)
            cancel = session.scalar(select(J.cancel_requested).where(J.run_id == run_id))
            return Heartbeat(True, bool(cancel))

    def complete(self, run_id: str, worker_id: str, result: dict[str, Any],
                 resumed: bool) -> bool:
        now = self.clock()
        with Session(self.engine) as session, session.begin():
            n = session.execute(update(J).where(*self._owned(run_id, worker_id)).values(
                status=JobStatus.SUCCEEDED.value, result=result, resumed=resumed,
                lease_owner=None, lease_until=None, finished_at=now, updated_at=now,
                last_error=None)).rowcount
        if n:
            telemetry.jobs_finished.add(1, {"status": JobStatus.SUCCEEDED.value})
        return bool(n)

    def fail(self, run_id: str, worker_id: str, error: str,
             retryable: bool = True) -> JobStatus | None:
        """Retry with exponential backoff, or FAILED once attempts are exhausted.

        Returns the new status, or None if this worker no longer holds the lease.
        """
        now = self.clock()
        with Session(self.engine) as session, session.begin():
            attempts = session.scalar(select(J.attempts).where(*self._owned(run_id, worker_id)))
            if attempts is None:
                return None
            max_attempts = session.scalar(select(J.max_attempts).where(J.run_id == run_id))
            if retryable and attempts < max_attempts:
                status = JobStatus.QUEUED
                values = {"available_at": now + timedelta(seconds=self.backoff(attempts))}
            else:
                status = JobStatus.FAILED
                values = {"finished_at": now}
            session.execute(update(J).where(*self._owned(run_id, worker_id)).values(
                status=status.value, last_error=error[:2000], lease_owner=None,
                lease_until=None, updated_at=now, **values))
        telemetry.jobs_finished.add(1, {"status": status.value})
        return status

    def mark_cancelled(self, run_id: str, worker_id: str) -> bool:
        now = self.clock()
        with Session(self.engine) as session, session.begin():
            n = session.execute(update(J).where(*self._owned(run_id, worker_id)).values(
                status=JobStatus.CANCELLED.value, lease_owner=None, lease_until=None,
                finished_at=now, updated_at=now)).rowcount
        if n:
            telemetry.jobs_finished.add(1, {"status": JobStatus.CANCELLED.value})
        return bool(n)

    def release(self, run_id: str, worker_id: str) -> bool:
        """Graceful shutdown: hand the job back without consuming an attempt."""
        now = self.clock()
        with Session(self.engine) as session, session.begin():
            n = session.execute(update(J).where(*self._owned(run_id, worker_id)).values(
                status=JobStatus.QUEUED.value, lease_owner=None, lease_until=None,
                available_at=now, updated_at=now,
                attempts=case((J.attempts > 0, J.attempts - 1), else_=0))).rowcount
        return bool(n)

    def reap_expired(self) -> int:
        """Fail jobs whose lease expired after their last allowed attempt."""
        now = self.clock()
        with Session(self.engine) as session, session.begin():
            n = session.execute(update(J).where(
                J.status == JobStatus.RUNNING.value, J.lease_until < now,
                J.attempts >= J.max_attempts,
            ).values(status=JobStatus.FAILED.value, lease_owner=None, lease_until=None,
                     finished_at=now, updated_at=now,
                     last_error="lease expired on final attempt (worker crashed or stalled)")
            ).rowcount or 0
        if n:
            telemetry.jobs_finished.add(n, {"status": JobStatus.FAILED.value})
        return n

    def backoff(self, attempts: int) -> float:
        return min(self.s.backoff_max_seconds,
                   self.s.backoff_base_seconds * 2 ** max(0, attempts - 1))

    # --- reads --------------------------------------------------------------------------

    def get(self, run_id: str) -> Job | None:
        with Session(self.engine) as session:
            row = session.get(J, run_id)
            return _job(row) if row else None

    def result(self, run_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            return session.scalar(select(J.result).where(J.run_id == run_id))

    def list(self, status: JobStatus | None = None, limit: int = 100,
             city_name: str | None = None, country: str | None = None) -> list[Job]:
        stmt = select(J).order_by(J.created_at.desc(), J.run_id).limit(limit)
        if status:
            stmt = stmt.where(J.status == status.value)
        if city_name:
            stmt = stmt.where(J.city_key == city_key(city_name, country))
        with Session(self.engine) as session:
            return [_job(r) for r in session.scalars(stmt)]

    def record_step(self, run_id: str, worker_id: str, attempt: int, seq: int, node: str,
                    duration_ms: float, summary: dict[str, Any]) -> bool:
        """Store one completed graph step (fenced: only the lease holder may write)."""
        with Session(self.engine) as session, session.begin():
            owned = session.scalar(select(func.count()).select_from(J)
                                   .where(*self._owned(run_id, worker_id)))
            if not owned:
                return False
            try:
                with session.begin_nested():
                    session.add(ResearchRunStepRecord(
                        run_id=run_id, attempt=attempt, seq=seq, node=node,
                        duration_ms=duration_ms, summary=summary,
                        finished_at=self.clock()))
            except IntegrityError:
                return False  # already recorded for this attempt
        return True

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        S = ResearchRunStepRecord
        with Session(self.engine) as session:
            rows = session.scalars(select(S).where(S.run_id == run_id)
                                   .order_by(S.attempt, S.seq)).all()
            return [{"attempt": r.attempt, "seq": r.seq, "node": r.node,
                     "duration_ms": r.duration_ms, "finished_at": _iso(r.finished_at),
                     "summary": r.summary} for r in rows]

    def stats(self) -> dict[str, Any]:
        now = self.clock()
        with Session(self.engine) as session:
            counts = dict(session.execute(select(J.status, func.count()).group_by(J.status)).all())
            oldest = session.scalar(select(func.min(J.created_at))
                                    .where(J.status == JobStatus.QUEUED.value))
        return {"by_status": counts,
                "oldest_queued_age_seconds":
                    (_aware(now) - _aware(oldest)).total_seconds() if oldest else None}

    # --- helpers ------------------------------------------------------------------------

    def _active_for(self, key: str) -> Job | None:
        with Session(self.engine) as session:
            row = session.scalars(select(J).where(J.city_key == key, J.status.in_(ACTIVE))).first()
            return _job(row) if row else None

    @staticmethod
    def _owned(run_id: str, worker_id: str):
        return (J.run_id == run_id, J.lease_owner == worker_id,
                J.status == JobStatus.RUNNING.value)

def _job(r: ResearchJobRecord) -> Job:
    return Job(run_id=r.run_id, city_name=r.city_name, country=r.country,
               status=JobStatus(r.status), priority=r.priority, attempts=r.attempts,
               max_attempts=r.max_attempts, lease_owner=r.lease_owner,
               lease_until=r.lease_until, cancel_requested=r.cancel_requested,
               resumed=r.resumed, last_error=r.last_error, created_at=r.created_at,
               started_at=r.started_at, finished_at=r.finished_at,
               field_keys=list(r.field_keys) if r.field_keys else None)

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt else None
