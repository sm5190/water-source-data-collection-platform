"""The adjudicated view of an evaluation run, and its reports (JSON, CSV, Markdown).

Adjudications never change stored results: the view applies the latest adjudication per target
on read. A field result's status becomes the adjudicated one (CONFIRM_MATCH → MATCH,
CONFIRM_MISMATCH → MISMATCH, AMBIGUOUS → NEEDS_REVIEW, REFERENCE_ERROR → NOT_COMPARABLE, i.e.
excluded from scoring, OVERRIDE_STATUS → the given status); a diagnosis gets the corrected root
cause. When anything was adjudicated, metrics are recomputed from the evaluation's own frozen
snapshot, and every adjusted item keeps its original value next to the new one.
"""

import csv
import io
import json
from collections import Counter
from collections.abc import Mapping
from typing import Any
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.field_state import load_field_definitions
from .metrics import compute_metrics
from .store import EvaluationStore, is_revalidation

ADJUDICATED_STATUS = {"CONFIRM_MATCH": "MATCH", "CONFIRM_MISMATCH": "MISMATCH",
                      "AMBIGUOUS": "NEEDS_REVIEW", "REFERENCE_ERROR": "NOT_COMPARABLE"}
REPORT_KINDS = ("summary", "fields", "city", "evidence", "failures", "metrics")
STAGE_ORDER = ("RAW", "PROPOSED", "AUTO", "FINAL")

def effective(store: EvaluationStore, eval_id: str,
              fields: Mapping[str, FieldDefinition] | None = None) -> dict[str, Any] | None:
    """The evaluation with adjudications applied (see module docstring)."""
    run = store.run(eval_id, with_snapshot=True)
    if run is None:
        return None
    snapshot = run.pop("snapshot")
    adjudications = store.adjudications(eval_id)
    latest = {(a["target"], a["target_id"]): a for a in adjudications}  # ordered by id
    results = store.field_results(eval_id)
    for r in results:
        a = latest.get(("FIELD_RESULT", r["id"]))
        r["original_status"] = r["status"]
        r["adjudication"] = a
        if a:
            r["status"] = a["status"] if a["decision"] == "OVERRIDE_STATUS" \
                else ADJUDICATED_STATUS.get(a["decision"], r["status"])
    diagnoses = store.diagnoses(eval_id)
    for d in diagnoses:
        a = latest.get(("DIAGNOSIS", d["id"]))
        d["original_root_cause"] = d["root_cause"]
        d["adjudication"] = a
        if a and a["decision"] == "CORRECT_DIAGNOSIS" and a.get("root_cause"):
            d["root_cause"] = a["root_cause"]
    evidence = store.evidence_checks(eval_id)
    if latest:
        metrics = compute_metrics(results, diagnoses, evidence, snapshot,
                                  fields or load_field_definitions())
        basis = "adjudicated"
    else:
        metrics, basis = store.metrics(eval_id), "as evaluated"
    return {"run": run, "results": results, "diagnoses": diagnoses, "evidence": evidence,
            "metrics": metrics, "metrics_basis": basis, "adjudications": adjudications,
            "adjudicated_results": sum(1 for r in results if r["adjudication"]),
            "adjudicated_diagnoses": sum(1 for d in diagnoses if d["adjudication"])}

