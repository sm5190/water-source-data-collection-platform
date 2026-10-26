"""Researcher-facing views of evaluations: manual vs automated values side by side for one city,
and research performance across cities. Pure over adjudicated views (``report.effective``);
nothing here changes stored results.

Stages shown: PROPOSED (what the system extracted and put forward), AUTO (what it accepted
without a person) and FINAL (after human review). Every one of the 32 standardized fields is
listed; a field a stage doesn't produce (derived fields before assembly, the generated
References column) is listed as not evaluated at that stage, never hidden.
"""

from collections import defaultdict
from collections.abc import Mapping
from typing import Any
from water_source_research.schemas.standardized import COLUMNS, HEADERS
from .metrics import GROUP, SIMPLE_STAGES, is_provisional, simple_scores
from .report import _show, effective, reference_text
from .store import EvaluationStore

STAGE_LABEL = {"PROPOSED": "Proposed", "AUTO": "Auto-accepted", "FINAL": "Final reviewed"}
SUB_LABEL = {"TEMPORAL_MISMATCH": "different year", "SCOPE_MISMATCH": "different area or measure"}
FIELD_ORDER = {key: n for n, (key, _) in enumerate(COLUMNS)}
JUDGE_VALIDATED = False  # set only after the Toronto calibration cases are reviewed and pass

def manual_text(ref: dict[str, Any]) -> str:
    """The manual value as a researcher would write it (no internal status codes)."""
    status = ref.get("value_status")
    if not ref or status in (None, "BLANK"):
        return ""
    if status == "KNOWN":
        return reference_text(ref)
    return {"NOT_APPLICABLE": "N/A", "UNKNOWN": "Unknown", "NONE_REPORTED": "None Reported",
            "PLACEHOLDER": f"{ref.get('raw')} (placeholder, not a value)"}.get(
                status, str(ref.get("raw") or ""))

def automated_text(r: dict[str, Any]) -> str:
    p = r["predicted"]
    stage = r["stage"]
    if stage == "PROPOSED":
        return "" if p.get("source") is None else _show(p.get("value"))
    if stage == "AUTO":
        if p.get("outcome") == "RESOLVED":
            return _show(p.get("value"))
        return "— (sent to review)" if p.get("outcome") == "ESCALATED" else ""
    if p.get("resolution_status") == "NEEDS_RESEARCH":
        return "Needs more research"
    status = p.get("value_status")
    if status == "KNOWN":
        return _show(p.get("value"))
    return {"NOT_APPLICABLE": "N/A", "UNKNOWN": "Unknown"}.get(status or "", "")

def _citations(r: dict[str, Any]) -> list[dict[str, Any]]:
    p = r["predicted"]
    if r["stage"] == "PROPOSED":
        return p.get("citations") or []
    if r["stage"] == "RAW":
        return []
    uids = set(p.get("claim_uids") or [])
    return [{"url": u, "source_type": t} for c in (p.get("candidates") or [])
            if c.get("claim_uid") in uids for u, t in c.get("sources") or []]

def city_comparison(view: dict[str, Any], stage: str) -> dict[str, Any]:
    """Rows (Field | Manual | Automated | Status) for one stage, plus every stage's summary."""
    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in view["results"]:
        by_stage[r["stage"]].append(r)
    stages = [s for s in SIMPLE_STAGES if s in view["run"]["stages"]]
    summaries = {s: {**simple_scores(by_stage[s]), "label": STAGE_LABEL[s]} for s in stages}
    labels = _record_labels(view["results"])
    proposed = {(r["record_key"], r["field_key"]): r for r in by_stage.get("PROPOSED", [])}
    present = {(r["record_key"], r["field_key"]): r for r in by_stage.get(stage, [])}
    record_keys = sorted({r["record_key"] for r in view["results"] if r["record_key"]}) or [""]
    rows = []
    for key, header in COLUMNS:
        cells = [(rk, present.get((rk, key))) for rk in [""] + record_keys
                 if (rk, key) in present] or [("", None)]
        for rk, r in cells:
            rows.append(_row(key, header, rk, labels.get(rk), r, proposed.get((rk, key)),
                             stage))
    return {"run": view["run"], "stage": stage, "stage_label": STAGE_LABEL.get(stage, stage),
            "stages": stages, "summaries": summaries, "rows": rows,
            "metrics_basis": view["metrics_basis"], "judge_validated": JUDGE_VALIDATED}

def _row(key: str, header: str, record_key: str, record_label: str | None,
         r: dict[str, Any] | None, proposed: dict[str, Any] | None,
         stage: str) -> dict[str, Any]:
    base = {"field_key": key, "field": header, "record_key": record_key,
            "record_label": record_label}
    if r is None:
        why = "generated from the sources at export" if key == "references" \
            else "calculated when records are assembled (shown in Final reviewed)"
        return {**base, "result_id": None, "manual": "", "automated": "",
                "status": "NOT_EVALUATED", "group": "NOT_EVALUATED", "sub_label": None,
                "provisional": False, "explanation": why, "details": None}
    cmp = r.get("comparison") or {}
    judgment = cmp.get("judgment") or {}
    p = r["predicted"]
    details = {
        "manual_year": r["reference"].get("reference_year"),
        "manual_sources": r["reference"].get("source_urls") or [],
        "automated_year": p.get("reference_year"),
        "citations": _citations(r),
        "source": p.get("source") or p.get("origin"),
        "selection_reason": p.get("selection_reason"),
        "candidate_count": p.get("candidate_count"),
        "candidates": [{"value": _show(c.get("value")), "status": c.get("status"),
                        "selected": c.get("claim_uid") in (p.get("selected_claim_uids") or []),
                        "sources": [u for u, _ in c.get("sources") or []]}
                       for c in ((proposed or {}).get("predicted") or {}).get("candidates")
                       or []],
        "judgment": judgment or None,
        "original_status": r.get("original_status"),
        "adjudication": r.get("adjudication"),
    }
    explanation = judgment.get("justification") or cmp.get("detail") or ""
    return {**base, "result_id": r["id"], "manual": manual_text(r["reference"]),
            "automated": automated_text(r), "status": r["status"],
            "group": GROUP.get(r["status"], "MISMATCH"),
            "sub_label": SUB_LABEL.get(r["status"]),
            "proposed_not_accepted": stage == "PROPOSED" and p.get("source") == "PROPOSED",
            "provisional": is_provisional(r), "explanation": explanation, "details": details}

