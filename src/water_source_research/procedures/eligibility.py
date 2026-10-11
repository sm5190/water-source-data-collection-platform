"""Which trajectories may be distilled. Isolated so the policy is testable and configurable.

completed run? -> enough resolved (accepted) fields? -> unresolved conflict? -> evidence-backed?
-> any successful research action to learn from?
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from water_source_research.schemas.events import EventKind, EventResult
from water_source_research.schemas.procedures import Trajectory
from water_source_research.schemas.research import FieldDefinition
from .config import ProcedureMemorySettings

@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    auto_promotable: bool = False
    reasons: list[str] = field(default_factory=list)

class EligibilityPolicy:
    def __init__(self, settings: ProcedureMemorySettings,
                 field_definitions: Mapping[str, FieldDefinition]) -> None:
        self.s = settings
        self.fields = field_definitions

    def evaluate(self, t: Trajectory) -> EligibilityDecision:
        if t.outcome != "COMPLETED":
            return _no(f"run outcome {t.outcome}, not COMPLETED")

        extractable = [k for k in t.field_keys if not self.fields[k].derived]
        if not extractable:
            return _no("field type has no extractable fields")
        resolved = [k for k in extractable if t.field_statuses.get(k) == "RESOLVED"]
        ratio = len(resolved) / len(extractable)
        if not resolved or ratio < self.s.eligibility_min_resolved_ratio:
            return _no(f"resolved ratio {ratio:.2f} below "
                       f"{self.s.eligibility_min_resolved_ratio:.2f}")

        reasons: list[str] = [f"{len(resolved)}/{len(extractable)} fields accepted"]
        auto_promotable = True
        conflicts = [k for k in extractable if t.field_statuses.get(k) == "CONFLICT"]
        if conflicts:
            if not self.s.eligibility_allow_conflicted:
                return _no(f"unresolved conflict in {conflicts}")
            auto_promotable = False
            reasons.append(f"unresolved conflict in {conflicts}: never auto-promoted")

        if self.s.eligibility_require_evidence:
            unsupported = [k for k in resolved
                           if not any(e.get("url") for e in t.accepted_evidence.get(k, []))]
            if unsupported:
                return _no(f"accepted values without accepted evidence: {unsupported}")

        if not any(s.kind == EventKind.TOOL_CALL and s.result == EventResult.SUCCESS
                   and s.field_key in t.field_keys for s in t.steps):
            return _no("no successful research action to learn from")

        if t.quality.human_correction_count:
            reasons.append(f"{t.quality.human_correction_count} human correction(s) "
                           "recorded against this trajectory")
        return EligibilityDecision(True, auto_promotable, reasons)

def _no(reason: str) -> EligibilityDecision:
    return EligibilityDecision(False, False, [reason])
