import asyncio
import logging
from collections.abc import Mapping
from urllib.parse import urlsplit
from uuid import uuid4
from langgraph.types import interrupt
from water_source_research.acquisition.service import AcquisitionService, DocStatus
from water_source_research.core.capabilities import Capability
from water_source_research.discovery.base import SearchResult
from water_source_research.discovery.normalize import canonical_url
from water_source_research.discovery.queries import build_queries, previous_queries
from water_source_research.discovery.ranking import dedupe_keys, merge_and_rank
from water_source_research.discovery.service import DiscoveryService
from water_source_research.llm.gateway import LLMGateway
from water_source_research.llm.router import llm_call_log
from water_source_research.observability.llm_telemetry import llm_call_context
from water_source_research.observability.otel import get_tracer
from water_source_research.procedures.retrieval import ProcedureMemory
from water_source_research.procedures.usage import UsageTracker
from water_source_research.schemas.common import (
    FINAL_STATUSES, CheckResult, FieldResolutionStatus, ValidationOutcome,
)
from water_source_research.schemas.events import (
    ANCHOR_FIELDS, EventKind, EventResult, append_event, strip_url,
)
from water_source_research.schemas.records import RecordSet
from water_source_research.schemas.research import (
    CityResearchState, ClaimQualifiers, FieldClaim, FieldDefinition,
)
from water_source_research.context_manager import (
    ContextBundle, ContextPurpose, ContextService, ContextStatus,
)
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.normalization import EvidenceIndex, normalize_research
from water_source_research.services.record_store import RecordRepository
from water_source_research.services.resolution import ResolutionPolicy, resolve_research
from water_source_research.services.supply_records import build_record_set
from water_source_research.services.review import (
    ReviewRepository, apply_decisions, legacy_decisions, open_review_items,
)
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
    evidence_store: EvidenceRepository | None = None,
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
            hits = updates.pop("_raw_hits", [])
            if evidence_store is not None and hits and state.get("run_id"):
                try:  # provenance for later diagnosis; never fails the run
                    await asyncio.to_thread(evidence_store.save_discovery_results,
                                            state["run_id"], rounds, hits)
                except Exception:
                    log.warning("could not store raw discovery results", exc_info=True)
        return {**state, **updates, "discovery_rounds": rounds}
    return discover_node

def utility_hosts(state: GraphState) -> list[str]:
    """Hosts of successfully acquired documents classified as the city's utility, most
    documents first (site-restricted queries and acquisition priority)."""
    counts: dict[str, int] = {}
    for d in state.get("acquired_documents", []):
        if d.get("status") == "OK" and (d.get("source_type") or "").upper() == "UTILITY":
            host = (urlsplit(d.get("final_url") or d.get("requested_url") or "").hostname
                    or "").removeprefix("www.")
            if host:
                counts[host] = counts.get(host, 0) + 1
    return sorted(counts, key=lambda h: -counts[h])

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
        already_run=previous_queries(events), max_per_kind=settings.max_queries_per_round,
        utility_hosts=utility_hosts(state))
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
    kept = {k for c in outcome.candidates for k in dedupe_keys(c)}
    hits = [{"call_index": i, "result_index": n, "provider": call.provider,
             "strategy": call.strategy, "query": call.query, "field_key": call.field_key,
             "rank": r.rank, "url": r.url, "canonical_url": canonical_url(r.url),
             "title": r.title, "source_type": r.source_type, "doi": r.doi,
             "retained": bool(dedupe_keys(r) & kept)}
            for i, call in enumerate(outcome.calls) for n, r in enumerate(call.results)
            if r.url]
    return {
        "_raw_hits": hits,
        "events": events,
        "discovery_candidates": [c.to_dict() for c in candidates],
        "candidate_urls": [c.url for c in candidates],
        "discovery_provider_status": status,
    }

