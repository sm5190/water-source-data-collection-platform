"""An immutable, read-only snapshot of a research run as persisted, plus its three stage views.

The snapshot is everything an evaluation looks at, taken in one pass and stored (with its
sha256) in the evaluation run, so results stay reproducible after the research run changes
(e.g. it gets reviewed). For a REPROCESS run, discovery, acquisition and extraction telemetry
come from the run it reprocessed (``origin``).

Stage views:
  RAW    every claim as extracted (reviewer-entered CORRECTED claims excluded)
  AUTO   the rules' last decision per (field, entity) before any reviewer decision
  FINAL  assembled SupplyRecord/city values of a finished, non-INCOMPLETE run, each tagged
         with its origin (RULES, REVIEWER, DERIVED, CATALOG, GAP)
"""

import hashlib
import json
from typing import Any
from sqlalchemy import Engine, and_, or_, select
from sqlalchemy.orm import Session
from water_source_research.db.models import ContextBundleRecord
from water_source_research.db.research_models import (
    ResearchJobRecord, ResearchRunStepRecord, RunEventRecord,
)
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.record_store import RecordRepository
from water_source_research.services.review import ReviewRepository

SNAPSHOT_VERSION = "snapshot-2"  # + raw discovery results

def build_snapshot(engine: Engine, run_id: str) -> dict[str, Any]:
    """Read (never write) everything the evaluation needs about ``run_id``."""
    with Session(engine) as session:
        job = session.get(ResearchJobRecord, run_id)
        if job is None:
            raise LookupError(f"unknown research run {run_id}")
        origin_id = job.parent_run_id if job.mode == "REPROCESS" and job.parent_run_id \
            else run_id
        origin = session.get(ResearchJobRecord, origin_id)
        result = job.result or {}
        origin_result = (origin.result if origin is not None else None) or {}
        events = [_event(e) for e in session.scalars(select(RunEventRecord).where(
            RunEventRecord.run_id == origin_id).order_by(RunEventRecord.seq))]
        steps = [{"run_id": s.run_id, "attempt": s.attempt, "seq": s.seq, "node": s.node,
                  "duration_ms": s.duration_ms}
                 for s in session.scalars(select(ResearchRunStepRecord).where(
                     ResearchRunStepRecord.run_id.in_({run_id, origin_id}))
                     .order_by(ResearchRunStepRecord.run_id, ResearchRunStepRecord.attempt,
                               ResearchRunStepRecord.seq))]
        bundles, bundle_basis = _bundles(session, origin, origin_result)
        job_dict = _job(job)
        origin_dict = _job(origin) if origin is not None else None
    evidence = EvidenceRepository(engine)
    reviews = ReviewRepository(engine)
    records = RecordRepository(engine).load(run_id)
    snap: dict[str, Any] = {
        "version": SNAPSHOT_VERSION,
        "run": job_dict,
        "origin": origin_dict,
        "unresolved_fields": result.get("unresolved_fields")
        or origin_result.get("unresolved_fields"),
        "fields_needing_evidence": result.get("fields_needing_evidence") or [],
        "claims": evidence.claims(run_id),
        "field_states": evidence.field_states(run_id),
        "resolutions": evidence.resolutions(run_id),
        "review_items": [{k: v for k, v in i.items() if k != "payload"}
                         for i in reviews.items(run_id)],
        "review_decisions": reviews.decisions(run_id),
        "records": records.model_dump(mode="json"),
        "quality": (result.get("record_set") or {}).get("quality", []),
        "documents": evidence.documents(run_id),
        "chunks": evidence.chunks(run_id, limit=100_000),
        "discovery_candidates": origin_result.get("discovery_candidates") or [],
        # every hit the recorded searches returned (runs before migration 0013: none)
        "discovery_results": evidence.discovery_results(origin_id),
        "discovery_rounds": origin_result.get("discovery_rounds"),
        "events": events,
        "steps": steps,
        "context_bundles": bundles,
        "context_bundle_basis": bundle_basis,
    }
    return snap

