"""Procedural memory inside the graph: advisory guidance in, trusted outcomes out.

- plan: retrieve guidance once per field type that still has unaccepted fields; the planner
  picks a usable match or the default strategy. Retrieval failures degrade to default.
- discover: render the selected recipe's query templates for this run (marks "applied").
- finalize: persist trusted events, enqueue distillation, record usage outcomes. All soft.

Guidance never produces claims or field values; only extraction from evidence does.
"""

import logging
import re
from collections.abc import Mapping
from typing import Any
from water_source_research.core.capabilities import Capability
from water_source_research.core.versions import WORKFLOW_VERSION
from water_source_research.observability.otel import get_tracer
from water_source_research.procedures import telemetry
from water_source_research.procedures.retrieval import ProcedureMemory, choose_strategy
from water_source_research.procedures.usage import UsageTracker, record_field_metrics
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.events import EventKind, append_event
from water_source_research.schemas.procedures import (
    RetrievalRequest, RetrievalResult, task_type_for,
)
from water_source_research.schemas.research import CityResearchState, FieldDefinition
from water_source_research.context_manager import ProcedureHint
from water_source_research.services.run_recorder import RunRecorder

log = logging.getLogger(__name__)
_DONE = {FieldResolutionStatus.RESOLVED, FieldResolutionStatus.NOT_APPLICABLE}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

async def plan_with_procedures(
    state: Mapping[str, Any],
    memory: ProcedureMemory,
    tracker: UsageTracker | None,
    available: frozenset[Capability],
    fields: Mapping[str, FieldDefinition],
) -> dict[str, Any]:
    research = CityResearchState.model_validate(state["research_state"])
    pending: dict[str, list[str]] = {}
    for key in state.get("unresolved_fields", []):
        d = fields.get(key)
        fs = research.fields.get(key)
        if d is None or d.derived or (fs and fs.status in _DONE):
            continue  # accepted/derived fields never trigger procedure retrieval
        pending.setdefault(d.field_type, []).append(key)

    water_type = _resolved_value(research, "water_type")
    guidance: dict[str, list[dict[str, Any]]] = {}
    selections: list[dict[str, Any]] = []
    events = list(state.get("events", []))
    degraded = False
    for field_type, keys in sorted(pending.items()):
        req = RetrievalRequest(task_type=task_type_for(field_type), field_type=field_type,
                               workflow_version=WORKFLOW_VERSION, run_id=state["run_id"],
                               field_keys=keys, water_type=water_type)
        try:
            result = await memory.aretrieve(req)
        except Exception:  # noqa: BLE001 - soft dependency boundary
            # retrieve() never raises; this guards the thread hand-off too
            result = RetrievalResult(degraded=True, degraded_reason="unavailable")
        degraded |= result.degraded
        guidance[field_type] = [_compact_match(m) for m in result.matches]
        selection = choose_strategy(result.matches, available)
        strategy = "default:degraded" if result.degraded else "default"
        attrs: dict[str, Any] = {}
        if selection:
            m = selection.match
            strategy = f"procedure:{m.procedure_id}@v{m.version}"
            attrs = {"procedure_id": m.procedure_id, "procedure_version": m.version}
            selections.append({
                "procedure_version_id": m.procedure_version_id,
                "procedure_id": m.procedure_id, "version": m.version,
                "field_type": field_type, "field_keys": keys,
                "query_patterns": m.recipe.query_patterns,
                "usable_capabilities": [s.capability.value for s in selection.usable_steps],
                "dropped_capabilities": selection.dropped_capabilities,
                "adapted": selection.adapted, "applied": False,
            })
            if tracker:
                try:
                    tracker.record_selection(m.procedure_version_id, state["run_id"], keys,
                                             selection.adapted, field_type)
                except Exception:
                    # Bookkeeping is soft; the guidance is still usable.
                    log.warning("procedure selection not recorded", exc_info=True)
        telemetry.strategy_decisions.add(len(keys), {
            "field_type": field_type, "strategy": strategy.split(":")[0]})
        events = append_event(events, kind=EventKind.NODE, node="plan", strategy=strategy,
                              attrs=attrs)
    return {"procedure_guidance": guidance, "procedure_selections": selections,
            "procedure_memory_degraded": degraded, "events": events}

