"""Procedure lifecycle: CANDIDATE -> (AWAITING_APPROVAL) -> ACTIVE -> DEPRECATED -> RETIRED.

Promotion needs support from several runs in several cities with a low correction rate, and
(by default) explicit human approval. Active procedures are deprecated when recently failing,
stale, or incompatible with the current workflow. Recipes themselves are never modified.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from water_source_research.core.capabilities import capabilities_for_workflow
from water_source_research.core.versions import WORKFLOW_VERSION
from water_source_research.db.models import utcnow
from water_source_research.observability.otel import get_tracer
from water_source_research.schemas.procedures import ProcedureStatus
from . import telemetry
from .config import ProcedureMemorySettings
from .repository import ProcedureRepository, VersionSnapshot

S = ProcedureStatus

@dataclass(frozen=True, slots=True)
class Transition:
    version_id: str
    from_status: str
    to_status: str
    reason: str

class LifecyclePolicy:
    def __init__(self, settings: ProcedureMemorySettings,
                 workflow_version: int = WORKFLOW_VERSION) -> None:
        self.s = settings
        self.workflow_version = workflow_version
        self.workflow_caps = {c.value for c in capabilities_for_workflow(workflow_version)}

    def evaluate(self, v: VersionSnapshot, now: datetime) -> tuple[ProcedureStatus, str] | None:
        s, status = self.s, ProcedureStatus(v.status)
        if status is S.CANDIDATE:
            if not v.auto_promotable:
                return None
            correction_rate = (v.support_corrected_runs / v.support_runs) if v.support_runs else 0
            if (v.support_runs >= s.promotion_min_support_runs
                    and v.support_cities >= s.promotion_min_support_cities
                    and correction_rate <= s.promotion_max_correction_rate):
                target = S.AWAITING_APPROVAL if s.require_human_approval else S.ACTIVE
                return target, (f"supported by {v.support_runs} runs in {v.support_cities} "
                                f"cities, correction rate {correction_rate:.0%}")
            return None
        if status is S.ACTIVE:
            if (v.min_workflow_version > self.workflow_version
                    or not set(v.required_capabilities) <= self.workflow_caps):
                return S.DEPRECATED, "incompatible with current workflow"
            outcomes = v.success_count + v.failure_count
            if (outcomes >= s.min_usage_for_deprecation and v.recent_success_rate is not None
                    and v.recent_success_rate < s.min_recent_success_rate):
                return S.DEPRECATED, f"recent success rate {v.recent_success_rate:.0%}"
            anchor = _aware(v.last_success_at or v.approved_at or v.status_changed_at)
            if _days(now, anchor) > s.stale_after_days:
                return S.DEPRECATED, "stale: no recent success"
            return None
        if status is S.DEPRECATED and _days(now, _aware(v.status_changed_at)) > s.retire_after_days:
            return S.RETIRED, "deprecated beyond retention window"
        return None

class LifecycleManager:
    def __init__(self, repo: ProcedureRepository, policy: LifecyclePolicy,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.repo = repo
        self.policy = policy
        self.clock = clock

    def apply(self, v: VersionSnapshot) -> Transition | None:
        decision = self.policy.evaluate(v, self.clock())
        if decision is None:
            return None
        target, reason = decision
        current = ProcedureStatus(v.status)
        ok = (self.repo.promote(v.id, {current}) if target is S.ACTIVE
              else self.repo.transition(v.id, {current}, target))
        if not ok:
            return None  # someone else changed it first; the next sweep re-evaluates
        telemetry.lifecycle_transitions.add(1, {"from_status": current.value,
                                                "to_status": target.value})
        return Transition(v.id, current.value, target.value, reason)

    def sweep(self) -> list[Transition]:
        with get_tracer().start_as_current_span("procedure.lifecycle_sweep") as span:
            transitions = [t for v in self.repo.lifecycle_candidates()
                           if (t := self.apply(v)) is not None]
            span.set_attribute("procedure.transition_count", len(transitions))
            return transitions

    def approve(self, version_id: str, reviewer: str) -> bool:
        ok = self.repo.promote(version_id, {S.AWAITING_APPROVAL}, approved_by=reviewer)
        if ok:
            telemetry.lifecycle_transitions.add(1, {"from_status": S.AWAITING_APPROVAL.value,
                                                    "to_status": S.ACTIVE.value})
        return ok

    def reject(self, version_id: str) -> bool:
        return self.repo.transition(version_id, {S.AWAITING_APPROVAL, S.CANDIDATE}, S.REJECTED)

    def deprecate(self, version_id: str) -> bool:
        return self.repo.transition(version_id, {S.ACTIVE, S.AWAITING_APPROVAL, S.CANDIDATE},
                                    S.DEPRECATED)

    def retire(self, version_id: str) -> bool:
        return self.repo.transition(version_id, {S.DEPRECATED}, S.RETIRED)

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

def _days(now: datetime, then: datetime) -> float:
    return (_aware(now) - then).total_seconds() / 86400
