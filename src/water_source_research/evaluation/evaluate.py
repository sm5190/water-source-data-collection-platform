"""Evaluate one research run against a reference dataset: per reference cell (city-level
field, or source-level field of one reference record) at three stages, plus evidence checks,
failure diagnoses and metrics. Pure over the snapshot; persistence is the caller's
(``run_evaluation`` does both).

Reference records are paired with the run's water sources by name (and configured aliases);
reference sources the run didn't find, and run sources the reference doesn't list, are
reported, never forced into a pair.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.field_state import load_field_definitions
from water_source_research.services.normalization import years_in
from water_source_research.services.resolution import ALL_RECORDS, CITY, name_key
from water_source_research.services.review import OFFICIAL_DEFAULT, proposal_key
from .compare import Comparison, Prediction, compare, entity_keys
from .diagnosis import Diagnoser
from .evidence import check_evidence
from .judge import JUDGE_PROMPT_VERSION, SemanticJudge, eligible
from .metrics import compute_metrics
from .policy import ComparePolicy, load_policy
from .reference import ReferenceCity, ReferenceRecord, ReferenceValue
from .snapshot import (
    auto_decisions, build_snapshot, has_final, raw_claims, snapshot_sha256, value_origin,
)

# RAW: every candidate (a cell scores its best one: "any-candidate recall", Advanced only).
# PROPOSED: the one value the system puts forward per cell (auto-accepted, else the proposed
# candidate). AUTO: only what the rules accepted. FINAL: after human review.
STAGES = ("RAW", "PROPOSED", "AUTO", "FINAL")
RANK = {"MATCH": 0, "PARTIAL_MATCH": 1, "SCOPE_MISMATCH": 2, "TEMPORAL_MISMATCH": 2,
        "NEEDS_REVIEW": 3, "MISMATCH": 4, "MISSING_PREDICTION": 5}
ENTITY = "water_source"
# Telemetry the evaluator needs but the research pipeline doesn't record (reported per run).
INSTRUMENTATION_GAPS = [
    {"code": "RAW_SEARCH_RESULTS", "detail": "this run's raw search results weren't recorded "
     "(runs before migration 0013): 'not returned' vs 'ranked out' can't be told apart"},
    {"code": "BUNDLE_PER_ATTEMPT", "detail": "only the last context bundle per field is linked "
     "to the run; earlier attempts are matched by city and time window"},
    {"code": "LLM_RAW_OUTPUT", "detail": "raw model responses and rejected citations/claims "
     "are not persisted (only counts in tracing spans)"},
    {"code": "SOURCE_TYPE_REASON", "detail": "why a document got its source type is not "
     "recorded"},
    {"code": "CHUNK_SCORES", "detail": "scores of chunks not selected into a bundle are not "
     "stored (only counts)"},
    {"code": "DYNAMIC_CONTENT", "detail": "acquisition doesn't record whether a page had "
     "script-rendered data that static fetching missed"},
]

def evaluate_snapshot(snap: dict[str, Any], ref_city: ReferenceCity,
                      fields: Mapping[str, FieldDefinition] | None = None,
                      policy: ComparePolicy | None = None,
                      judge: SemanticJudge | None = None) -> dict[str, Any]:
    """``judge``: the semantic judge for cells the deterministic comparison can't decide
    (None = deterministic only)."""
    fields = fields or load_field_definitions()
    policy = policy or load_policy()
    stages = ["RAW", "PROPOSED", "AUTO"] + (["FINAL"] if has_final(snap) else [])
    claims = raw_claims(snap)
    auto = auto_decisions(snap)
    records = snap["records"]
    pairing = {
        "RAW": _pair(ref_city.records, _raw_sources(claims), policy),
        "AUTO": _pair(ref_city.records, _auto_sources(auto, snap), policy),
    }
    pairing["PROPOSED"] = pairing["RAW"]
    if "FINAL" in stages:
        pairing["FINAL"] = _pair(ref_city.records, [
            (r["record_key"], r.get("water_source") or "") for r in records["records"]], policy)

    researched = set(snap["run"].get("field_keys") or fields)
    results: list[dict[str, Any]] = []
    for scope, record, d in _cells(ref_city, fields):
        ref = ref_city.value(d.key, record.record_key if record else "")
        rkey = record.record_key if record else ""
        for stage in stages:
            if stage != "FINAL" and (d.derived or d.generated):
                continue
            pred_key = pairing[stage]["pairs"].get(rkey) if record else None
            if stage == "RAW":
                r = _raw_cell(d, ref, claims, pred_key, scope, ref_city, policy)
            elif stage == "PROPOSED":
                r = _proposed_cell(d, ref, auto, claims, pred_key, scope, ref_city, policy,
                                   pairing["AUTO"]["pairs"].get(rkey) if record else None)
            elif stage == "AUTO":
                r = _auto_cell(d, ref, auto, claims, pred_key, scope, ref_city, policy)
            else:
                r = _final_cell(d, ref, records, pred_key, scope, ref_city, policy)
            if d.key not in researched and r["status"] == "MISSING_PREDICTION":
                r = {**r, "status": "NOT_RESEARCHED",
                     "comparison": {**r["comparison"], "status": "NOT_RESEARCHED",
                                    "detail": "outside the run's researched field subset"}}
            results.append({"stage": stage, "scope": scope, "record_key": rkey,
                            "field_key": d.key, **r,
                            "reference": _ref_dict(ref)})

    judge_info = apply_judge(results, judge, snap) if judge is not None else None
    diagnoser = Diagnoser(snap, fields, policy)
    diagnoses = []
    by_cell: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for r in results:
        by_cell.setdefault((r["record_key"], r["field_key"]), {})[r["stage"]] = r
    for (rkey, fkey), stage_results in by_cell.items():
        ref = ref_city.value(fkey, rkey)
        if ref is None:
            continue
        diagnoses += diagnoser.diagnose(fkey, rkey, ref, stage_results,
                                        _row_urls(ref_city, rkey))
    diagnoses += _run_level_findings(snap, pairing)
    evidence = check_evidence(snap, fields)
    metrics = compute_metrics(results, diagnoses, evidence, snap, fields)
    gaps = [g for g in INSTRUMENTATION_GAPS if not (
        g["code"] == "RAW_SEARCH_RESULTS" and snap.get("discovery_results"))]
    return {"stages": stages, "pairing": pairing, "results": results,
            "diagnoses": diagnoses, "evidence": evidence, "metrics": metrics,
            "gaps": gaps, "evaluated_at": datetime.now(UTC).isoformat(),
            "judge": judge_info}

# --- semantic judge ---------------------------------------------------------------------------

JUDGED_STAGES = ("PROPOSED", "AUTO", "FINAL")

def apply_judge(results: list[dict[str, Any]], judge: SemanticJudge,
                snap: dict[str, Any]) -> dict[str, Any]:
    """Refine undecided cells (``judge.eligible``) with the semantic judge, in place. The
    deterministic status is kept next to the judged one; a cell the judge couldn't decide
    (model unavailable or failed) needs a person (NEEDS_REVIEW)."""
    claims = {c["claim_uid"]: c for c in snap["claims"] if c.get("claim_uid")}
    judged = unavailable = 0
    for r in results:
        if r["stage"] not in JUDGED_STAGES or not eligible(r):
            continue
        p, ref = r["predicted"], r["reference"]
        uids = p.get("selected_claim_uids") or p.get("claim_uids") or []
        selected = [claims[u] for u in uids if u in claims]
        year = p.get("reference_year") or next(
            (claim_prediction(c).reference_year for c in selected), None)
        # Years are compared in their own fields (e.g. water_quality_year): a year attached to
        # only one side must not make the same description look different.
        x = judge.make_input(
            r["field_key"], _text(ref.get("raw") if ref.get("raw") not in (None, "")
                                  else ref.get("normalized")),
            None, _text(p.get("value")), None,
            [x["quote"] for c in selected for x in c.get("citations") or [] if x.get("quote")])
        j = judge.judge(x)
        cmp = dict(r["comparison"] or {})
        cmp["deterministic_status"] = r["status"]
        if j is None:
            unavailable += 1
            reason = judge.unavailable or (judge.errors[-1] if judge.errors else "model error")
            cmp.update(status="NEEDS_REVIEW", tags=[*cmp.get("tags", []), "SEMANTIC_UNAVAILABLE"],
                       detail=f"semantic check unavailable ({reason}); needs a person")
        else:
            judged += 1
            out = j["output"]
            cmp.update(status=j["status"], judgment_id=j["judgment_id"],
                       detail=out["justification"],
                       judgment={k: out[k] for k in (
                           "verdict", "overlapping_facts", "missing_facts", "differing_facts",
                           "same_meaning_of_measure", "same_geographic_scope",
                           "same_reporting_period", "justification")}
                       | {"model": j["model"], "provider": j["provider"],
                          "prompt_version": j["prompt_version"], "cached": j["cached"]})
        r["comparison"] = cmp
        r["status"] = cmp["status"]
    return {"prompt_version": JUDGE_PROMPT_VERSION, "provider": judge.provider,
            "model": judge.model, "judged": judged, "unavailable_cells": unavailable,
            "new_calls": judge.calls, "errors": judge.errors[-5:],
            "unavailable": judge.unavailable}

def _text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)

# --- cells and pairing -----------------------------------------------------------------------

def _cells(city: ReferenceCity, fields: Mapping[str, FieldDefinition]):
    """(scope, reference record or None, field) for every evaluated reference cell."""
    for d in fields.values():
        if d.generated:
            continue
        if d.scope in ("city", "time_series"):
            yield "CITY", None, d
        else:
            for rec in city.records:
                yield "RECORD", rec, d

def _raw_sources(claims: list[dict[str, Any]]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for c in claims:
        if c["field_key"] == ENTITY and c.get("validation_outcome") != "INVALID":
            label = str(c.get("normalized_value") or c.get("value") or "")
            seen.setdefault(name_key(label), label)
    return list(seen.items())

def _auto_sources(auto: dict[tuple[str, str], dict[str, Any]],
                  snap: dict[str, Any]) -> list[tuple[str, str]]:
    claims = {c["claim_uid"]: c for c in snap["claims"] if c.get("claim_uid")}
    out = []
    for (field, entity), r in auto.items():
        if field == ENTITY and r["to_status"] == "RESOLVED" and r["selected_claim_uids"]:
            c = claims.get(r["selected_claim_uids"][0]) or {}
            out.append((entity, str(c.get("normalized_value") or c.get("value") or entity)))
    return out

def _pair(records: list[ReferenceRecord], predicted: list[tuple[str, str]],
          policy: ComparePolicy) -> dict[str, Any]:
    """Reference record -> predicted entity key, by name/alias only."""
    pairs: dict[str, str | None] = {}
    used: set[str] = set()
    for rec in records:
        keys = entity_keys(rec.water_source or "", policy) if rec.water_source else set()
        hit = next((k for k, label in predicted if k not in used
                    and (entity_keys(label, policy) & keys)), None)
        if hit is None and rec.record_key == "unknown" and len(predicted) == 1:
            hit = predicted[0][0]  # the reference doesn't know the source; the run found one
        pairs[rec.record_key] = hit
        if hit:
            used.add(hit)
    return {"pairs": pairs,
            "missing": [r.record_key for r in records if pairs.get(r.record_key) is None],
            "extra": [{"key": k, "label": label} for k, label in predicted if k not in used]}

# --- stages -----------------------------------------------------------------------------------

def claim_prediction(c: dict[str, Any]) -> Prediction:
    q = c.get("qualifiers") or {}
    v = c.get("validation") or {}
    status = {"KNOWN": "KNOWN", "NOT_APPLICABLE": "NOT_APPLICABLE"}.get(c["value_status"],
                                                                         "UNKNOWN")
    quotes = " ".join(x.get("quote") or "" for x in c.get("citations") or [])
    year = q.get("reference_year") if q.get("year_basis") in ("quote", "passage") else None
    return Prediction(
        value=c["normalized_value"] if c.get("normalized_value") is not None else c["value"],
        value_status=status, reference_year=year,
        candidate_years=years_in(quotes, datetime.now(UTC).year + 1),
        flags=[k["code"] for k in v.get("checks", []) if k["result"] in ("WARN", "FAIL")],
        claim_uid=c.get("claim_uid"))

def _claims_for(field_key: str, claims: list[dict[str, Any]], pred_key: str | None,
                scope: str, city: ReferenceCity) -> list[dict[str, Any]]:
    mine = [c for c in claims if c["field_key"] == field_key]
    if scope == "CITY" or len(city.records) == 1:
        return mine
    if field_key == ENTITY:
        return [c for c in mine if pred_key and name_key(str(
            c.get("normalized_value") or c.get("value"))) == pred_key]
    allowed = {pred_key, ALL_RECORDS, None, "unattributed"} if pred_key else \
        {ALL_RECORDS, None, "unattributed"}
    return [c for c in mine if c.get("entity_key") in allowed]

def _raw_cell(d: FieldDefinition, ref: ReferenceValue | None, claims: list[dict[str, Any]],
              pred_key: str | None, scope: str, city: ReferenceCity,
              policy: ComparePolicy) -> dict[str, Any]:
    mine = _claims_for(d.key, claims, pred_key, scope, city)
    lat = _other_lat(d, ref, city)
    candidates = []
    for c in mine:
        cmp = compare(d, ref, claim_prediction(c), policy, lat)
        candidates.append({
            "claim_uid": c["claim_uid"], "value": c.get("normalized_value", c["value"]),
            "original_value": c.get("original_value", c["value"]),
            "confidence": c["confidence"], "validation": c.get("validation_outcome"),
            "fail_codes": [k["code"] for k in (c.get("validation") or {}).get("checks", [])
                           if k["result"] == "FAIL"],
            "entity_key": c.get("entity_key"),
            "sources": [(x["url"], x.get("source_type")) for x in c.get("citations") or []],
            **cmp.model_dump(mode="json")})
    if candidates:
        best = min(candidates, key=lambda x: RANK.get(x["status"], 9))
        top = max(candidates, key=lambda x: x["confidence"])
        comparison = {k: best[k] for k in Comparison.model_fields}
        status = best["status"]
    else:
        comparison = compare(d, ref, None, policy, lat).model_dump(mode="json")
        status, top = comparison["status"], None
    return {"status": status, "comparison": comparison,
            "predicted": {"candidates": candidates,
                          "top_candidate": top["claim_uid"] if top else None,
                          "top_candidate_status": top["status"] if top else None,
                          "paired_source": pred_key},
            "links": {"claim_uids": [c["claim_uid"] for c in candidates]}}

def _auto_cell(d: FieldDefinition, ref: ReferenceValue | None,
               auto: dict[tuple[str, str], dict[str, Any]], claims: list[dict[str, Any]],
               pred_key: str | None, scope: str, city: ReferenceCity,
               policy: ComparePolicy) -> dict[str, Any]:
    keys = [CITY, ALL_RECORDS] if scope == "CITY" else \
        ([pred_key] if pred_key else []) + [ALL_RECORDS] + \
        (["unattributed"] if len(city.records) == 1 else [])
    if d.key == ENTITY:
        keys = [pred_key] if pred_key else []
    decision = next((auto[(d.key, k)] for k in keys if (d.key, k) in auto), None)
    by_uid = {c["claim_uid"]: c for c in claims}
    lat = _other_lat(d, ref, city)
    if decision is None:
        outcome, pred = "NO_DECISION", None
    elif decision["to_status"] == "RESOLVED":
        selected = [by_uid[u] for u in decision["selected_claim_uids"] if u in by_uid]
        outcome = "RESOLVED"
        if not selected:
            pred = None
        elif d.multi_valued and len(selected) > 1:
            pred = claim_prediction(selected[0]).model_copy(update={
                "value": [s.get("normalized_value") or s["value"] for s in selected]})
        else:
            pred = claim_prediction(selected[0])
    else:
        outcome, pred = "ESCALATED", None
    cmp = compare(d, ref, pred, policy, lat)
    if outcome != "RESOLVED" and cmp.status not in ("MISSING_REFERENCE", "NOT_COMPARABLE"):
        # No automatic answer is not an answer of "N/A": nothing to score as correct.
        cmp = Comparison(status="MISSING_PREDICTION",
                         detail=f"no automatic value ({outcome.lower().replace('_', ' ')})")
    return {"status": cmp.status, "comparison": cmp.model_dump(mode="json"),
            "predicted": {"outcome": outcome, "value": pred.value if pred else None,
                          "claim_uids": decision["selected_claim_uids"] if decision else [],
                          "rule": decision["rule"] if decision else None,
                          "to_status": decision["to_status"] if decision else None,
                          "reason_codes": decision["reason_codes"] if decision else [],
                          "entity_key": decision["entity_key"] if decision else None},
            "links": {"resolution_seq": decision["seq"] if decision else None}}

def _proposed_cell(d: FieldDefinition, ref: ReferenceValue | None,
                   auto: dict[tuple[str, str], dict[str, Any]], claims: list[dict[str, Any]],
                   pred_key: str | None, scope: str, city: ReferenceCity, policy: ComparePolicy,
                   auto_key: str | None) -> dict[str, Any]:
    """The one value the system puts forward for a cell, before any person: the value the
    rules accepted, else the proposed candidate (``proposal_order``; confidence never used).
    Every candidate stays listed with its own comparison; only the selected one is scored."""
    raw = _raw_cell(d, ref, claims, pred_key, scope, city, policy)
    candidates = raw["predicted"]["candidates"]
    accepted = _auto_cell(d, ref, auto, claims, auto_key or pred_key, scope, city, policy)
    by_uid = {c["claim_uid"]: c for c in claims}
    if accepted["predicted"]["outcome"] == "RESOLVED":
        uids = accepted["predicted"]["claim_uids"]
        cmp = accepted["comparison"]
        source, reason = "AUTO_ACCEPTED", f"accepted by the rules ({accepted['predicted']['rule']})"
        value = accepted["predicted"]["value"]
    else:
        mine = proposal_order(d, _claims_for(d.key, claims, pred_key, scope, city))
        if not mine:
            uids, value, source, reason = [], None, None, "no candidate was extracted"
            cmp = compare(d, ref, None, policy, _other_lat(d, ref, city)).model_dump(mode="json")
            if cmp["status"] not in ("MISSING_REFERENCE", "NOT_COMPARABLE"):
                # Nothing extracted is not an answer of N/A (the methodology has no blanks).
                cmp = Comparison(status="MISSING_PREDICTION",
                                 detail="no candidate was extracted").model_dump(mode="json")
        else:
            usable = [c for c in mine if c.get("validation_outcome") != "INVALID"]
            chosen = (usable if d.multi_valued and usable else mine)[: None if d.multi_valued
                                                                     else 1]
            uids = [c["claim_uid"] for c in chosen]
            pred = claim_prediction(chosen[0])
            if d.multi_valued and len(chosen) > 1:
                pred = pred.model_copy(update={"value": list(dict.fromkeys(
                    claim_prediction(c).value for c in chosen))})
            value = pred.value
            cmp = compare(d, ref, pred, policy, _other_lat(d, ref, city)).model_dump(mode="json")
            source = "PROPOSED"
            reason = _selection_reason(chosen[0], len(mine), d)
    selected = [by_uid[u] for u in uids if u in by_uid]
    return {"status": cmp["status"], "comparison": cmp,
            "predicted": {"source": source, "value": value, "selected_claim_uids": uids,
                          "selection_reason": reason, "candidate_count": len(candidates),
                          "reference_year": next((claim_prediction(c).reference_year
                                                  for c in selected), None),
                          "citations": [{"url": x["url"], "title": x.get("title"),
                                         "quote": x.get("quote"),
                                         "page_number": x.get("page_number"),
                                         "source_type": x.get("source_type")}
                                        for c in selected for x in c.get("citations") or []],
                          "candidates": candidates},
            "links": {"claim_uids": uids, "candidate_claim_uids": raw["links"]["claim_uids"]}}

def proposal_order(d: FieldDefinition, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates in the order the review screen proposes them (services/review.py)."""
    official = set(d.official_sources) if d.official_sources else set(OFFICIAL_DEFAULT)

    def key(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
        n, c = pair
        checks = {k["code"]: k["result"] for k in (c.get("validation") or {}).get("checks", [])}
        types = {x.get("source_type") for x in c.get("citations") or []} | set(
            (c.get("qualifiers") or {}).get("source_types") or [])
        return proposal_key(c.get("validation_outcome"), types, checks.get("VALUE_IN_QUOTE"),
                            official, n)
    return [c for _, c in sorted(enumerate(claims), key=key)]

def _selection_reason(c: dict[str, Any], n: int, d: FieldDefinition) -> str:
    if n == 1:
        return "the only candidate"
    official = set(d.official_sources) if d.official_sources else set(OFFICIAL_DEFAULT)
    parts = ["passed validation" if c.get("validation_outcome") != "INVALID"
             else "no candidate passed validation"]
    if {x.get("source_type") for x in c.get("citations") or []} & official:
        parts.append("official source")
    return f"first of {n} candidates: " + ", ".join(parts)

def _final_cell(d: FieldDefinition, ref: ReferenceValue | None, records: dict[str, Any],
                pred_key: str | None, scope: str, city: ReferenceCity,
                policy: ComparePolicy) -> dict[str, Any]:
    by_source = {r["source_key"]: r["record_key"] for r in records["records"]}
    rv = None
    if scope == "CITY" or d.scope in ("city", "time_series"):
        rv = records["city_values"].get(d.key)
    elif pred_key is not None:
        rk = by_source.get(pred_key, pred_key)
        rv = (records["record_values"].get(rk) or {}).get(d.key)
    if rv is None or rv.get("resolution_status") == "NEEDS_RESEARCH":
        pred = None  # rejected by the reviewer, nothing chosen: no answer (not an Unknown)
    else:
        status = {"KNOWN": "KNOWN", "NOT_APPLICABLE": "NOT_APPLICABLE"}.get(
            rv["value_status"], "UNKNOWN")
        pred = Prediction(value=rv.get("value"), value_status=status,
                          reference_year=rv.get("reference_year"))
    cmp = compare(d, ref, pred, policy, _other_lat(d, ref, city))
    return {"status": cmp.status, "comparison": cmp.model_dump(mode="json"),
            "predicted": {"value": rv.get("value") if rv else None,
                          "value_status": rv.get("value_status") if rv else None,
                          "resolution_status": rv.get("resolution_status") if rv else None,
                          "reference_year": rv.get("reference_year") if rv else None,
                          "origin": value_origin(rv) if rv else None,
                          "review_decision_ids": rv.get("review_decision_ids") if rv else [],
                          "calculation": rv.get("calculation") if rv else None,
                          "claim_uids": rv.get("claim_uids") if rv else []},
            "links": {"record_key": rv.get("record_key") if rv else None}}

def _other_lat(d: FieldDefinition, ref: ReferenceValue | None,
               city: ReferenceCity) -> float | None:
    if not d.key.endswith("longitude"):
        return None
    lat_key = d.key.replace("longitude", "latitude")
    lat = city.value(lat_key, ref.record_key if ref else "")
    return float(lat.normalized) if lat is not None and lat.value_status == "KNOWN" else None

def _row_urls(city: ReferenceCity, record_key: str) -> list[str]:
    """Every source URL of the reference row (all rows for a city-level field)."""
    records = [r for r in city.records if r.record_key == record_key] or city.records
    return list(dict.fromkeys(u for r in records for urls in r.references.values()
                              for u in urls))

def _ref_dict(ref: ReferenceValue | None) -> dict[str, Any]:
    return ref.model_dump(mode="json") if ref else {}

def _run_level_findings(snap: dict[str, Any], pairing: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings not tied to one field: suspicious source types, unpaired sources."""
    from .evidence import suspicious_official
    out = []
    for doc in snap["documents"]:
        why = suspicious_official(doc.get("requested_url") or "", doc.get("source_type"))
        if why:
            out.append({"stage": "RUN", "record_key": "", "field_key": "",
                        "root_cause": "UNKNOWN", "earliest_stage": "discovery",
                        "confidence": "UNKNOWN", "symptoms": ["SOURCE_CLASSIFICATION_ERROR"],
                        "probes": [{"probe": "source_type", "observed": {
                            "document_id": doc["document_id"], "url": doc["requested_url"],
                            "source_type": doc["source_type"]}}],
                        "explanation": f"suspected source-type misclassification: {why}"})
    for stage, p in pairing.items():
        for e in p["extra"]:
            out.append({"stage": stage, "record_key": e["key"], "field_key": ENTITY,
                        "root_cause": "UNKNOWN", "earliest_stage": None,
                        "confidence": "UNKNOWN", "symptoms": ["EXTRA_SOURCE"],
                        "probes": [{"probe": "pairing", "observed": e}],
                        "explanation": f"the run has a water source the reference doesn't "
                                       f"list: {e['label']!r} (a reference gap or a wrong "
                                       f"source — needs adjudication)"})
    return out

def run_evaluation(engine, research_run_id: str, dataset_id: str | None = None,
                   policy: ComparePolicy | None = None,
                   judge: SemanticJudge | None = None) -> str:
    """Snapshot the research run (read-only), evaluate it against the reference dataset
    (default: the latest one containing its city) and store a new evaluation run."""
    from .store import EvaluationStore
    store = EvaluationStore(engine)
    policy = policy or load_policy()
    snap = build_snapshot(engine, research_run_id)
    run = snap["run"]
    dataset_id = dataset_id or store.latest_dataset_with(run["city_name"], run["country"])
    if dataset_id is None:
        raise LookupError(f"no reference dataset contains {run['city_name']}; import one first")
    ds = store.load_dataset(dataset_id)
    if ds is None:
        raise LookupError(f"unknown reference dataset {dataset_id}")
    city = ds.city(run["city_name"], run["country"])
    if city is None:
        raise LookupError(f"reference dataset {dataset_id} has no {run['city_name']}")
    evaluation = evaluate_snapshot(snap, city, policy=policy, judge=judge)
    origin = (snap.get("origin") or snap["run"])["run_id"]
    return store.save_run(research_run_id, origin, dataset_id, city.city_key, snap,
                          snapshot_sha256(snap), evaluation, policy.version,
                          config={"reference_policy": ds.policy_version,
                                  "snapshot_version": snap["version"],
                                  "pairing": evaluation["pairing"],
                                  "judge": evaluation["judge"]})
