import asyncio
import logging
from collections.abc import Mapping
from uuid import uuid4
from water_source_research.core.capabilities import Capability
from water_source_research.discovery.base import SearchResult
from water_source_research.discovery.queries import build_queries, previous_queries
from water_source_research.discovery.ranking import merge_and_rank
from water_source_research.discovery.service import DiscoveryService
from water_source_research.llm.gateway import LLMGateway
from water_source_research.observability.otel import get_tracer
from water_source_research.procedures.retrieval import ProcedureMemory
from water_source_research.procedures.usage import UsageTracker
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.events import (
    ANCHOR_FIELDS, EventKind, EventResult, append_event, strip_url,
)
from water_source_research.schemas.research import (
    CityResearchState, FieldClaim, FieldDefinition,
)
from water_source_research.context_manager import (
    ContextBundle, ContextPurpose, ContextService, ContextStatus,
)
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.run_recorder import RunRecorder
from .procedure_guidance import (
    apply_selected_procedures, finalize_post_processing, plan_with_procedures,
)
from .state import GraphState

log = logging.getLogger(__name__)

def make_plan_node(
    fields: Mapping[str, FieldDefinition],
    procedure_memory: ProcedureMemory | None = None,
    usage_tracker: UsageTracker | None = None,
    available: frozenset[Capability] = frozenset(),
):
    async def plan_node(state: GraphState) -> GraphState:
        updates: GraphState = {}
        if "run_id" not in state:
            updates["run_id"] = str(uuid4())
        if "research_state" not in state:
            research = initialize_city_state(state["city_name"], state.get("country"))
            updates["research_state"] = research.model_dump(mode="json")
        state = {**state, **updates}
        with get_tracer().start_as_current_span("graph.plan") as span:
            span.set_attribute("research.run_id", state["run_id"])
            if procedure_memory is None:
                span.set_attribute("procedure.enabled", False)
                return state
            guidance = await plan_with_procedures(state, procedure_memory, usage_tracker,
                                                  available, fields)
            span.set_attribute("procedure.enabled", True)
            span.set_attribute("procedure.selected_count", len(guidance["procedure_selections"]))
            span.set_attribute("procedure.degraded", guidance["procedure_memory_degraded"])
            return {**state, **guidance}
    return plan_node

def make_discover_node(
    usage_tracker: UsageTracker | None = None,
    discovery: DiscoveryService | None = None,
    fields: Mapping[str, FieldDefinition] | None = None,
):
    async def discover_node(state: GraphState) -> GraphState:
        state = {**state, **apply_selected_procedures(state, usage_tracker)}
        rounds = state.get("discovery_rounds", 0) + 1
        if discovery is None:  # no providers configured: nothing to discover
            return {**state, "candidate_urls": state.get("candidate_urls", []),
                    "discovery_rounds": rounds}
        with get_tracer().start_as_current_span("graph.discover") as span:
            span.set_attribute("graph.run_id", state.get("run_id", ""))
            span.set_attribute("graph.discovery_round", rounds)
            updates = await _discover(discovery, fields or {}, state)
            span.set_attribute("graph.discovery_candidates",
                               len(updates.get("discovery_candidates", [])))
        return {**state, **updates, "discovery_rounds": rounds}
    return discover_node

