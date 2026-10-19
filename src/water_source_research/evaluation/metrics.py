"""Evaluation metrics with explicit numerators, denominators and exclusions.

Unit: a comparable reference cell (city-level field, or source-level field of a reference
record). Not comparable — excluded from every denominator, and counted — are cells whose
reference is Unknown/blank (MISSING_REFERENCE) or a placeholder/invalid (NOT_COMPARABLE),
and fields outside the run's researched subset that it produced nothing for (NOT_RESEARCHED).
NEEDS_REVIEW cells stay in denominators but are never counted correct until a person
adjudicates them; their number is reported next to each rate.

Value accuracy, coverage and evidence validity are separate numbers. Automatic-resolution
precision is always reported with its coverage (4/4 correct at 4/27 coverage is not "100%").
"""

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from water_source_research.schemas.research import FieldDefinition
from .compare import NOT_SCORED
WRONG = {"MISMATCH", "SCOPE_MISMATCH", "TEMPORAL_MISMATCH"}

def metric(scope: str, key: str, name: str, num: float | None, den: float | None,
           **detail: Any) -> dict[str, Any]:
    value = (num / den) if num is not None and den else None
    return {"scope": scope, "scope_key": key, "metric": name, "numerator": num,
            "denominator": den, "value": round(value, 4) if value is not None else None,
            "detail": detail}

