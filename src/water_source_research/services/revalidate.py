"""Revalidation of a stored run (mode REVALIDATE): validation and resolution re-run on the
parent's claims as extracted and its acquired evidence, with no discovery, fetching, model calls
or human review. Used to measure validation/resolution changes reproducibly
(scripts/revalidate_run.py).

Input, all read from the parent and frozen there:
- the claims as the model extracted them (``field_claims``: original values, citations and the
  extraction qualifiers — round, source kind, stated relations). Reviewer corrections (CORRECTED
  claims), review statuses and reviewer resolutions are left out, so the result is what the
  automated pipeline decides on its own;
- the parent's documents and evidence chunks, copied to the new run (insert-or-skip), so
  citations link within the new run.

The graph enters at ``validate`` and runs validate -> resolve -> human_review (no review store:
nothing is asked of people) -> assemble -> finalize. The new run gets its own claims,
resolutions, records and run events, and a research_jobs row (mode REVALIDATE, parent_run_id,
SUCCEEDED) so it can be evaluated; evaluations of REVALIDATE runs stay out of the per-city
"latest" views. The parent's rows are only read.

``compare_decisions`` checks that a revalidation reproduces the parent's automatic decisions
(last RULES resolution per field and entity before any reviewer decision).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from water_source_research.agents.graph import build_graph
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.schemas.common import ValueOrigin, ValueStatus
from water_source_research.schemas.research import (
    CityResearchState, ClaimQualifiers, EvidenceRef, FieldClaim,
)
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.field_state import initialize_city_state, unresolved_fields
from water_source_research.services.record_store import RecordRepository
from water_source_research.services.run_recorder import RunRecorder

REVALIDATE = "REVALIDATE"
_DROPPED = ("evidence_chunks",)  # kept in evidence_chunks, not duplicated in the job result
# Extraction-time qualifiers: everything else is recomputed by validation.
_EXTRACTION_QUALIFIERS = ("extraction_round", "source_kind", "relations")

def claims_as_extracted(stored: list[dict[str, Any]], city: str, country: str | None
                        ) -> tuple[CityResearchState, dict[str, int]]:
    """(research state, counts) from stored claim rows, as the model extracted them."""
    research = initialize_city_state(city, country)
    counts = {"claims": 0, "excluded_corrected": 0, "unknown_field": 0}
    for row in stored:
        if row.get("review_status") == "CORRECTED":  # a reviewer's value, not the pipeline's
            counts["excluded_corrected"] += 1
            continue
        fs = research.fields.get(row["field_key"])
        if fs is None:
            counts["unknown_field"] += 1
            continue
        original = row.get("original_value")
        q = row.get("qualifiers") or {}
        fs.candidate_claims.append(FieldClaim(
            field_key=row["field_key"],
            value=original if original is not None else row["value"],
            value_status=ValueStatus(row["value_status"]),
            value_origin=ValueOrigin(row["value_origin"]), confidence=row["confidence"],
            evidence=[EvidenceRef(url=c["url"], title=c.get("title"),
                                  page_number=c.get("page_number"), passage=c["quote"],
                                  retrieved_at=c.get("retrieved_at"))
                      for c in row.get("citations") or []],
            qualifiers=ClaimQualifiers(**{k: q[k] for k in _EXTRACTION_QUALIFIERS
                                          if q.get(k) is not None})))
        counts["claims"] += 1
    return research, counts

async def revalidate_run(engine: Engine, parent_run_id: str) -> tuple[str, dict[str, Any]]:
    """(revalidation run id, final state)."""
    with Session(engine) as session:
        parent = session.get(ResearchJobRecord, parent_run_id)
        if parent is None:
            raise LookupError(f"unknown research run {parent_run_id}")
        city, country, keys = parent.city_name, parent.country, parent.field_keys
        city_key, parent_result = parent.city_key, dict(parent.result or {})
    evidence = EvidenceRepository(engine)
    stored = evidence.claims(parent_run_id)
    if not stored:
        raise LookupError(f"run {parent_run_id} has no stored claims to revalidate")
    run_id = str(uuid4())
    started = datetime.now(UTC)
    reclassified: list[dict[str, Any]] = []
    evidence.copy_run_evidence(parent_run_id, run_id, reclassify_city=city,
                               changes=reclassified)
    research, counts = claims_as_extracted(stored, city, country)
    graph = build_graph(
        run_recorder=RunRecorder(engine, enqueue_distillation_jobs=False),
        evidence_store=evidence, record_store=RecordRepository(engine))
    final = await graph.ainvoke({
        "run_id": run_id, "city_name": city, "country": country,
        "research_state": research.model_dump(mode="json"),
        "unresolved_fields": parent_result.get("unresolved_fields")
        or unresolved_fields(initialize_city_state(city, country), keys),
        "fields_needing_evidence": list(parent_result.get("fields_needing_evidence") or []),
        "discovery_rounds": parent_result.get("discovery_rounds") or 0,
        "evidence_chunks": evidence.evidence_chunks(run_id),
        "acquired_documents": evidence.documents(run_id),
        "reprocess_of": parent_run_id,
    }, {"recursion_limit": 100})
    final = dict(final)
    final["revalidation"] = {"parent_run_id": parent_run_id, **counts,
                             "reclassified": reclassified}
    with Session(engine) as session, session.begin():
        session.add(ResearchJobRecord(
            run_id=run_id, city_name=city, country=country, city_key=city_key,
            status="SUCCEEDED", priority=0, attempts=1, max_attempts=1,
            result={k: v for k, v in final.items() if k not in _DROPPED},
            field_keys=keys, review_round=0, completion=final.get("completion"),
            mode=REVALIDATE, parent_run_id=parent_run_id, created_at=started,
            started_at=started, finished_at=datetime.now(UTC)))
    return run_id, final

# --- reproduction check -------------------------------------------------------------------------

def automatic_decisions(resolutions: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    """The last RULES decision per (field, entity) before the first reviewer decision."""
    out: dict[tuple[str, str], dict] = {}
    for r in resolutions:  # in seq order
        if r["decided_by"] != "RULES":
            break
        out[(r["field_key"], r["entity_key"])] = {
            "status": r["to_status"], "rule": r["rule"],
            "selected": sorted(r.get("selected_claim_uids") or []),
            "policy_version": r.get("policy_version")}
    return out

def compare_decisions(engine: Engine, parent_run_id: str, run_id: str) -> list[dict[str, Any]]:
    """Every (field, entity) whose automatic decision differs between the parent and its
    revalidation ([] = reproduced exactly)."""
    evidence = EvidenceRepository(engine)
    before = automatic_decisions(evidence.resolutions(parent_run_id))
    after = automatic_decisions(evidence.resolutions(run_id))
    diffs = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b is not None and a is not None and \
                (b["status"], b["rule"], b["selected"]) == (a["status"], a["rule"], a["selected"]):
            continue
        diffs.append({"field_key": key[0], "entity_key": key[1], "before": b, "after": a})
    return diffs