async def _discover(discovery: DiscoveryService, fields: Mapping[str, FieldDefinition],
                    state: GraphState) -> GraphState:
    """One discovery round for the fields that still need evidence. Never raises."""
    if "fields_needing_evidence" in state:
        targets = list(state["fields_needing_evidence"])
    else:
        targets = list(state.get("unresolved_fields", []))
    research = CityResearchState.model_validate(state["research_state"])
    utility = research.fields.get("utility_name")
    events = list(state.get("events", []))
    settings = discovery.settings
    queries = build_queries(
        city=research.city_name, country=research.country,
        utility=str(utility.selected_claim.value) if utility and utility.selected_claim
        and utility.status is FieldResolutionStatus.RESOLVED else None,
        field_keys=targets, fields=fields, planned_queries=state.get("planned_queries"),
        already_run=previous_queries(events), max_per_kind=settings.max_queries_per_round)
    status = dict(state.get("discovery_provider_status", {}))
    if not queries:
        return {"events": events, "discovery_provider_status": status,
                "candidate_urls": state.get("candidate_urls", [])}
    try:
        outcome = await discovery.discover(queries, city=research.city_name,
                                           country=research.country)
    except Exception:  # discovery must never fail the run
        log.warning("discovery round failed", exc_info=True)
        events = append_event(events, kind=EventKind.NODE, node="discover",
                              strategy="discovery:error", result=EventResult.FAILURE)
        status["discovery"] = "error"
        return {"events": events, "discovery_provider_status": status,
                "candidate_urls": state.get("candidate_urls", [])}

    for call in outcome.calls:
        attrs: dict[str, object] = {"sources_found": call.sources_found,
                                    "sources_retained": call.sources_retained,
                                    "duration_ms": call.duration_ms}
        if call.query:
            attrs["query"] = call.query
        events = append_event(events, kind=EventKind.TOOL_CALL, node="discover",
                              field_key=call.field_key, capability=call.capability,
                              provider=call.provider, strategy=call.strategy,
                              result=EventResult(call.result), attrs=attrs)
    previous = [SearchResult.from_dict(c) for c in state.get("discovery_candidates", [])]
    candidates = merge_and_rank(previous + outcome.candidates, research.city_name,
                                max_per_host=settings.max_results_per_host,
                                limit=settings.max_candidates_total)
    status.update(outcome.provider_status)
    return {
        "events": events,
        "discovery_candidates": [c.to_dict() for c in candidates],
        "candidate_urls": [c.url for c in candidates],
        "discovery_provider_status": status,
    }

async def acquire_node(state: GraphState) -> GraphState:
    return {**state, "evidence_chunks": state.get("evidence_chunks", [])}

def make_extract_node(context_service: ContextService, gateway: LLMGateway):
    async def extract_node(state: GraphState) -> GraphState:
        run_id = state["run_id"]
        # First pass: every unresolved field. Re-entry after discovery: only fields that
        # previously lacked evidence (others already have candidate claims).
        if "fields_needing_evidence" in state:
            fields = list(state["fields_needing_evidence"])
        else:
            fields = list(state.get("unresolved_fields", []))

        with get_tracer().start_as_current_span("graph.extract") as span:
            span.set_attribute("graph.run_id", run_id)
            span.set_attribute("graph.fields", fields)
            span.set_attribute("graph.discovery_round", state.get("discovery_rounds", 0))
            results = await asyncio.gather(*(
                _extract_field(context_service, gateway, state, run_id, f) for f in fields
            ))

            research = CityResearchState.model_validate(state["research_state"])
            seen = {(c, k) for c, k in state.get("seen_evidence", [])}
            claims = list(state.get("claims", []))
            bundle_ids = dict(state.get("context_bundle_ids", {}))
            events = list(state.get("events", []))
            needing: list[str] = []
            for field_key, bundle, new_claims in results:
                if bundle.bundle_id:
                    bundle_ids[field_key] = bundle.bundle_id
                if bundle.status is ContextStatus.NOT_NEEDED:
                    continue
                events = append_event(events, kind=EventKind.NODE, node="extract",
                                      field_key=field_key, strategy=bundle.status.value,
                                      attrs={"bundle_status": bundle.status.value,
                                             "claims": len(new_claims)})
                if bundle.status is ContextStatus.NO_SUFFICIENT_EVIDENCE:
                    needing.append(field_key)
                    continue
                # The LLM has now seen these chunks for this field; don't resend them.
                seen |= bundle.consumed
                if not new_claims:
                    needing.append(field_key)
                    continue
                fs = research.fields[field_key]
                fs.candidate_claims.extend(new_claims)
                if fs.status is FieldResolutionStatus.UNRESOLVED:
                    fs.status = FieldResolutionStatus.CANDIDATE
                    events = append_event(events, kind=EventKind.FIELD_TRANSITION,
                                          node="extract", field_key=field_key,
                                          attrs={"from_status": "UNRESOLVED",
                                                 "to_status": "CANDIDATE"})
                claims.extend(c.model_dump(mode="json") for c in new_claims)
            span.set_attribute("graph.fields_needing_evidence", needing)

        return {
            **state,
            "research_state": research.model_dump(mode="json"),
            "seen_evidence": sorted([c, k] for c, k in seen),
            "claims": claims,
            "context_bundle_ids": bundle_ids,
            "fields_needing_evidence": needing,
            "events": events,
        }

    return extract_node