def snapshot_sha256(snap: dict[str, Any]) -> str:
    canonical = json.dumps(snap, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _job(j: ResearchJobRecord) -> dict[str, Any]:
    return {"run_id": j.run_id, "city_name": j.city_name, "country": j.country,
            "status": j.status, "mode": j.mode, "parent_run_id": j.parent_run_id,
            "attempts": j.attempts, "review_round": j.review_round,
            "completion": j.completion, "field_keys": j.field_keys,
            "created_at": _iso(j.created_at), "started_at": _iso(j.started_at),
            "finished_at": _iso(j.finished_at), "last_error": j.last_error}

def _event(e: RunEventRecord) -> dict[str, Any]:
    return {"seq": e.seq, "kind": e.kind, "node": e.node, "field_key": e.field_key,
            "capability": e.capability, "provider": e.provider, "strategy": e.strategy,
            "result": e.result, "attrs": e.attrs, "created_at": _iso(e.created_at)}

def _bundles(session: Session, origin: ResearchJobRecord | None,
             origin_result: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Context bundles the origin run used. Only the last bundle per field is linked to the
    run (``context_bundle_ids``); earlier attempts are matched by city and the run's time
    window (bundles carry no run id) — reported as an instrumentation gap."""
    if origin is None:
        return [], "none"
    linked = set((origin_result.get("context_bundle_ids") or {}).values())
    window = and_(ContextBundleRecord.city_name == origin.city_name,
                  or_(ContextBundleRecord.created_at.between(origin.started_at,
                                                             origin.finished_at),
                      ContextBundleRecord.last_used_at.between(origin.started_at,
                                                               origin.finished_at))) \
        if origin.started_at and origin.finished_at else ContextBundleRecord.id.in_(linked)
    rows = session.scalars(select(ContextBundleRecord).where(
        or_(ContextBundleRecord.id.in_(linked), window))
        .order_by(ContextBundleRecord.created_at)).all()
    out = []
    for b in rows:
        items = [{"kind": i.get("kind"), "source_id": i.get("source_id"),
                  "chunk_id": i.get("chunk_id"), "field_keys": i.get("field_keys") or [],
                  "score": i.get("score")} for i in (b.payload or {}).get("items", [])]
        sources = {sid: {"url": s.get("url"), "page_number": s.get("page_number")}
                   for sid, s in ((b.payload or {}).get("sources") or {}).items()}
        out.append({"id": b.id, "purpose": b.purpose, "target_fields": b.target_fields,
                    "created_at": _iso(b.created_at), "linked": b.id in linked,
                    "stats": (b.payload or {}).get("stats") or {}, "items": items,
                    "sources": sources})
    return out, "linked+time-window" if len(out) > len(linked) else "linked"

def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None

# --- stage views ------------------------------------------------------------------------------

def raw_claims(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Claims as the extraction produced them (reviewer corrections are not predictions)."""
    return [c for c in snap["claims"] if c.get("review_status") != "CORRECTED"
            and c.get("claim_uid")]

def auto_decisions(snap: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """(field, entity) -> the rules' last decision before the first reviewer decision."""
    first_review = min((r["seq"] for r in snap["resolutions"]
                        if r["decided_by"] == "REVIEWER"), default=None)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in snap["resolutions"]:
        if r["decided_by"] != "RULES":
            continue
        if first_review is not None and r["seq"] > first_review:
            continue
        out[(r["field_key"], r["entity_key"])] = r
    return out

def has_final(snap: dict[str, Any]) -> bool:
    run = snap["run"]
    return run["status"] == "SUCCEEDED" and run.get("completion") not in (None, "INCOMPLETE") \
        and bool(snap["records"].get("records"))

def value_origin(rv: dict[str, Any]) -> str:
    """Where a final value came from: REVIEWER, DERIVED, CATALOG, RULES or GAP."""
    if rv.get("review_decision_ids"):
        return "REVIEWER"
    if rv.get("calculation"):
        return "DERIVED"
    if rv.get("resolution_status") == "FROM_CITY_CATALOG":
        return "CATALOG"
    if rv.get("value_status") == "KNOWN":
        return "RULES"
    return "GAP"