def make_acquire_node(acquisition: AcquisitionService | None = None,
                      evidence_store: EvidenceRepository | None = None):
    async def acquire_node(state: GraphState) -> GraphState:
        if acquisition is None:  # no acquirer configured: evidence only from the caller
            return {**state, "evidence_chunks": state.get("evidence_chunks", [])}
        with get_tracer().start_as_current_span("graph.acquire") as span:
            span.set_attribute("graph.run_id", state.get("run_id", ""))
            updates, attempted, new_chunks = await _acquire(acquisition, evidence_store, state)
            span.set_attribute("graph.documents_attempted", attempted)
            span.set_attribute("graph.new_chunks", new_chunks)
        return {**state, **updates}
    return acquire_node

# No acquirer: evidence comes only from the caller (tests patch this to inject chunks).
acquire_node = make_acquire_node()

async def _acquire(acquisition: AcquisitionService, evidence_store: EvidenceRepository | None,
                   state: GraphState) -> tuple[GraphState, int, int]:
    """Fetch new candidate documents and turn them into evidence chunks.

    Individual fetch failures are recorded, never raised; a failure to *persist* evidence
    does raise, so the run is retried rather than recorded without it."""
    previous = list(state.get("acquired_documents", []))
    outcome = await acquisition.acquire_round(state.get("discovery_candidates", []), previous,
                                              round_no=state.get("discovery_rounds", 1),
                                              priority_hosts=utility_hosts(state))
    chunks = list(state.get("evidence_chunks", []))
    known = {c["chunk_id"] for c in chunks}
    new_chunks = [c for c in outcome.chunks if c.chunk_id not in known]
    chunks.extend(c.model_dump(mode="json") for c in new_chunks)
    events = list(state.get("events", []))
    for rec in outcome.records:
        attrs: dict[str, object] = {"url": rec["requested_url"],
                                    "chunks": rec.get("chunks") or 0,
                                    "duration_ms": rec.get("duration_ms") or 0.0}
        if rec.get("source_type"):
            attrs["source_type"] = rec["source_type"]
        ok = rec["status"] == DocStatus.OK
        strategy = "fetch:" + rec["status"].lower()
        if rec.get("error_kind"):
            strategy += ":" + rec["error_kind"].lower()
        events = append_event(events, kind=EventKind.TOOL_CALL, node="acquire",
                              capability=Capability.STATIC_DOCUMENT_FETCH,
                              provider="direct_http", strategy=strategy,
                              result=EventResult.SUCCESS if ok else EventResult.FAILURE,
                              attrs=attrs)
        if ok and rec.get("acquisition_method") == "DIRECT_HTTP_PDF":
            events = append_event(events, kind=EventKind.TOOL_CALL, node="acquire",
                                  capability=Capability.DOCUMENT_PARSING, provider="pdfium",
                                  strategy="pdf_text", result=EventResult.SUCCESS,
                                  attrs={"url": rec["requested_url"],
                                         "chunks": rec.get("chunks") or 0})
    if evidence_store is not None and outcome.records:
        await asyncio.to_thread(evidence_store.save_documents, state["run_id"],
                                outcome.records, outcome.chunks)
    updates: GraphState = {"acquired_documents": previous + outcome.records,
                           "evidence_chunks": chunks, "events": events}
    return updates, len(outcome.records), len(new_chunks)

# Fields that define the run's entities: later rounds may name additional legitimate values
# (another water source, the utility's own documents), so they are re-extracted when
# materially new relevant evidence arrives even though they already have candidates.
REEXTRACT_FIELDS = ("water_source", "utility_name", "reservoir_names")
MAX_REEXTRACTIONS_PER_FIELD = 2  # per run; at most one per round

def reextraction_targets(context_service: ContextService, state: GraphState,
                         already: list[str]) -> dict[str, int]:
    """field -> number of new relevant chunks, for entity-defining fields that already have
    candidates, are still researched, have re-extractions left and got new evidence."""
    if "fields_needing_evidence" not in state:  # first pass: everything is extracted anyway
        return {}
    research = CityResearchState.model_validate(state["research_state"])
    researched = set(state.get("unresolved_fields") or research.fields)
    done = state.get("reextractions") or {}
    seen = {(c, k) for c, k in state.get("seen_evidence", [])}
    chunks = state.get("evidence_chunks", [])
    out: dict[str, int] = {}
    for key in REEXTRACT_FIELDS:
        fs = research.fields.get(key)
        if (key in already or key not in researched or fs is None or not fs.candidate_claims
                or fs.status in FINAL_STATUSES
                or done.get(key, 0) >= MAX_REEXTRACTIONS_PER_FIELD):
            continue
        new = context_service.manager.new_relevant_evidence(key, research, chunks, seen)
        if new:
            out[key] = len(new)
    return out

