"""Compact, UI-facing summaries of what each graph step produced.

Nodes return the full graph state, so a step's output is summarized from the state after
the step plus what changed since the previous one (new events, claims, ...). Summaries are
bounded in size; full detail lives in the run result and the trusted event log.
"""

from typing import Any

# Expected order of a run without discovery loops; used for progress display.
NODE_ORDER = ("plan", "discover", "acquire", "extract", "validate", "human_review", "finalize")
MAX_EVENTS_PER_STEP = 100
MAX_TOP_CANDIDATES = 20
TOP_CANDIDATE_KEYS = ("title", "url", "providers", "source_type", "doi", "doi_verified",
                      "published_date", "score")

def summarize_step(node: str, state: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    new_events = state.get("events", [])[len(previous.get("events", [])):]
    summary: dict[str, Any] = {"events": new_events[:MAX_EVENTS_PER_STEP]}
    if len(new_events) > MAX_EVENTS_PER_STEP:
        summary["events_truncated"] = len(new_events) - MAX_EVENTS_PER_STEP

    if node == "plan":
        summary.update(
            procedure_memory_degraded=state.get("procedure_memory_degraded", False),
            procedure_selections=[
                {"field_type": s["field_type"], "procedure_id": s["procedure_id"],
                 "version": s["version"], "adapted": s["adapted"]}
                for s in state.get("procedure_selections", [])],
            fields_to_research=len(state.get("unresolved_fields", [])),
        )
    elif node == "discover":
        candidates = state.get("discovery_candidates", [])
        summary.update(discovery_round=state.get("discovery_rounds", 0),
                       planned_queries=state.get("planned_queries", {}),
                       candidate_urls=len(state.get("candidate_urls", [])),
                       provider_status=state.get("discovery_provider_status", {}),
                       new_candidates=max(0, len(candidates)
                                          - len(previous.get("discovery_candidates", []))),
                       top_candidates=[{k: c.get(k) for k in TOP_CANDIDATE_KEYS}
                                       for c in candidates[:MAX_TOP_CANDIDATES]])
    elif node == "acquire":
        chunks = state.get("evidence_chunks", [])
        summary.update(evidence_chunks=len(chunks),
                       sources=sorted({c.get("url", "") for c in chunks})[:50])
    elif node == "extract":
        new_claims = state.get("claims", [])[len(previous.get("claims", [])):]
        summary.update(
            fields_needing_evidence=state.get("fields_needing_evidence", []),
            new_claims=[{"field_key": c.get("field_key"), "value": c.get("value"),
                         "confidence": c.get("confidence"),
                         "citations": len(c.get("evidence", []))} for c in new_claims][:100],
            context_bundles=len(state.get("context_bundle_ids", {})),
        )
    elif node == "validate":
        summary.update(needs_human_review=state.get("needs_human_review", False),
                       review_reason=state.get("review_reason"))
    elif node == "human_review":
        summary.update(decisions=[e for e in new_events if e.get("kind") == "HITL"])
    elif node == "finalize":
        statuses: dict[str, int] = {}
        for f in (state.get("research_state") or {}).get("fields", {}).values():
            statuses[str(f.get("status"))] = statuses.get(str(f.get("status")), 0) + 1
        summary.update(finalized=state.get("finalized", False), field_statuses=statuses,
                       post_processing_errors=state.get("post_processing_errors", []),
                       distillation_jobs=len(state.get("distillation_jobs", [])))
    return summary