async def _extract_field(
    context_service: ContextService,
    gateway: LLMGateway,
    state: GraphState,
    run_id: str,
    field_key: str,
) -> tuple[str, ContextBundle, list[FieldClaim]]:
    bundle = await context_service.get_context(
        run_id=run_id, field_key=field_key, operation=ContextPurpose.EXTRACT, graph_state=state,
    )
    if bundle.status is not ContextStatus.READY:
        return field_key, bundle, []
    claims = await gateway.extract(bundle, run_id=run_id, field_key=field_key)
    wrong = sorted({c.field_key for c in claims if c.field_key != field_key})
    if wrong:
        raise ValueError(f"Gateway returned claims for {wrong} when extracting {field_key!r}")
    return field_key, bundle, claims

async def validate_node(state: GraphState) -> GraphState:
    missing = state.get("fields_needing_evidence") or []
    if missing:
        return {
            **state,
            "needs_human_review": True,
            "review_reason": "No sufficient evidence after discovery for: " + ", ".join(missing),
        }
    return {**state, "needs_human_review": state.get("needs_human_review", False)}

async def human_review_node(state: GraphState) -> GraphState:
    """Apply reviewer decisions and record them as trusted HITL events.

    APPROVED/CORRECTED accepts the top candidate claim (a correction replaces only its
    value; the evidence must still exist). REJECTED discards the candidates.
    """
    decisions = state.get("review_decisions") or []
    if not decisions:
        return state
    research = CityResearchState.model_validate(state["research_state"])
    source_types = {strip_url(c["url"]): c.get("source_type")
                    for c in state.get("evidence_chunks", [])}
    events = list(state.get("events", []))
    for d in decisions:
        key, decision = d["field_key"], EventResult(d["decision"])
        fs = research.fields.get(key)
        if fs is None:
            continue
        hitl_attrs = {"correction_category": d["correction_category"]} \
            if d.get("correction_category") else {}
        events = append_event(events, kind=EventKind.HITL, node="human_review", field_key=key,
                              result=decision, attrs=hitl_attrs)
        previous = fs.status.value
        if decision in (EventResult.APPROVED, EventResult.CORRECTED):
            ranked = sorted(fs.candidate_claims, key=lambda c: -c.confidence)
            if not ranked or not ranked[0].evidence:
                continue  # nothing evidence-backed to accept
            claim = ranked[0]
            if decision is EventResult.CORRECTED and "value" in d:
                claim = claim.model_copy(update={"value": d["value"]})
            fs.selected_claim = claim
            fs.status = FieldResolutionStatus.RESOLVED
            for ref in claim.evidence:
                events = append_event(events, kind=EventKind.EVIDENCE_ACCEPTED,
                                      node="human_review", field_key=key,
                                      attrs={"url": ref.url,
                                             "source_type": source_types.get(strip_url(ref.url))})
            attrs = {"from_status": previous, "to_status": "RESOLVED"}
            if key in ANCHOR_FIELDS and isinstance(claim.value, str):
                attrs["anchor_value"] = claim.value
            events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="human_review",
                                  field_key=key, attrs=attrs)
        elif decision is EventResult.REJECTED:
            fs.candidate_claims = []
            fs.selected_claim = None
            fs.status = FieldResolutionStatus.UNRESOLVED
            events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="human_review",
                                  field_key=key,
                                  attrs={"from_status": previous, "to_status": "UNRESOLVED"})
    return {**state, "research_state": research.model_dump(mode="json"), "events": events,
            "review_decisions": []}

def make_finalize_node(
    fields: Mapping[str, FieldDefinition],
    run_recorder: RunRecorder | None = None,
    usage_tracker: UsageTracker | None = None,
):
    async def finalize_node(state: GraphState) -> GraphState:
        # The scientific result is complete here; everything below is soft post-processing.
        state = {**state, "finalized": True}
        with get_tracer().start_as_current_span("graph.finalize") as span:
            span.set_attribute("research.run_id", state.get("run_id", ""))
            post = await asyncio.to_thread(finalize_post_processing, state, run_recorder,
                                           usage_tracker, fields)
            span.set_attribute("research.post_processing_errors",
                               len(post["post_processing_errors"]))
        return {**state, **post}
    return finalize_node