def make_extract_node(context_service: ContextService, gateway: LLMGateway):
    async def extract_node(state: GraphState) -> GraphState:
        run_id = state["run_id"]
        # First pass: every unresolved field. Re-entry after discovery: fields that lacked
        # evidence, plus entity-defining fields with materially new relevant evidence.
        if "fields_needing_evidence" in state:
            fields = list(state["fields_needing_evidence"])
        else:
            fields = list(state.get("unresolved_fields", []))
        again = reextraction_targets(context_service, state, fields)
        fields += list(again)
        extraction_round = max(1, state.get("discovery_rounds", 0))

        with get_tracer().start_as_current_span("graph.extract") as span:
            span.set_attribute("graph.run_id", run_id)
            span.set_attribute("graph.fields", fields)
            span.set_attribute("graph.discovery_round", state.get("discovery_rounds", 0))
            results = await asyncio.gather(*(
                _extract_field(context_service, gateway, state, run_id, f,
                               extraction_round=extraction_round,
                               reextraction=(f in again),
                               attempt=1 + (state.get("reextractions") or {}).get(f, 0)
                               if f in again else 1)
                for f in fields
            ))

            research = CityResearchState.model_validate(state["research_state"])
            seen = {(c, k) for c, k in state.get("seen_evidence", [])}
            claims = list(state.get("claims", []))
            bundle_ids = dict(state.get("context_bundle_ids", {}))
            events = list(state.get("events", []))
            needing: list[str] = []
            reextractions = dict(state.get("reextractions") or {})
            for field_key, bundle, new_claims, calls in results:
                if bundle.bundle_id:
                    bundle_ids[field_key] = bundle.bundle_id
                if bundle.status is ContextStatus.NOT_NEEDED:
                    reason = bundle.skipped_fields.get(field_key)
                    if reason and not reason.startswith("already "):  # no model call, and why
                        events = append_event(
                            events, kind=EventKind.NODE, node="extract", field_key=field_key,
                            strategy=ContextStatus.NOT_NEEDED.value,
                            attrs={"bundle_status": ContextStatus.NOT_NEEDED.value,
                                   "claims": 0, "discovery_round": extraction_round,
                                   "skip_reason": reason})
                    continue
                re_run = field_key in again
                called = bundle.status is ContextStatus.READY
                if re_run and called:  # only a re-extraction the model saw uses the budget
                    reextractions[field_key] = reextractions.get(field_key, 0) + 1
                attrs: dict[str, object] = {"bundle_status": bundle.status.value,
                                            "claims": len(new_claims),
                                            "discovery_round": extraction_round,
                                            **context_attrs(bundle, field_key),
                                            **llm_usage(calls)}
                if bundle.status is ContextStatus.NO_RELEVANT_EVIDENCE:
                    attrs.update(skip_reason="no new passage about the city or its water "
                                             "system (other places, or content already sent)",
                                 **relevance_attrs(bundle, field_key))
                if re_run:
                    attrs.update(reextraction=reextractions.get(field_key, 0),
                                 new_chunks=again[field_key])
                events = append_event(events, kind=EventKind.NODE, node="extract",
                                      field_key=field_key,
                                      strategy=("reextract:" if re_run else "")
                                      + bundle.status.value, attrs=attrs)
                if not called:  # no evidence, or none about the city: back to discovery
                    if not re_run:
                        needing.append(field_key)
                    continue
                # The LLM has now seen these chunks for this field; don't resend them.
                seen |= bundle.consumed
                if not new_claims:
                    if not re_run:  # a re-extraction that found nothing keeps its candidates
                        needing.append(field_key)
                    continue
                for c in new_claims:
                    q = c.qualifiers or ClaimQualifiers()
                    c.qualifiers = q.model_copy(update={"extraction_round": extraction_round})
                fs = research.fields[field_key]
                fs.candidate_claims.extend(new_claims)
                if field_key == "water_source":
                    _keep_reservoirs(research, new_claims, state, events)
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
            "reextractions": reextractions,
            "events": events,
        }

    return extract_node