def headline(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{"RAW.coverage": metric, ...} for the stage metrics."""
    return {f"{m['scope_key']}.{m['metric']}": m for m in view["metrics"]
            if m["scope"] == "stage"}

def fraction(m: dict[str, Any] | None) -> str:
    if not m or m["denominator"] in (None, 0):
        return "—" if not m else f"{m['numerator']:g}/0"
    return f"{m['numerator']:g}/{m['denominator']:g} ({m['value']:.0%})"

# --- display values -------------------------------------------------------------------------

def _show(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_show(v) for v in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)

def predicted_text(r: dict[str, Any]) -> str:
    p = r["predicted"]
    if r["stage"] == "RAW":
        return " | ".join(f"{_show(c['value'])} [{c['status']}]" for c in p["candidates"])
    if r["stage"] == "AUTO":
        return _show(p.get("value")) if p.get("outcome") == "RESOLVED" \
            else (p.get("outcome") or "").lower().replace("_", " ")
    if r["stage"] == "PROPOSED":
        if p.get("source") is None:
            return ""
        many = f" (1 of {p['candidate_count']})" if p.get("candidate_count", 0) > 1 else ""
        return _show(p.get("value")) + many
    value = _show(p.get("value"))
    status = p.get("value_status")
    shown = value if status == "KNOWN" else (status or "")
    return f"{shown} ({p.get('origin')})" if p.get("origin") else shown

def reference_text(ref: dict[str, Any]) -> str:
    if not ref:
        return ""
    if ref.get("value_status") != "KNOWN":
        return f"{ref.get('value_status')} ({_show(ref.get('raw'))})"
    year = f" ({ref['reference_year']})" if ref.get("reference_year") else ""
    return _show(ref.get("normalized")) + year

# --- report rows ----------------------------------------------------------------------------

def report_rows(view: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if kind == "fields":
        return [{"stage": r["stage"], "record": r["record_key"], "field": r["field_key"],
                 "reference": reference_text(r["reference"]),
                 "reference_sources": "; ".join(r["reference"].get("source_urls") or []),
                 "predicted": predicted_text(r), "status": r["status"],
                 "original_status": r["original_status"],
                 "adjudicated": bool(r["adjudication"]),
                 "detail": (r["comparison"] or {}).get("detail") or "",
                 "tags": "; ".join((r["comparison"] or {}).get("tags") or []),
                 "claim_uids": "; ".join((r.get("links") or {}).get("claim_uids") or [])}
                for r in view["results"]]
    if kind == "city":
        return comparison_rows(view)
    if kind == "evidence":
        return [{"claim_uid": e["claim_uid"], "field": e["field_key"],
                 "citation": e["citation_index"], "check": e["check"], "result": e["result"],
                 "detail": e["detail"] or "", "links": json.dumps(e["links"] or {})}
                for e in view["evidence"]]
    if kind == "failures":
        return [{"stage": d["stage"], "record": d["record_key"], "field": d["field_key"],
                 "root_cause": d["root_cause"], "original_root_cause": d["original_root_cause"],
                 "earliest_stage": d["earliest_stage"] or "", "confidence": d["confidence"],
                 "symptoms": "; ".join(d["symptoms"]), "explanation": d["explanation"],
                 "adjudicated": bool(d["adjudication"]),
                 "probes": json.dumps(d["probes"], default=str)}
                for d in view["diagnoses"]]
    if kind == "metrics":
        return [{"scope": m["scope"], "key": m["scope_key"], "metric": m["metric"],
                 "numerator": m["numerator"], "denominator": m["denominator"],
                 "value": m["value"], "basis": view["metrics_basis"],
                 "detail": json.dumps(m["detail"] or {}, default=str)}
                for m in view["metrics"]]
    raise ValueError(f"unknown report kind {kind!r}")

def comparison_rows(view: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per reference cell: reference, then each stage's prediction and status."""
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for r in view["results"]:
        row = cells.setdefault((r["record_key"], r["field_key"]), {
            "field": r["field_key"], "record": r["record_key"],
            "reference": reference_text(r["reference"]),
            "reference_sources": "; ".join(r["reference"].get("source_urls") or [])})
        row[f"{r['stage']} prediction"] = predicted_text(r)
        row[f"{r['stage']} status"] = r["status"] + ("*" if r["adjudication"] else "")
    return list(cells.values())

# --- rendering ------------------------------------------------------------------------------

def render(view: dict[str, Any], kind: str, fmt: str) -> tuple[bytes, str, str]:
    """(content, filename, media type). summary: md or json; other kinds: csv or json."""
    if kind not in REPORT_KINDS:
        raise ValueError(f"unknown report kind {kind!r}")
    stem = f"evaluation_{view['run']['id'][:8]}_{kind}"
    if fmt == "json":
        body = view if kind == "summary" else {"run": view["run"],
                                               "metrics_basis": view["metrics_basis"],
                                               "rows": report_rows(view, kind)}
        return (json.dumps(body, indent=1, default=str, ensure_ascii=False).encode("utf-8"),
                f"{stem}.json", "application/json")
    if fmt == "md" and kind == "summary":
        return summary_markdown(view).encode("utf-8"), f"{stem}.md", "text/markdown"
    if fmt == "csv" and kind != "summary":
        rows = report_rows(view, kind)
        out = io.StringIO()
        if rows:
            columns = list(dict.fromkeys(k for row in rows for k in row))
            writer = csv.DictWriter(out, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        return out.getvalue().encode("utf-8-sig"), f"{stem}.csv", "text/csv"
    raise ValueError(f"format {fmt!r} is not available for {kind!r} "
                     "(summary: md/json; others: csv/json)")

def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")

def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    out = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    out += ["| " + " | ".join(_md_cell(r.get(c))[:160] for c in columns) + " |" for r in rows]
    return out + [""]

def summary_markdown(view: dict[str, Any]) -> str:
    run = view["run"]
    lines = [f"# Evaluation {run['id']}", "",
             f"- Research run: `{run['research_run_id']}` (history from `{run['origin_run_id']}`)",
             f"- City: {run['city_key']}",
             f"- Reference dataset: `{run['dataset_id']}`",
             f"- Stages: {', '.join(run['stages'])}",
             (f"- Comparison policy: {run['compare_policy_version']}; snapshot "
              f"`{run['snapshot_sha256'][:16]}`; evaluated {run['created_at']}"),
             (f"- Metrics basis: **{view['metrics_basis']}** ({view['adjudicated_results']} "
              f"field result(s) and {view['adjudicated_diagnoses']} diagnosis(es) adjudicated)"),
             "", "## Metrics (numerator/denominator)", ""]
    rows = []
    for m in view["metrics"]:
        if m["scope"] != "stage" or m["denominator"] is None:
            continue
        extra = m["detail"].get("coverage") if m["detail"] else None
        rows.append({"stage": m["scope_key"], "metric": m["metric"], "value": fraction(m),
                     "note": f"at coverage {extra}" if extra else ""})
    lines += _md_table(rows, ["stage", "metric", "value", "note"])
    excluded = next((m["detail"].get("excluded") for m in view["metrics"]
                     if m["scope"] == "stage" and m["metric"] == "cells"), None)
    if excluded:
        lines += ["Excluded from denominators: "
                  + ", ".join(f"{k} {v}" for k, v in sorted(excluded.items())), ""]

    lines += ["## Field comparison", ""]
    comparison = comparison_rows(view)
    columns = ["field", "record", "reference"] + [
        c for s in STAGE_ORDER if s in run["stages"]
        for c in (f"{s} prediction", f"{s} status")]
    lines += _md_table(comparison, columns)
    lines += ["`*` = adjudicated.", ""]

    lines += ["## Failures (earliest demonstrable cause)", ""]
    causes = Counter(d["root_cause"] for d in view["diagnoses"] if d["stage"] != "RUN")
    lines += [", ".join(f"{k}: {v}" for k, v in causes.most_common()) or "_none_", ""]
    lines += _md_table([{**d, "symptoms": ", ".join(d["symptoms"])} for d in view["diagnoses"]],
                       ["stage", "field_key", "record_key", "root_cause", "confidence",
                        "symptoms", "explanation"])

    lines += ["## Evidence checks", ""]
    lines += _md_table([{"check": m["scope_key"], "pass": fraction(m),
                         "results": json.dumps(m["detail"].get("results") or {})}
                        for m in view["metrics"] if m["scope"] == "evidence"],
                       ["check", "pass", "results"])

    lines += ["## Adjudications", ""]
    lines += _md_table(view["adjudications"], ["created_at", "reviewer", "target",
                                               "target_id", "decision", "status",
                                               "root_cause", "note"])
    lines += ["## Instrumentation gaps", "",
              "Conclusions that depend on these are PROBABLE or UNKNOWN, never CONFIRMED.", ""]
    lines += [f"- **{g['code']}**: {g['detail']}" for g in run["gaps"]] + [""]
    return "\n".join(lines)

def overview(store: EvaluationStore, include_revalidation: bool = False
             ) -> list[dict[str, Any]]:
    """The latest completed evaluation per city, with its headline metrics (evaluations
    of REVALIDATE runs only with ``include_revalidation``)."""
    seen: set[str] = set()
    out = []
    for run in store.runs():  # newest first
        if run["status"] != "COMPLETED" or run["city_key"] in seen \
                or (is_revalidation(run) and not include_revalidation):
            continue
        seen.add(run["city_key"])
        view = effective(store, run["id"])
        h = headline(view)
        evidence = [m for m in view["metrics"] if m["scope"] == "evidence"
                    and m["scope_key"] == "QUOTE_VERIFIED"]
        out.append({"eval_run_id": run["id"], "city_key": run["city_key"],
                    "research_run_id": run["research_run_id"], "stages": run["stages"],
                    "created_at": run["created_at"], "metrics_basis": view["metrics_basis"],
                    "metrics": {k: {x: h[k][x] for x in ("numerator", "denominator", "value")}
                                for k in ("RAW.coverage", "RAW.accuracy", "AUTO.coverage",
                                          "AUTO.precision", "FINAL.accuracy",
                                          "FINAL.coverage") if k in h},
                    "quote_verified": evidence[0] if evidence else None,
                    "root_causes": dict(Counter(d["root_cause"] for d in view["diagnoses"]
                                                if d["stage"] != "RUN"))})
    return out