def apply_selected_procedures(state: Mapping[str, Any],
                              tracker: UsageTracker | None) -> dict[str, Any]:
    """Turn selected recipes into concrete, run-specific query plans (advisory)."""
    selections = [dict(s) for s in state.get("procedure_selections", [])]
    if not selections:
        return {}
    research = CityResearchState.model_validate(state["research_state"])
    values = {"city": research.city_name, "country": research.country,
              "utility": _resolved_value(research, "utility_name"),
              "jurisdiction": _resolved_value(research, "jurisdiction")}
    planned = dict(state.get("planned_queries", {}))
    events = list(state.get("events", []))
    for sel in selections:
        if sel.get("applied"):
            continue
        queries = [q for p in sel["query_patterns"] if (q := _render(p, values))]
        planned[sel["field_type"]] = queries
        sel["applied"] = True
        if tracker:
            try:
                tracker.record_application(sel["procedure_version_id"], state["run_id"],
                                           sel["field_keys"])
            except Exception:
                log.warning("procedure application not recorded", exc_info=True)
        events = append_event(events, kind=EventKind.NODE, node="discover",
                              strategy=f"procedure:{sel['procedure_id']}@v{sel['version']}",
                              attrs={"planned_queries": len(queries)})
    return {"procedure_selections": selections, "planned_queries": planned, "events": events}

def procedure_hints(state: Mapping[str, Any], field_type: str) -> tuple[ProcedureHint, ...]:
    """Guidance for a PLAN/QUERY_GENERATION ContextBundle (kept apart from evidence)."""
    return tuple(ProcedureHint(m["procedure_id"], m["version"], m["recipe_text"])
                 for m in state.get("procedure_guidance", {}).get(field_type, []))

def finalize_post_processing(
    state: Mapping[str, Any],
    recorder: RunRecorder | None,
    tracker: UsageTracker | None,
    fields: Mapping[str, FieldDefinition],
) -> dict[str, Any]:
    """Soft post-processing after the scientific result is complete. Never raises."""
    errors: list[str] = []
    jobs: list[str] = []
    tracer = get_tracer()

    def soft(stage: str, fn):
        try:
            return fn()
        except Exception as exc:
            log.warning("post-processing stage %s failed", stage, exc_info=True)
            errors.append(f"{stage}: {type(exc).__name__}")
            telemetry.post_processing_errors.add(1, {"stage": stage})
            return None

    soft("metrics", lambda: record_field_metrics(
        state, {k: d.field_type for k, d in fields.items()}))
    if tracker:
        soft("usage_outcomes", lambda: tracker.record_outcomes(state))
    if recorder:
        with tracer.start_as_current_span("trajectory.persist") as span:
            inserted = soft("persist_run", lambda: recorder.persist(state))
            span.set_attribute("trajectory.events_inserted", inserted or 0)
        if inserted is not None:  # jobs need the persisted run/events
            with tracer.start_as_current_span("distillation.enqueue") as span:
                jobs = soft("enqueue", lambda: recorder.enqueue_distillation(state)) or []
                span.set_attribute("distillation.jobs_enqueued", len(jobs))
    return {"distillation_jobs": jobs, "post_processing_errors": errors}

def _compact_match(m) -> dict[str, Any]:
    return {"procedure_id": m.procedure_id, "procedure_version_id": m.procedure_version_id,
            "version": m.version, "rank": m.rank, "score": m.score,
            "similarity_score": m.similarity_score, "reliability_score": m.reliability_score,
            "reasons": m.reasons, "recipe_text": m.recipe.recipe_text()}

def _resolved_value(research: CityResearchState, key: str) -> str | None:
    fs = research.fields.get(key)
    if fs and fs.status is FieldResolutionStatus.RESOLVED and fs.selected_claim:
        return str(fs.selected_claim.value)
    return None

def _render(pattern: str, values: Mapping[str, str | None]) -> str | None:
    names = _PLACEHOLDER.findall(pattern)
    if any(not values.get(n) for n in names):
        return None  # e.g. {utility} before the utility is known: skip, don't guess
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), pattern)