def _keep_reservoirs(research: CityResearchState, claims: list[FieldClaim],
                     state: GraphState, events: list[dict]) -> None:
    """A reservoir named as a water source stays a water_source candidate (for review, never
    converted) and is also a reservoir_names candidate with the same evidence and stated
    relations, when that field is researched."""
    researched = set(state.get("unresolved_fields") or research.fields)
    target = research.fields.get("reservoir_names")
    if target is None or "reservoir_names" not in researched:
        return
    for c in claims:
        if not c.qualifiers or c.qualifiers.source_kind != "reservoir":
            continue
        copy = c.model_copy(deep=True, update={"field_key": "reservoir_names",
                                                "claim_uid": None})
        if any(str(x.value).casefold() == str(copy.value).casefold()
               for x in target.candidate_claims):
            continue
        target.candidate_claims.append(copy)
        if target.status is FieldResolutionStatus.UNRESOLVED:
            target.status = FieldResolutionStatus.CANDIDATE

async def _extract_field(
    context_service: ContextService,
    gateway: LLMGateway,
    state: GraphState,
    run_id: str,
    field_key: str,
    *,
    extraction_round: int = 1,
    reextraction: bool = False,
    attempt: int = 1,
) -> tuple[str, ContextBundle, list[FieldClaim], list[dict]]:
    with get_tracer().start_as_current_span("extraction.field") as span:
        span.set_attribute("research.run_id", run_id)
        span.set_attribute("research.field_key", field_key)
        span.set_attribute("research.discovery_round", extraction_round)
        span.set_attribute("extraction.reextraction", reextraction)
        span.set_attribute("extraction.attempt", attempt)
        bundle = await context_service.get_context(
            run_id=run_id, field_key=field_key, operation=ContextPurpose.EXTRACT,
            graph_state=state)
        span.set_attribute("context.status", bundle.status.value)
        if bundle.status is not ContextStatus.READY:
            return field_key, bundle, [], []
        calls: list[dict] = []  # model calls made for this field (set in this task's context)
        llm_call_log.set(calls)
        with llm_call_context(run_id=run_id, field_key=field_key,
                              discovery_round=extraction_round, reextraction=reextraction,
                              attempt=attempt, bundle_id=bundle.bundle_id,
                              task_kind="extract"):
            claims = await gateway.extract(bundle, run_id=run_id, field_key=field_key)
        wrong = sorted({c.field_key for c in claims if c.field_key != field_key})
        if wrong:
            raise ValueError(f"Gateway returned claims for {wrong} when extracting "
                             f"{field_key!r}")
        span.set_attribute("extraction.claims", len(claims))
        return field_key, bundle, claims, calls

def context_attrs(bundle: ContextBundle, field_key: str) -> dict[str, object]:
    """What the model was (or would have been) shown, for the token audit: the number of
    evidence passages for the field and a short digest of the rendered context, so repeated
    identical contexts can be counted. 16 hex characters: long enough to tell contexts apart,
    short enough not to be mistaken for a secret by the event sanitizer."""
    items = sum(1 for i in bundle.items if i.kind == "evidence" and field_key in i.field_keys)
    attrs: dict[str, object] = {"evidence_items": items}
    if items:
        attrs["context_digest"] = bundle.fingerprint[:16]
    return attrs

