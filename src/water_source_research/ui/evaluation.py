"""Evaluation mode of the dashboard: how saved research output compares with the manually
researched reference, where it failed, adjudication, and report downloads.

Talks only to the API (`/evaluation/*`). Adjudications here are evaluation judgments (is this
comparison / diagnosis right?), recorded separately from research review decisions."""

import json
from typing import Any, get_args
import pandas as pd
import streamlit as st
from water_source_research.evaluation.compare import Status
from water_source_research.evaluation.diagnosis import ROOT_CAUSES
from water_source_research.ui.client import ApiError

STATUS_ICON = {"MATCH": "🟢", "PARTIAL_MATCH": "🟡", "NEEDS_REVIEW": "🟠", "MISMATCH": "🔴",
               "TEMPORAL_MISMATCH": "🕰️", "SCOPE_MISMATCH": "🗺️", "MISSING_PREDICTION": "⚪",
               "MISSING_REFERENCE": "▫️", "NOT_COMPARABLE": "▫️", "NOT_RESEARCHED": "▫️"}
FIELD_DECISIONS = {
    "CONFIRM_MATCH": "Confirm: the prediction matches the reference",
    "CONFIRM_MISMATCH": "Confirm: the prediction is wrong",
    "AMBIGUOUS": "Ambiguous (needs more judgment)",
    "REFERENCE_ERROR": "The reference is wrong (exclude from scoring)",
    "OVERRIDE_STATUS": "Set another status",
}
DIAGNOSIS_DECISIONS = {"CONFIRM_DIAGNOSIS": "Confirm the diagnosis",
                       "CORRECT_DIAGNOSIS": "Correct the root cause"}
HEADLINE = [("RAW.coverage", "Raw coverage"), ("RAW.accuracy", "Raw accuracy"),
            ("AUTO.precision", "Auto precision"), ("AUTO.coverage", "Auto coverage"),
            ("FINAL.accuracy", "Final accuracy"), ("FINAL.coverage", "Final coverage")]

def fraction(m: dict[str, Any] | None) -> str:
    if not m or m.get("denominator") in (None, 0):
        return "—"
    return f"{m['numerator']:g}/{m['denominator']:g} ({m['value']:.0%})"

def render_evaluation(client: Any, reviewer: str | None) -> None:
    st.title("🧪 Research evaluation")
    st.caption("Saved research output compared with the manually researched reference. "
               "Evaluation reads research data only; adjudications here never change it.")
    overview_tab, compare_tab, field_tab, reports_tab = st.tabs(
        ["📊 Overview", "🏙️ City comparison", "🔎 Field investigation", "📄 Reports"])
    with overview_tab:
        render_overview(client)
        render_new_evaluation(client)
    runs = client.eval_runs()
    if not runs:
        for tab in (compare_tab, field_tab, reports_tab):
            with tab:
                st.info("No evaluation runs yet: evaluate a research run in the Overview tab.")
        return
    labels = {r["id"]: f"{r['city_key']} · {'/'.join(r['stages'])} · "
                       f"{(r['created_at'] or '')[:16].replace('T', ' ')} · {r['id'][:8]}"
              for r in runs}
    with st.sidebar:
        eval_id = st.selectbox("Evaluation run", list(labels), format_func=labels.get,
                               key="eval_run")
    run = client.eval_run(eval_id)
    with compare_tab:
        render_comparison(client, run)
    with field_tab:
        render_investigation(client, run, reviewer)
    with reports_tab:
        render_reports(client, run)

# --- overview -------------------------------------------------------------------------------

