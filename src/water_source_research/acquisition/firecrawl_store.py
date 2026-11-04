"""Per-run Firecrawl attempt ledger (table ``firecrawl_attempts``): the conservative credit
budget that survives restarts and concurrent callers.

- reserve() runs in one transaction under a per-run lock (Postgres advisory transaction
  lock; a process lock on other backends): it refuses a URL already attempted in the run (in
  any state, so a restarted worker never repeats a request that may have been billed) and a
  reservation that would take the run past its cap, else writes a PENDING row.
- settle() records the outcome. A definite answer settles to its estimate; an uncertain one
  (timeout, dropped connection, a process that died mid-request) stays UNCERTAIN with its
  reservation, never released to zero.
- Budget used = Σ SETTLED estimates + Σ PENDING/UNCERTAIN reservations.
"""

import hashlib
import threading
from datetime import UTC, datetime
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import FirecrawlAttemptRecord as A

PENDING, SETTLED, UNCERTAIN = "PENDING", "SETTLED", "UNCERTAIN"
_LOCK = threading.Lock()

def _lock_key(run_id: str) -> int:
    return int.from_bytes(hashlib.sha1(f"firecrawl:{run_id}".encode()).digest()[:8],
                          "big", signed=True)

class FirecrawlAttemptStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _locked(self, session: Session, run_id: str) -> None:
        if self.engine.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(run_id)})

    @staticmethod
    def _used(session: Session, run_id: str) -> float:
        settled = session.scalar(select(func.coalesce(func.sum(A.credits_estimated), 0)).where(
            A.run_id == run_id, A.state == SETTLED)) or 0
        held = session.scalar(select(func.coalesce(func.sum(A.credits_reserved), 0)).where(
            A.run_id == run_id, A.state != SETTLED)) or 0
        return float(settled) + float(held)

    def used(self, run_id: str) -> float:
        with Session(self.engine) as s:
            return self._used(s, run_id)

    def attempted(self, run_id: str) -> set[str]:
        with Session(self.engine) as s:
            return set(s.scalars(select(A.canonical_url).where(A.run_id == run_id)))

    def recover_pending(self, run_id: str) -> int:
        """PENDING rows of this run were left by a process that died mid-request (one worker
        holds a run at a time): they become UNCERTAIN, keeping their reservation."""
        with Session(self.engine) as s, s.begin():
            r = s.execute(update(A).where(A.run_id == run_id, A.state == PENDING).values(
                state=UNCERTAIN, outcome="PROCESS_ENDED", finished_at=datetime.now(UTC)))
            return r.rowcount or 0

    def reserve(self, run_id: str, canonical_url: str, round_no: int, credits: float,
                cap: float) -> tuple[int | None, str, float]:
        """(attempt id, "RESERVED", used after) or (None, ALREADY_ATTEMPTED | BUDGET, used)."""
        with _LOCK, Session(self.engine) as s:
            try:
                with s.begin():
                    self._locked(s, run_id)
                    if s.scalar(select(A.id).where(A.run_id == run_id,
                                                   A.canonical_url == canonical_url)):
                        return None, "ALREADY_ATTEMPTED", self._used(s, run_id)
                    used = self._used(s, run_id)
                    if used + credits > cap:
                        return None, "BUDGET", used
                    row = A(run_id=run_id, canonical_url=canonical_url, round=round_no,
                            state=PENDING, credits_reserved=credits,
                            started_at=datetime.now(UTC))
                    s.add(row)
                    s.flush()
                    return row.id, "RESERVED", used + credits
            except IntegrityError:  # another process reserved the same URL first
                return None, "ALREADY_ATTEMPTED", self.used(run_id)

    def settle(self, attempt_id: int, *, uncertain: bool, estimated: float | None,
               outcome: str, error_kind: str | None = None) -> None:
        with Session(self.engine) as s, s.begin():
            row = s.get(A, attempt_id)
            if row is None:
                return
            row.finished_at = datetime.now(UTC)
            row.outcome = outcome[:32]
            row.error_kind = (error_kind or None) and error_kind[:32]
            if uncertain or estimated is None:
                row.state = UNCERTAIN  # may have been billed: keep the reservation
                row.credits_estimated = None
            else:
                row.state = SETTLED
                row.credits_estimated = float(estimated)
