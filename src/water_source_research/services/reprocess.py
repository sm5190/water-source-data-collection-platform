"""Reprocess an earlier run without new discovery, acquisition or model calls.

A REPROCESS job (research_jobs.mode, parent_run_id) is a new run of the same city. Its input is
built from what the parent stored: the research state (claims as extracted) from the parent's
job result — or, if that is missing, rebuilt from its field_claims/claim_citations rows — and
its acquired documents and evidence chunks, which are copied to the new run (insert-or-skip),
so citations link and trace within the new run exactly as for a fresh one. The graph enters at
``validate`` (state["reprocess_of"]), so validation, resolution, human review, assembly and
export run on the stored evidence. The parent's rows are never modified.
"""

from collections.abc import Mapping
from typing import Any
from sqlalchemy import Engine
from water_source_research.schemas.common import ValueOrigin, ValueStatus
from water_source_research.schemas.research import CityResearchState, EvidenceRef, FieldClaim
from .evidence_store import EvidenceRepository
from .field_state import initialize_city_state, unresolved_fields

def reprocess_input(engine: Engine, job: Any, parent_result: Mapping[str, Any] | None
                    ) -> dict[str, Any]:
    """Graph input for REPROCESS ``job`` (idempotent: a retried claim copies nothing twice)."""
    parent = job.parent_run_id
    evidence = EvidenceRepository(engine)
    evidence.copy_run_evidence(parent, job.run_id, reclassify_city=job.city_name)
    result = parent_result or {}
    if result.get("research_state"):
        research = CityResearchState.model_validate(result["research_state"])
    else:
        research = rebuild_research(evidence.claims(parent), job.city_name, job.country)
    researched = result.get("unresolved_fields") or unresolved_fields(
        initialize_city_state(job.city_name, job.country), job.field_keys)
    return {
        "run_id": job.run_id, "city_name": job.city_name, "country": job.country,
        "research_state": research.model_dump(mode="json"),
        "unresolved_fields": list(researched),
        "fields_needing_evidence": list(result.get("fields_needing_evidence") or []),
        "claims": list(result.get("claims") or []),
        "discovery_rounds": result.get("discovery_rounds") or 0,
        "evidence_chunks": evidence.evidence_chunks(job.run_id),
        "acquired_documents": evidence.documents(job.run_id),
        "reprocess_of": parent,
    }

def rebuild_research(stored_claims: list[Mapping[str, Any]], city: str,
                     country: str | None) -> CityResearchState:
    """A research state from stored claim rows (as extracted; statuses are re-derived)."""
    research = initialize_city_state(city, country)
    for row in stored_claims:
        fs = research.fields.get(row["field_key"])
        if fs is None or row.get("review_status") == "CORRECTED":
            continue
        original = row.get("original_value")
        fs.candidate_claims.append(FieldClaim(
            field_key=row["field_key"], value=original if original is not None else row["value"],
            value_status=ValueStatus(row["value_status"]),
            value_origin=ValueOrigin(row["value_origin"]), confidence=row["confidence"],
            evidence=[EvidenceRef(url=c["url"], title=c.get("title"),
                                  page_number=c.get("page_number"), passage=c["quote"],
                                  retrieved_at=c.get("retrieved_at"))
                      for c in row.get("citations") or []]))
    return research