def relevance_attrs(bundle: ContextBundle, field_key: str) -> dict[str, object]:
    """Why the gate held a field back: the passages' locality and the relevance rules they met
    (event attrs are scalars, so as "TARGET:1,NONE:3")."""
    localities: dict[str, int] = {}
    rules: dict[str, int] = {}
    for i in bundle.items:
        if i.kind != "evidence" or field_key not in i.field_keys:
            continue
        loc = (i.locality or {}).get(field_key, "UNKNOWN")
        localities[loc] = localities.get(loc, 0) + 1
        for r in (i.relevance or {}).get(field_key, []):
            rules[r] = rules.get(r, 0) + 1
        if i.repeat_of and field_key in i.repeat_of:  # already sent for this field
            rules["REPEAT_OF_SEEN"] = rules.get("REPEAT_OF_SEEN", 0) + 1
    fmt = lambda d: ",".join(f"{k}:{v}" for k, v in sorted(d.items())) or "none"  # noqa: E731
    return {"items_by_locality": fmt(localities), "rule_hits": fmt(rules)}

def llm_usage(calls: list[dict]) -> dict[str, object]:
    """Model usage of one extraction for its trusted event: real model requests (a response
    cache hit makes 0), tokens and latency, so a run's LLM usage is auditable in run_events.
    Recorded on the NODE event (not a TOOL_CALL), so procedure eligibility is unchanged."""
    if not calls:
        return {}
    unknown = sum(1 for c in calls if c["prompt_tokens"] is None
                  or c["completion_tokens"] is None)
    usage: dict[str, object] = {
        "llm_requests": sum(c["requests"] for c in calls),
        "cache_hits": sum(1 for c in calls if c["cache_hit"]),
        "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in calls),
        "completion_tokens": sum(c["completion_tokens"] or 0 for c in calls),
        "duration_ms": sum(c["latency_ms"] for c in calls),
        "model": f'{calls[0]["provider"]}:{calls[0]["model"]}'}
    if unknown:  # token sums cover only the calls whose usage the provider reported
        usage["usage_unknown_calls"] = unknown
    persistent = sum(1 for c in calls if c.get("cache_layer") == "persistent")
    if persistent:  # app-cache hits answered by the persistent tier (the rest: in process)
        usage["persistent_cache_hits"] = persistent
    cached = [c.get("cached_tokens") for c in calls if c.get("cached_tokens") is not None]
    if cached:  # provider-side prompt cache; absent when the provider doesn't report it
        usage["cached_tokens"] = sum(cached)
    return usage

def make_validate_node(fields: Mapping[str, FieldDefinition]):
    """Normalize and validate every candidate claim (deterministic; no model calls).

    Claims are never dropped: each gets a stable claim_uid and a ClaimValidation, and INVALID
    claims stay visible candidates that resolution never selects."""
    async def validate_node(state: GraphState) -> GraphState:
        research = CityResearchState.model_validate(state["research_state"])
        with get_tracer().start_as_current_span("graph.validate") as span:
            span.set_attribute("graph.run_id", state.get("run_id", ""))
            normalized = normalize_research(research, fields, evidence_index(state))
            seen = set(state.get("validated_claim_uids", []))
            events = list(state.get("events", []))
            counts: dict[str, int] = {}
            for key, fs in normalized.fields.items():
                for claim in fs.candidate_claims:
                    if claim.claim_uid in seen or claim.validation is None:
                        continue
                    seen.add(claim.claim_uid or "")
                    outcome = claim.validation.outcome
                    counts[outcome.value] = counts.get(outcome.value, 0) + 1
                    problems = claim.validation.codes(CheckResult.FAIL, CheckResult.WARN)
                    events = append_event(
                        events, kind=EventKind.VALIDATION, node="validate", field_key=key,
                        strategy=outcome.value,
                        result=EventResult.REJECTED if outcome is ValidationOutcome.INVALID
                        else EventResult.ACCEPTED,
                        attrs={"claim_uid": claim.claim_uid, "outcome": outcome.value,
                               **({"reason_code": ",".join(problems)} if problems else {})})
            for outcome_name, n in counts.items():
                span.set_attribute(f"graph.claims_{outcome_name.lower()}", n)
        return {**state, "research_state": normalized.model_dump(mode="json"),
                "events": events, "validated_claim_uids": sorted(seen)}
    return validate_node