def render_overview(client: Any) -> None:
    rows = client.eval_overview()
    if not rows:
        st.info("No completed evaluations yet.")
        return
    st.markdown(f"**{len(rows)} evaluated city(ies)** — latest evaluation each; fractions are "
                "numerator/denominator.")
    st.dataframe(pd.DataFrame([{
        "city": r["city_key"], "stages": "/".join(r["stages"]),
        **{label: fraction(r["metrics"].get(key)) for key, label in HEADLINE},
        "quotes verified": fraction(r.get("quote_verified")),
        "metrics": r["metrics_basis"], "evaluation": r["eval_run_id"][:8],
    } for r in rows]), hide_index=True, width="stretch")
    causes: dict[str, int] = {}
    for r in rows:
        for cause, n in r["root_causes"].items():
            causes[cause] = causes.get(cause, 0) + n
    if causes:
        st.markdown("**Failure categories (earliest demonstrable cause)**")
        st.bar_chart(pd.DataFrame({"cells": causes}).sort_values("cells", ascending=False))

def render_new_evaluation(client: Any) -> None:
    with st.expander("➕ Evaluate a research run"):
        runs = [r for r in client.runs(limit=200)
                if r["status"] in ("SUCCEEDED", "AWAITING_REVIEW")]
        if not runs:
            st.caption("No finished or review-waiting research runs.")
            return
        references = client.eval_references()
        labels = {r["run_id"]: f"{r['city_name']}, {r.get('country') or ''} · {r['status']} · "
                               f"{(r['created_at'] or '')[:16].replace('T', ' ')} · "
                               f"{r['run_id'][:8]}" for r in runs}
        research_run = st.selectbox("Research run", list(labels), format_func=labels.get,
                                    key="eval_new_run")
        datasets = {"": "Latest dataset containing this city"} | {
            d["id"]: f"{d['filename']} · {', '.join(d['cities'])} · {d['id'][:8]}"
            for d in references}
        dataset = st.selectbox("Reference dataset", list(datasets), format_func=datasets.get,
                               key="eval_new_dataset")
        st.caption("A run waiting for review is evaluated at RAW and AUTO only; evaluate it "
                   "again after review for FINAL. Every evaluation is kept.")
        if st.button("Evaluate", key="eval_new_go"):
            try:
                made = client.evaluate(research_run, dataset or None)
                st.session_state["eval_run"] = made["id"]
                st.success(f"Evaluation {made['id'][:8]} stored ({'/'.join(made['stages'])}).")
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))

# --- city comparison ------------------------------------------------------------------------

def render_metrics(run: dict[str, Any]) -> None:
    stage = {f"{m['scope_key']}.{m['metric']}": m for m in run["metrics"]
             if m["scope"] == "stage"}
    shown = [(k, label) for k, label in HEADLINE if k in stage]
    cols = st.columns(len(shown) or 1)
    for col, (key, label) in zip(cols, shown, strict=False):
        col.metric(label, fraction(stage[key]))
    basis = run["metrics_basis"]
    st.caption(f"Metrics {basis}"
               + (f" ({run['adjudicated_results']} result(s), {run['adjudicated_diagnoses']} "
                  "diagnosis(es) adjudicated)" if basis == "adjudicated" else "")
               + f" · reference {run['dataset_id'][:8]} · policy "
               f"{run['compare_policy_version']} · snapshot {run['snapshot_sha256'][:12]}")

def report_rows(client: Any, eval_id: str, kind: str) -> list[dict[str, Any]]:
    content, _, _ = client.eval_report(eval_id, kind, "json")
    return json.loads(content)["rows"]