def compute_metrics(results: list[dict[str, Any]], diagnoses: list[dict[str, Any]],
                    evidence: list[dict[str, Any]], snap: dict[str, Any],
                    fields: Mapping[str, FieldDefinition]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_stage.setdefault(r["stage"], []).append(r)
    for stage, rows in by_stage.items():
        out += _stage(stage, rows, diagnoses)
        out += _by_field_type(stage, rows, fields)
    out += _evidence(evidence)
    out += _diagnoses(diagnoses)
    out += _reliability(snap)
    return out

def _stage(stage: str, rows: list[dict[str, Any]], diagnoses: list[dict[str, Any]]
           ) -> list[dict[str, Any]]:
    scored = [r for r in rows if r["status"] not in NOT_SCORED]
    excluded = Counter(r["status"] for r in rows if r["status"] in NOT_SCORED)
    c = len(scored)
    review = sum(r["status"] == "NEEDS_REVIEW" for r in scored)
    common = {"excluded": dict(excluded), "needs_review": review}
    out = [metric("stage", stage, "cells", c, None, **common)]
    if stage == "RAW":
        covered = [r for r in scored if r["predicted"]["candidates"]]
        match = [r for r in covered if r["status"] == "MATCH"]
        top = [r for r in covered if r["predicted"]["top_candidate_status"] == "MATCH"]
        candidates = [x for r in rows for x in r["predicted"]["candidates"]]
        off_field = [x for x in candidates
                     if {"VALUE_WHERE_NOT_APPLICABLE", "NO_FIELD_KEYWORDS"} & set(x["tags"])
                     or (x["status"] == "NEEDS_REVIEW" and x.get("coverage") == 0)]
        with_evidence = {(d["record_key"], d["field_key"]) for d in diagnoses
                         if d["stage"] == "RAW" and any(
                             p["observed"].get("chunks_with_value_count") for p in d["probes"]
                             if p["probe"] == "evidence")}
        available = len(match) + len([r for r in scored if r["status"] != "MATCH"
                                      and (r["record_key"], r["field_key"]) in with_evidence])
        out += [
            metric("stage", stage, "coverage", len(covered), c, **common),
            metric("stage", stage, "accuracy", len(match), len(covered), **common),
            metric("stage", stage, "top_candidate_accuracy", len(top), len(covered)),
            metric("stage", stage, "missed", c - len(covered), c),
            metric("stage", stage, "evidence_adjusted_recall", len(match), available,
                   note="denominator: cells matched, plus cells whose reference value is in "
                        "the acquired evidence"),
            metric("stage", stage, "off_field_claim_rate", len(off_field), len(candidates)),
        ]
    elif stage == "AUTO":
        resolved = [r for r in scored if r["predicted"]["outcome"] == "RESOLVED"]
        correct = [r for r in resolved if r["status"] == "MATCH"]
        wrong = [r for r in resolved if r["status"] in WRONG]
        escalated = [r for r in scored if r["predicted"]["outcome"] == "ESCALATED"]
        unnecessary = [d for d in diagnoses if d["root_cause"] == "UNNECESSARY_ESCALATION"]
        out += [
            metric("stage", stage, "coverage", len(resolved), c, **common),
            metric("stage", stage, "precision", len(correct), len(resolved),
                   coverage=f"{len(resolved)}/{c}"),
            metric("stage", stage, "wrong_acceptance_rate", len(wrong), len(resolved)),
            metric("stage", stage, "escalation_rate", len(escalated), c),
            metric("stage", stage, "unnecessary_escalation_rate", len(unnecessary),
                   len(escalated)),
        ]
    elif stage == "FINAL":
        known = [r for r in scored if r["predicted"].get("value_status") == "KNOWN"]
        match = [r for r in known if r["status"] == "MATCH"]
        reviewed = [r for r in scored if r["predicted"].get("review_decision_ids")]
        derived = [r for r in scored if r["predicted"].get("origin") == "DERIVED"]
        na = [r for r in scored if r["reference"].get("value_status") == "NOT_APPLICABLE"]
        out += [
            metric("stage", stage, "accuracy", len(match), len(known), **common),
            metric("stage", stage, "coverage", len(known), c),
            metric("stage", stage, "reviewer_decided_rate", len(reviewed), c),
            metric("stage", stage, "reviewer_decided_accuracy",
                   sum(r["status"] == "MATCH" for r in reviewed), len(reviewed)),
            metric("stage", stage, "derived_accuracy",
                   sum(r["status"] == "MATCH" for r in derived), len(derived)),
            metric("stage", stage, "not_applicable_represented",
                   sum(r["status"] == "MATCH" for r in na), len(na)),
        ]
    return out

def _by_field_type(stage: str, rows: list[dict[str, Any]],
                   fields: Mapping[str, FieldDefinition]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["status"] in NOT_SCORED:
            continue
        groups.setdefault(fields[r["field_key"]].field_type, []).append(r)
    return [metric("field_type", f"{stage}:{t}", "match_rate",
                   sum(r["status"] == "MATCH" for r in g), len(g)) for t, g in groups.items()]

def _evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    checks: dict[str, list[str]] = {}
    for e in evidence:
        checks.setdefault(e["check"], []).append(e["result"])
    for check, results in sorted(checks.items()):
        counted = [r for r in results if r != "N/A"]
        out.append(metric("evidence", check, "pass_rate", counted.count("PASS"), len(counted),
                          results=dict(Counter(results))))
    return out

def _diagnoses(diagnoses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    causes = Counter(d["root_cause"] for d in diagnoses if d["stage"] != "RUN")
    symptoms = Counter(s for d in diagnoses for s in d["symptoms"])
    total = sum(causes.values())
    out = [metric("root_cause", k, "count", v, total) for k, v in sorted(causes.items())]
    out += [metric("symptom", k, "count", v, None) for k, v in sorted(symptoms.items())]
    return out

def _reliability(snap: dict[str, Any]) -> list[dict[str, Any]]:
    docs = snap["documents"]
    statuses = Counter(d["status"] for d in docs)
    errors = Counter(d.get("error_kind") for d in docs if d.get("error_kind"))
    events = snap["events"]
    discover = [e for e in events if e["kind"] == "TOOL_CALL" and e["node"] == "discover"]
    extract = [e for e in events if e["kind"] == "NODE" and e["node"] == "extract"]
    attrs = [e["attrs"] or {} for e in extract]
    origin = snap.get("origin") or snap["run"]
    out = [
        metric("reliability", "acquisition", "documents_ok", statuses.get("OK", 0), len(docs),
               statuses=dict(statuses), error_kinds=dict(errors)),
        metric("reliability", "discovery", "calls_succeeded",
               sum(e["result"] == "SUCCESS" for e in discover), len(discover),
               results=dict(Counter(e["result"] for e in discover)),
               candidates_retained=len(snap["discovery_candidates"])),
        metric("reliability", "extraction", "attempts_with_claims",
               sum(bool(a.get("claims")) for a in attrs if a.get("bundle_status") == "READY"),
               sum(a.get("bundle_status") == "READY" for a in attrs),
               bundle_status=dict(Counter(a.get("bundle_status") for a in attrs)),
               llm_requests=sum(a.get("llm_requests") or 0 for a in attrs),
               prompt_tokens=sum(a.get("prompt_tokens") or 0 for a in attrs),
               completion_tokens=sum(a.get("completion_tokens") or 0 for a in attrs),
               llm_ms=sum(a.get("duration_ms") or 0 for a in attrs)),
        metric("reliability", "run", "attempts", origin.get("attempts"), None,
               duration_s=_seconds(origin.get("started_at"), origin.get("finished_at"))),
    ]
    return out

def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(),
                 1)