def _record_labels(results: list[dict[str, Any]]) -> dict[str, str]:
    """Reference record key -> its water source name (from the reference)."""
    out = {}
    for r in results:
        if r["field_key"] == "water_source" and r["record_key"]:
            out.setdefault(r["record_key"], reference_text(r["reference"]) or r["record_key"])
    return out

# --- across cities --------------------------------------------------------------------------

def latest_views(store: EvaluationStore) -> list[dict[str, Any]]:
    """The adjudicated view of the latest completed evaluation per city."""
    seen: set[str] = set()
    out = []
    for run in store.runs():  # newest first
        if run["status"] != "COMPLETED" or run["city_key"] in seen:
            continue
        seen.add(run["city_key"])
        view = effective(store, run["id"])
        if view is not None:
            out.append(view)
    return out

def performance(store: EvaluationStore) -> dict[str, Any]:
    views = latest_views(store)
    cities = []
    pooled: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_field: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    field_cities: dict[tuple[str, str], set[str]] = defaultdict(set)
    judged = verified = agreed = 0
    for v in views:
        rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in v["results"]:
            if r["stage"] in SIMPLE_STAGES:
                rows[r["stage"]].append(r)
                pooled[r["stage"]].append(r)
                by_field[(r["stage"], r["field_key"])].append(r)
                field_cities[(r["stage"], r["field_key"])].add(v["run"]["city_key"])
            if (r.get("comparison") or {}).get("judgment_id"):
                judged += 1
                if r.get("adjudication"):
                    verified += 1
                    agreed += r["status"] == r["original_status"]
        cities.append({"city_key": v["run"]["city_key"], "eval_run_id": v["run"]["id"],
                       "research_run_id": v["run"]["research_run_id"],
                       "created_at": v["run"]["created_at"],
                       "metrics_basis": v["metrics_basis"],
                       "scores": {s: simple_scores(rows[s]) for s in rows}})
    stages = [s for s in SIMPLE_STAGES if pooled.get(s)]
    fields = []
    for key, header in COLUMNS:
        entry: dict[str, Any] = {"field_key": key, "field": header}
        for s in stages:
            sc = simple_scores(by_field.get((s, key), []))
            entry[s] = {**sc, "n_cities": len(field_cities.get((s, key), set()))}
        fields.append(entry)
    dataset = {}
    for s in stages:
        city_acc = [c["scores"][s]["accuracy_strict"] for c in cities
                    if s in c["scores"] and c["scores"][s]["accuracy_strict"] is not None]
        city_len = [c["scores"][s]["accuracy_lenient"] for c in cities
                    if s in c["scores"] and c["scores"][s]["accuracy_lenient"] is not None]
        pooled_scores = simple_scores(pooled[s])
        dataset[s] = {**pooled_scores, "n_cities": len(city_acc),
                      "mean_city_accuracy_strict": _mean(city_acc) if len(city_acc) >= 2
                      else None,
                      "mean_city_accuracy_lenient": _mean(city_len) if len(city_len) >= 2
                      else None,
                      "missing_data_rate": round(1 - pooled_scores["coverage"], 4)
                      if pooled_scores["coverage"] is not None else None}
    return {"cities": cities, "stages": stages, "stage_labels": STAGE_LABEL, "fields": fields,
            "dataset": dataset, "history": history(store),
            "judge": {"judged": judged, "verified": verified,
                      "agreement_with_people": round(agreed / verified, 4) if verified else None,
                      "provisional": judged - verified, "validated": JUDGE_VALIDATED}}

def history(store: EvaluationStore) -> list[dict[str, Any]]:
    """Every completed evaluation, oldest first, with its stored (as evaluated) scores."""
    out = []
    for run in reversed(store.runs()):
        if run["status"] != "COMPLETED":
            continue
        metrics = {(m["scope_key"], m["metric"]): m for m in store.metrics(run["id"])}
        scores = {}
        for s in SIMPLE_STAGES:
            if (s, "accuracy_strict") in metrics:
                scores[s] = {k: _frac(metrics.get((s, k)))
                             for k in ("accuracy_strict", "accuracy_lenient", "answer_coverage")}
        out.append({"eval_run_id": run["id"], "city_key": run["city_key"],
                    "created_at": run["created_at"],
                    "compare_policy_version": run["compare_policy_version"], "scores": scores})
    return out

def _frac(m: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if m is None:
        return None
    return {"numerator": m["numerator"], "denominator": m["denominator"], "value": m["value"]}

def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4)

def field_header(key: str) -> str:
    return HEADERS.get(key, key)