def render_comparison(client: Any, run: dict[str, Any]) -> None:
    st.markdown(f"**{run['city_key']}** · research run `{run['research_run_id']}`"
                + (f" (history from `{run['origin_run_id'][:8]}`)"
                   if run["origin_run_id"] != run["research_run_id"] else ""))
    render_metrics(run)
    rows = report_rows(client, run["id"], "city")
    statuses = sorted({v.rstrip("*") for r in rows for k, v in r.items()
                       if k.endswith(" status")})
    picked = st.multiselect("Show cells with status", statuses, default=statuses,
                            key=f"cmp_status:{run['id']}")
    shown = [r for r in rows if any(v.rstrip("*") in picked for k, v in r.items()
                                    if k.endswith(" status"))]
    table = []
    for r in shown:
        row = {"field": r["field"] + (f" [{r['record']}]" if r["record"] else ""),
               "reference": r["reference"]}
        for stage in run["stages"]:
            status = r.get(f"{stage} status", "")
            row[f"{stage}"] = r.get(f"{stage} prediction", "")
            row[f"{stage} result"] = f"{STATUS_ICON.get(status.rstrip('*'), '')} {status}"
        row["reference sources"] = r["reference_sources"]
        table.append(row)
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    st.caption("`*` = adjudicated. RAW lists every candidate with its own comparison; AUTO is "
               "the rules' decision before review; FINAL shows the value's origin.")
    gaps = run.get("gaps") or []
    if gaps:
        with st.expander(f"Instrumentation gaps ({len(gaps)})"):
            for g in gaps:
                st.markdown(f"- **{g['code']}** — {g['detail']}")

# --- field investigation and adjudication -------------------------------------------------

def render_investigation(client: Any, run: dict[str, Any], reviewer: str | None) -> None:
    results = client.eval_fields(run["id"])
    keys = list(dict.fromkeys(r["field_key"] for r in results))
    flagged = {r["field_key"] for r in results if r["status"] not in ("MATCH",)}
    field_key = st.selectbox("Field", keys, key=f"inv_field:{run['id']}",
                             format_func=lambda k: f"{'⚠️ ' if k in flagged else ''}{k}")
    data = client.eval_field(run["id"], field_key)
    ref = data["results"][0]["reference"]
    st.markdown(f"**Reference:** {ref.get('raw')} → `{ref.get('normalized')}` · "
                f"{ref.get('value_status')} · unit {ref.get('unit') or '—'} · year "
                f"{ref.get('reference_year') or '—'}"
                + (f" · issues: {', '.join(i['code'] for i in ref['issues'])}"
                   if ref.get("issues") else ""))
    for url in ref.get("source_urls") or []:
        st.markdown(f"- reference source: [{url}]({url})")
    for r in data["results"]:
        icon = STATUS_ICON.get(r["status"], "")
        changed = f" (evaluated {r['original_status']})" if r.get("adjudication") else ""
        where = f" [{r['record_key']}]" if r["record_key"] else ""
        with st.expander(f"{r['stage']}{where}: {icon} {r['status']}{changed}",
                         expanded=r["status"] != "MATCH"):
            render_stage(r)
            adjudication_form(client, run["id"], "FIELD_RESULT", r["id"], FIELD_DECISIONS,
                              reviewer)
    if data["evidence"]:
        st.markdown("**Evidence checks**")
        st.dataframe(pd.DataFrame([{"claim": e["claim_uid"][:10], "citation": e["citation_index"],
                                    "check": e["check"], "result": e["result"],
                                    "detail": e["detail"] or ""} for e in data["evidence"]]),
                     hide_index=True, width="stretch")
    for d in data["diagnoses"]:
        corrected = f" (evaluated {d['original_root_cause']})" if d.get("adjudication") else ""
        st.markdown(f"**Diagnosis ({d['stage']}):** {d['root_cause']}{corrected} · "
                    f"{d['confidence']} · earliest stage {d['earliest_stage'] or '—'}"
                    + (f" · symptoms: {', '.join(d['symptoms'])}" if d["symptoms"] else ""))
        st.caption(d["explanation"])
        with st.expander("Probe trail (what was observed)"):
            for p in d["probes"]:
                st.markdown(f"*{p['probe']}*")
                st.json(p["observed"], expanded=False)
        adjudication_form(client, run["id"], "DIAGNOSIS", d["id"], DIAGNOSIS_DECISIONS,
                          reviewer)
    if data["adjudications"]:
        st.markdown("**Adjudication history (append-only)**")
        st.dataframe(pd.DataFrame(data["adjudications"]), hide_index=True, width="stretch")

