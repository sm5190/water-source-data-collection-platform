"""Did retrieved memory actually help? retrieved != selected != applied != successful.

Outcomes are derived from trusted run events and final field state, and are applied to a
procedure version's statistics exactly once per (version, run, field).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from water_source_research.core.capabilities import DISCOVERY_CAPABILITIES
from water_source_research.schemas.events import EventKind, EventResult
from . import telemetry
from .config import ProcedureMemorySettings
from .repository import ProcedureRepository, UsageOutcome

_DISCOVERY = {c.value for c in DISCOVERY_CAPABILITIES}

def field_outcome(state: Mapping[str, Any], field_key: str) -> UsageOutcome:
    fields = (state.get("research_state") or {}).get("fields", {})
    status = (fields.get(field_key) or {}).get("status")
    events = [e for e in state.get("events", []) if e.get("field_key") == field_key]
    tool = [e for e in events if e.get("kind") == EventKind.TOOL_CALL]
    hitl = [e for e in events if e.get("kind") == EventKind.HITL]
    validations = [e for e in events if e.get("kind") == EventKind.VALIDATION]
    corrected = any(e.get("result") == EventResult.CORRECTED for e in hitl)
    resolved = status == "RESOLVED"
    costs = [e["attrs"]["cost"] for e in tool if isinstance(e.get("attrs", {}).get("cost"),
                                                             (int, float))]
    return UsageOutcome(
        success=resolved,
        field_resolved=resolved,
        validation_result=validations[-1].get("result") if validations else None,
        tool_calls=len(tool),
        search_rounds=len([e for e in tool if e.get("capability") in _DISCOVERY]),
        cost=sum(costs) if costs else None,
        latency_ms=_latency_ms(state.get("events", []), field_key),
        human_review_required=bool(hitl) or bool(state.get("needs_human_review")),
        human_corrected=corrected,
    )

class UsageTracker:
    def __init__(self, repo: ProcedureRepository, settings: ProcedureMemorySettings) -> None:
        self.repo = repo
        self.settings = settings

    def record_selection(self, version_id: str, run_id: str, field_keys: list[str],
                         adapted: bool, field_type: str) -> int:
        n = self.repo.mark_selected(version_id, run_id, field_keys, adapted)
        telemetry.procedures_selected.add(1, {"field_type": field_type,
                                              "adapted": str(adapted).lower()})
        return n

    def record_application(self, version_id: str, run_id: str, field_keys: list[str]) -> int:
        return self.repo.mark_applied(version_id, run_id, field_keys)

    def record_outcomes(self, state: Mapping[str, Any]) -> int:
        recorded = 0
        for sel in state.get("procedure_selections", []):
            if not sel.get("applied"):
                continue
            for field_key in sel["field_keys"]:
                if self.repo.record_outcome(sel["procedure_version_id"], state["run_id"],
                                            field_key, field_outcome(state, field_key),
                                            self.settings.ewma_alpha):
                    recorded += 1
        return recorded

def record_field_metrics(state: Mapping[str, Any], field_types: Mapping[str, str]) -> None:
    """Per-field outcome metrics split by strategy, so memory vs default can be compared."""
    selected: dict[str, str] = {}
    for sel in state.get("procedure_selections", []):
        if sel.get("applied"):
            for fk in sel["field_keys"]:
                selected[fk] = "procedure"
    for field_key in state.get("unresolved_fields", []):
        if field_key not in field_types:
            continue
        o = field_outcome(state, field_key)
        dims = {"field_type": field_types[field_key],
                "strategy": selected.get(field_key, "default")}
        telemetry.field_outcomes.add(1, {**dims, "resolved": str(o.field_resolved).lower(),
                                         "human_review": str(o.human_review_required).lower(),
                                         "human_corrected": str(o.human_corrected).lower()})
        telemetry.field_tool_calls.record(o.tool_calls, dims)
        telemetry.field_search_rounds.record(o.search_rounds, dims)

def _latency_ms(events: list[dict[str, Any]], field_key: str) -> float | None:
    if not events:
        return None
    resolved_at = next((e.get("created_at") for e in events
                        if e.get("field_key") == field_key
                        and e.get("kind") == EventKind.FIELD_TRANSITION
                        and (e.get("attrs") or {}).get("to_status") == "RESOLVED"), None)
    started_at = events[0].get("created_at")
    if not resolved_at or not started_at:
        return None
    delta = datetime.fromisoformat(resolved_at) - datetime.fromisoformat(started_at)
    return max(0.0, delta.total_seconds() * 1000)