def make_resolve_node(fields: Mapping[str, FieldDefinition],
                      policy: ResolutionPolicy | None = None):
    """Deterministic candidate resolution: accept only under explicit, conservative rules;
    flag conflicts and anything uncertain for human review."""
    policy = policy or ResolutionPolicy()

    async def resolve_node(state: GraphState) -> GraphState:
        research = CityResearchState.model_validate(state["research_state"])
        with get_tracer().start_as_current_span("graph.resolve") as span:
            span.set_attribute("graph.run_id", state.get("run_id", ""))
            resolved, entries = resolve_research(research, fields, evidence_index(state), policy)
            log, events = _record_resolution(state, resolved, entries)
            for key, fs in resolved.fields.items():
                before = research.fields[key].status if key in research.fields else None
                if before is None or before is fs.status:
                    continue
                attrs: dict[str, object] = {"from_status": before.value,
                                            "to_status": fs.status.value}
                if fs.status is FieldResolutionStatus.RESOLVED and key in ANCHOR_FIELDS \
                        and fs.selected_claim and isinstance(fs.selected_claim.value, str):
                    attrs["anchor_value"] = fs.selected_claim.value
                events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="resolve",
                                      field_key=key, attrs=attrs)
            review = review_summary(resolved, state)
            span.set_attribute("graph.fields_requiring_review", review["fields_requiring_review"])
        return {**state, "research_state": resolved.model_dump(mode="json"), "events": events,
                "resolution_log": log, **review}
    return resolve_node