def render_stage(r: dict[str, Any]) -> None:
    p, cmp = r["predicted"], r["comparison"] or {}
    if cmp.get("detail"):
        st.caption(cmp["detail"])
    if r["stage"] == "RAW":
        if not p["candidates"]:
            st.caption("No candidate claim was extracted for this cell.")
            return
        st.dataframe(pd.DataFrame([{
            "value": str(c["value"]), "comparison": c["status"],
            "confidence": c["confidence"], "validation": c.get("validation") or "",
            "failed checks": ", ".join(c["fail_codes"]), "tags": ", ".join(c["tags"]),
            "sources": "; ".join(f"{u} ({t or '—'})" for u, t in c["sources"]),
            "claim": c["claim_uid"],
        } for c in p["candidates"]]), hide_index=True, width="stretch")
    elif r["stage"] == "AUTO":
        st.markdown(f"Outcome **{p['outcome']}** · rule {p.get('rule') or '—'} · "
                    f"value {p.get('value')} · reasons "
                    f"{', '.join(p.get('reason_codes') or []) or '—'}")
    else:
        st.markdown(f"Value **{p.get('value')}** · {p.get('value_status')} · origin "
                    f"{p.get('origin') or '—'}")
        if p.get("calculation"):
            st.caption(f"Calculated: {p['calculation'].get('formula')}")
        if p.get("review_decision_ids"):
            st.caption(f"Reviewer decision(s): {', '.join(map(str, p['review_decision_ids']))}")

def adjudication_form(client: Any, eval_id: str, target: str, target_id: int,
                      decisions: dict[str, str], reviewer: str | None) -> None:
    with st.form(f"adj:{target}:{target_id}"):
        kind = st.selectbox("Adjudicate", list(decisions), format_func=decisions.get,
                            key=f"adj_kind:{target}:{target_id}")
        status = root_cause = None
        if target == "FIELD_RESULT":
            status = st.selectbox("Status (for 'set another status')",
                                  [""] + list(get_args(Status)),
                                  key=f"adj_status:{target}:{target_id}")
        else:
            root_cause = st.selectbox("Root cause (for 'correct')", [""] + list(ROOT_CAUSES),
                                      key=f"adj_cause:{target}:{target_id}")
        note = st.text_input("Note", key=f"adj_note:{target}:{target_id}")
        if not st.form_submit_button("Record adjudication", disabled=not reviewer):
            if not reviewer:
                st.caption("Set **Reviewer** in the sidebar to adjudicate.")
            return
    try:
        client.adjudicate(eval_id, {"target": target, "target_id": target_id,
                                    "decision": kind, "reviewer": reviewer,
                                    "status": status or None, "root_cause": root_cause or None,
                                    "note": note or None})
        st.success("Adjudication recorded.")
        st.rerun()
    except ApiError as exc:
        st.error(str(exc))

# --- reports --------------------------------------------------------------------------------

REPORTS = [("Summary (Markdown)", "summary", "md"), ("Full evaluation (JSON)", "summary", "json"),
           ("Field results (CSV)", "fields", "csv"), ("City comparison (CSV)", "city", "csv"),
           ("Evidence checks (CSV)", "evidence", "csv"), ("Failures (CSV)", "failures", "csv"),
           ("Metrics (CSV)", "metrics", "csv")]

def render_reports(client: Any, run: dict[str, Any]) -> None:
    st.caption(f"Reports use the {run['metrics_basis']} view of evaluation {run['id'][:8]}.")
    cols = st.columns(2)
    for i, (label, kind, fmt) in enumerate(REPORTS):
        col = cols[i % 2]
        slot = f"eval_report:{run['id']}:{kind}:{fmt}"
        if col.button(f"Prepare {label}", key=f"prepare:{slot}"):
            try:
                st.session_state[slot] = client.eval_report(run["id"], kind, fmt)
            except ApiError as exc:
                col.error(str(exc))
        if slot in st.session_state:
            content, filename, media = st.session_state[slot]
            col.download_button(f"⬇️ {label}", content, file_name=filename, mime=media,
                                key=f"download:{slot}")
