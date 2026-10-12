"""Read-only views over a finished city run, for the dashboard.

Built from the run result (final research state) and the trusted event log. Citations come
only from claim evidence (EvidenceRef: url/title/page/passage), never from procedure memory.
"""

from collections.abc import Mapping
from typing import Any
from water_source_research.schemas.research import FieldDefinition

NO_REVIEW_STATUSES = ("RESOLVED", "NOT_APPLICABLE")

def human_review_log(result: Mapping[str, Any],
                     fields: Mapping[str, FieldDefinition]) -> list[dict[str, Any]]:
    """One entry per field that needed (or received) human attention, with reason and citations."""
    research = (result.get("research_state") or {}).get("fields", {})
    events = result.get("events") or []
    rounds = result.get("discovery_rounds") or 0
    missing = set(result.get("fields_needing_evidence") or [])
    needs_review = bool(result.get("needs_human_review"))
    decisions: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if e.get("kind") == "HITL" and e.get("field_key"):
            decisions.setdefault(e["field_key"], []).append(e)

    items: list[dict[str, Any]] = []
    for key, d in fields.items():
        if d.derived:
            continue
        state = research.get(key) or {}
        status = state.get("status", "UNRESOLVED")
        candidates = state.get("candidate_claims") or []
        base = {"field_key": key, "field_type": d.field_type, "field_status": status}
        if key in missing:
            items.append({**base, "reason_code": "NO_SUFFICIENT_EVIDENCE",
                          "reason": f"No sufficient evidence after {rounds} discovery round(s)",
                          "decision": None, "citations": _claims_citations(candidates)})
        elif status == "CONFLICT":
            items.append({**base, "reason_code": "CONFLICT",
                          "reason": f"{len(candidates)} conflicting candidate claims",
                          "decision": None, "citations": _claims_citations(candidates)})
        elif status in ("CANDIDATE", "HITL_REQUIRED") and needs_review:
            items.append({**base, "reason_code": "AWAITING_VALIDATION",
                          "reason": "Candidate claim awaiting reviewer validation",
                          "decision": None, "citations": _claims_citations(candidates)})
        for e in decisions.get(key, []):
            selected = state.get("selected_claim")
            items.append({**base, "reason_code": "REVIEWER_DECISION",
                          "reason": f"Reviewer {str(e.get('result', '')).lower()}",
                          "decision": e.get("result"),
                          "correction_category": (e.get("attrs") or {}).get("correction_category"),
                          "logged_at": e.get("created_at"),
                          "citations": _claims_citations([selected] if selected else [])})
    return items

def field_provenance(result: Mapping[str, Any],
                     fields: Mapping[str, FieldDefinition]) -> list[dict[str, Any]]:
    """Field-aware tracking: status, value, citations, history and strategy per field."""
    research = (result.get("research_state") or {}).get("fields", {})
    events = result.get("events") or []
    bundles = result.get("context_bundle_ids") or {}
    documents = _documents_by_url(result.get("acquired_documents") or [])
    researched = set(result.get("unresolved_fields") or fields)
    missing = set(result.get("fields_needing_evidence") or [])
    strategy_by_type = {
        s["field_type"]: f"procedure {s['procedure_id']} v{s['version']}"
        + (" (adapted)" if s.get("adapted") else "")
        for s in (result.get("procedure_selections") or []) if s.get("applied")
    }
    by_field: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if e.get("field_key"):
            by_field.setdefault(e["field_key"], []).append(e)

    rows = []
    for key, d in fields.items():
        state = research.get(key) or {}
        selected = state.get("selected_claim") or {}
        candidates = state.get("candidate_claims") or []
        field_events = by_field.get(key, [])
        status = state.get("status", "UNRESOLVED")
        rows.append({
            "field_key": key,
            "field_type": d.field_type,
            "derived": d.derived,
            "depends_on": d.depends_on,
            "status": status,
            "researched": key in researched,
            # Nothing is accepted until a reviewer approves it (human_review -> RESOLVED).
            "review_required": key in researched and not d.derived
            and status not in NO_REVIEW_STATUSES,
            "value": selected.get("value"),
            "value_origin": selected.get("value_origin"),
            "value_status": selected.get("value_status"),
            "confidence": selected.get("confidence"),
            "citations": _citations(selected, documents) if selected else [],
            "candidates": [{"value": c.get("value"), "confidence": c.get("confidence"),
                            "value_origin": c.get("value_origin"),
                            "value_status": c.get("value_status"),
                            "citations": _citations(c, documents)} for c in candidates],
            "needs_evidence": key in missing,
            "strategy": strategy_by_type.get(d.field_type, "default"),
            "context_bundle_id": bundles.get(key),
            "transitions": [{"at": e.get("created_at"), "node": e.get("node"),
                             "from": (e.get("attrs") or {}).get("from_status"),
                             "to": (e.get("attrs") or {}).get("to_status")}
                            for e in field_events if e.get("kind") == "FIELD_TRANSITION"],
            "extraction_attempts": [{"at": e.get("created_at"),
                                     "bundle_status": (e.get("attrs") or {}).get("bundle_status"),
                                     "claims": (e.get("attrs") or {}).get("claims")}
                                    for e in field_events
                                    if e.get("kind") == "NODE" and e.get("node") == "extract"],
            "reviewer_decisions": [{"at": e.get("created_at"), "decision": e.get("result")}
                                   for e in field_events if e.get("kind") == "HITL"],
            "accepted_evidence": [{"url": (e.get("attrs") or {}).get("url"),
                                   "source_type": (e.get("attrs") or {}).get("source_type")}
                                  for e in field_events if e.get("kind") == "EVIDENCE_ACCEPTED"],
        })
    return rows

def _documents_by_url(records: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for r in records:
        if r.get("status") != "OK":
            continue
        for url in (r.get("final_url"), r.get("requested_url")):
            if url:
                out.setdefault(url, r)
    return out

def _citations(claim: Mapping[str, Any],
               documents: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for ev in claim.get("evidence") or []:
        doc = (documents or {}).get(ev.get("url") or "", {})
        out.append({"url": ev.get("url"), "title": ev.get("title"),
                    "page_number": ev.get("page_number"), "passage": ev.get("passage"),
                    "retrieved_at": ev.get("retrieved_at"),
                    "source_type": doc.get("source_type"),
                    "acquisition_method": doc.get("acquisition_method"),
                    "source_date": doc.get("source_date")})
    return out

def _claims_citations(claims: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in claims:
        for cite in _citations(c):
            out.append({"claim_value": c.get("value"), "confidence": c.get("confidence"), **cite})
    return out