def _record_resolution(state: GraphState, resolved: CityResearchState,
                       entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Append changed (field, entity) decisions to the resolution log, with trusted events.
    Unchanged decisions from an earlier pass (e.g. after a review round) are not repeated."""
    log = list(state.get("resolution_log", []))
    events = list(state.get("events", []))
    latest = {(e["field_key"], e["entity_key"]): e for e in log}
    source_types = {c["url"]: c.get("source_type") for c in state.get("evidence_chunks", [])}
    for entry in entries:
        key = (entry["field_key"], entry["entity_key"])
        prior = latest.get(key)
        if prior and all(prior[k] == entry[k] for k in
                         ("to_status", "rule", "selected_claim_uids", "reason_codes")):
            continue
        entry = {**entry, "seq": len(log) + 1}
        log.append(entry)
        latest[key] = entry
        accepted = entry["to_status"] == FieldResolutionStatus.RESOLVED.value
        events = append_event(
            events, kind=EventKind.RESOLUTION, node="resolve", field_key=entry["field_key"],
            strategy=entry["rule"],
            result=EventResult.ACCEPTED if accepted else EventResult.NO_RESULT,
            attrs={"entity_key": entry["entity_key"], "rule": entry["rule"],
                   "from_status": entry["from_status"], "to_status": entry["to_status"],
                   **({"reason_code": ",".join(entry["reason_codes"])}
                      if entry["reason_codes"] else {})})
        if not accepted:
            continue
        fs = resolved.fields[entry["field_key"]]
        for uid in entry["selected_claim_uids"]:
            claim = fs.claim(uid)
            for ref in claim.evidence if claim else []:
                events = append_event(
                    events, kind=EventKind.EVIDENCE_ACCEPTED, node="resolve",
                    field_key=entry["field_key"],
                    attrs={"url": ref.url, "source_type": source_types.get(ref.url),
                           "claim_uid": uid})
    return log, events

def evidence_index(state: GraphState) -> EvidenceIndex:
    return EvidenceIndex(state.get("evidence_chunks", []), state.get("acquired_documents", []))

def review_summary(research: CityResearchState, state: GraphState) -> GraphState:
    """Which fields need a person, and why. A run never counts as complete just because
    extraction finished."""
    missing = [k for k in state.get("fields_needing_evidence") or []
               if k in research.fields and research.fields[k].status not in FINAL_STATUSES]
    conflicts = [k for k, fs in research.fields.items()
                 if fs.status is FieldResolutionStatus.CONFLICT]
    hitl = [k for k, fs in research.fields.items()
            if fs.status is FieldResolutionStatus.HITL_REQUIRED]
    reasons = []
    if missing:
        reasons.append("No sufficient evidence after discovery for: " + ", ".join(missing))
    if conflicts:
        reasons.append("Conflicting values for: " + ", ".join(conflicts))
    if hitl:
        reasons.append("Not accepted automatically: " + ", ".join(hitl))
    return {"needs_human_review": bool(missing or conflicts or hitl),
            "review_reason": "; ".join(reasons) or None,
            "fields_requiring_review": conflicts + hitl}

def make_human_review_node(fields: Mapping[str, FieldDefinition],
                           review_store: ReviewRepository | None = None,
                           evidence_store: EvidenceRepository | None = None):
    """Human review.

    With a ``review_store`` (production; needs a checkpointer): persist the open review items
    of this round and pause the run (LangGraph ``interrupt``). The worker marks the job
    AWAITING_REVIEW; decisions arrive through the API; when the run is resumed this node runs
    again from the top, saves the same items (insert-or-skip) and applies the latest decision
    per item from the database (never from the resume payload). The rules then re-resolve
    what a reviewer sent back, and another round follows if anything is still open. Claims
    and resolution decisions are persisted before pausing, so every claim a review item
    names exists in ``field_claims`` while the run waits.

    Without one (tests, inline runs): applies field-level ``review_decisions`` from the state.
    """
    async def human_review_node(state: GraphState) -> GraphState:
        research = CityResearchState.model_validate(state["research_state"])
        index = evidence_index(state)
        chunks = {c["chunk_id"]: c for c in state.get("evidence_chunks", [])}
        round_no = state.get("review_round", 0) + 1
        with get_tracer().start_as_current_span("graph.human_review") as span:
            span.set_attribute("graph.run_id", state.get("run_id", ""))
            span.set_attribute("review.round", round_no)
            if review_store is None:
                raw = state.get("review_decisions") or []
                if not raw:
                    return state
                items, decisions = legacy_decisions(research, fields, raw, index)
            else:
                run_id = state["run_id"]
                specs = open_review_items(run_id, round_no, research, fields,
                                          state.get("unresolved_fields"),
                                          state.get("fields_needing_evidence"), index)
                if not specs:
                    return state
                if evidence_store is not None:
                    await asyncio.to_thread(
                        evidence_store.save_results, run_id, state["research_state"],
                        state.get("fields_needing_evidence"), state.get("unresolved_fields"),
                        state.get("resolution_log"))
                await asyncio.to_thread(review_store.save_items, run_id, specs)
                span.set_attribute("review.items", len(specs))
                interrupt({"round": round_no, "items": [s.item_id for s in specs]})
                items = [{"id": s.item_id, "field_key": s.field_key, "entity_key": s.entity_key,
                          "label": s.label, "candidate_claim_uids": s.candidate_claim_uids}
                         for s in specs]
                decisions = await asyncio.to_thread(review_store.decisions, run_id, round_no)
            applied = apply_decisions(research, fields, items, decisions, index, chunks.get)
            span.set_attribute("review.decisions_applied", len(applied.hitl))
            if review_store is not None:
                outcomes = {h["decision_id"]: h["field_to_status"] for h in applied.hitl
                            if h["decision_id"] is not None}
                await asyncio.to_thread(review_store.record_outcomes, outcomes)
                await asyncio.to_thread(review_store.mark_applied, state["run_id"], round_no)
        events = _review_events(state, research, applied)
        log = list(state.get("resolution_log", []))
        log += [{**e, "seq": len(log) + n + 1} for n, e in enumerate(applied.log)]
        return {**state, "research_state": applied.research.model_dump(mode="json"),
                "events": events, "resolution_log": log, "review_decisions": [],
                "review_round": round_no if review_store is not None
                else state.get("review_round", 0)}
    return human_review_node

def _review_events(state: GraphState, before: CityResearchState, applied) -> list[dict]:
    """Trusted HITL, EVIDENCE_ACCEPTED and FIELD_TRANSITION events for applied decisions."""
    events = list(state.get("events", []))
    source_types = {strip_url(c["url"]): c.get("source_type")
                    for c in state.get("evidence_chunks", [])}
    for h in applied.hitl:
        attrs: dict[str, object] = {"entity_key": h["entity_key"]}
        if h["correction_category"]:
            attrs["correction_category"] = h["correction_category"]
        if h["decision_id"] is not None:
            attrs["decision_id"] = h["decision_id"]
        if h.get("validation_override"):
            attrs["validation_override"] = True
        attrs["to_status"] = h.get("to_status")
        events = append_event(events, kind=EventKind.HITL, node="human_review",
                              field_key=h["field_key"], strategy=h["decision"],
                              result=EventResult(h["result"]), attrs=attrs)
    for key, claim in applied.accepted:
        for ref in claim.evidence:
            events = append_event(events, kind=EventKind.EVIDENCE_ACCEPTED, node="human_review",
                                  field_key=key,
                                  attrs={"url": ref.url, "claim_uid": claim.claim_uid,
                                         "source_type": source_types.get(strip_url(ref.url))})
    for key, fs in applied.research.fields.items():
        previous = before.fields[key].status
        if previous is fs.status:
            continue
        attrs = {"from_status": previous.value, "to_status": fs.status.value}
        if fs.status is FieldResolutionStatus.RESOLVED and key in ANCHOR_FIELDS \
                and fs.selected_claim and isinstance(fs.selected_claim.value, str):
            attrs["anchor_value"] = fs.selected_claim.value
        events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="human_review",
                              field_key=key, attrs=attrs)
    return events

def make_assemble_node(fields: Mapping[str, FieldDefinition]):
    """SupplyRecords, derived fields and the final quality checks (services/supply_records.py).

    Deterministic. ``completion`` says what the run is scientifically: FINALIZED,
    FINALIZED_WITH_GAPS (explicit Unknowns) or INCOMPLETE (open fields or failed checks)."""
    async def assemble_node(state: GraphState) -> GraphState:
        research = CityResearchState.model_validate(state["research_state"])
        with get_tracer().start_as_current_span("graph.assemble") as span:
            span.set_attribute("graph.run_id", state.get("run_id", ""))
            rs = build_record_set(research, fields, state.get("unresolved_fields"))
            span.set_attribute("records.count", len(rs.records))
            span.set_attribute("records.completion", rs.completion)
        events = list(state.get("events", []))
        events = append_event(events, kind=EventKind.NODE, node="assemble",
                              strategy=rs.completion,
                              result=EventResult.FAILURE if rs.completion == "INCOMPLETE"
                              else EventResult.SUCCESS,
                              attrs={"records": len(rs.records)})
        return {**state, "record_set": rs.model_dump(mode="json"),
                "completion": rs.completion, "events": events}
    return assemble_node

def make_finalize_node(
    fields: Mapping[str, FieldDefinition],
    run_recorder: RunRecorder | None = None,
    usage_tracker: UsageTracker | None = None,
    evidence_store: EvidenceRepository | None = None,
    record_store: RecordRepository | None = None,
):
    async def finalize_node(state: GraphState) -> GraphState:
        state = {**state, "finalized": True}
        with get_tracer().start_as_current_span("graph.finalize") as span:
            span.set_attribute("research.run_id", state.get("run_id", ""))
            if evidence_store is not None:  # part of the result: a failure fails the run
                await asyncio.to_thread(
                    evidence_store.save_results, state["run_id"], state["research_state"],
                    state.get("fields_needing_evidence"), state.get("unresolved_fields"),
                    state.get("resolution_log"))
            if record_store is not None and state.get("record_set"):
                await asyncio.to_thread(record_store.save, state["run_id"],
                                        RecordSet.model_validate(state["record_set"]))
            # The scientific result is complete here; everything below is soft post-processing.
            post = await asyncio.to_thread(finalize_post_processing, state, run_recorder,
                                           usage_tracker, fields)
            span.set_attribute("research.post_processing_errors",
                               len(post["post_processing_errors"]))
        return {**state, **post}
    return finalize_node
