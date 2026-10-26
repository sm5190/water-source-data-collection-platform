"""Evaluation mode of the dashboard.

Main view (for researchers): per city, Field | Manual Data | Automated Data | Match Status for
the Proposed, Auto-accepted or Final reviewed stage, with accuracy (strict and lenient) and
coverage, each as numerator/denominator; evidence and a Verify control per field in
expanders. Research performance: the same scores by city, by field and overall. Everything
technical (stage matrix, diagnoses, probe trails, evidence checks, reports) is in Advanced.

Talks only to the API (`/evaluation/*`). Verifications here are evaluation adjudications (is
this comparison right?), recorded separately from research review decisions."""

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
    st.title("🧪 Automated research vs manual data")
    st.caption("What the system collected, what the manually researched workbook says, and "
               "how well they match. Evaluation never changes research data.")
    city_tab, perf_tab, advanced_tab = st.tabs(
        ["🏙️ City comparison", "📊 Research performance", "🛠 Advanced"])
    with city_tab:
        render_city(client, reviewer)
    with perf_tab:
        render_performance(client)
    with advanced_tab:
        render_advanced(client, reviewer)

def render_advanced(client: Any, reviewer: str | None) -> None:
    """The technical evaluation views: stage matrix, diagnoses, evidence checks, reports."""
    overview_tab, compare_tab, field_tab, reports_tab = st.tabs(
        ["Overview", "Stage matrix", "Field investigation", "Reports"])
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
    with overview_tab:
        eval_id = st.selectbox("Evaluation run (for the other Advanced tabs)", list(labels),
                               format_func=labels.get, key="eval_run")
    run = client.eval_run(eval_id)
    with compare_tab:
        render_comparison(client, run)
    with field_tab:
        render_investigation(client, run, reviewer)
    with reports_tab:
        render_reports(client, run)

# --- city comparison (main view) ------------------------------------------------------------

GROUP_BADGE = {"MATCH": "🟢 Match", "PARTIAL": "🟡 Partial match", "MISMATCH": "🔴 Mismatch",
               "MISSING_AUTOMATED": "⚪ Missing in automated data",
               "MISSING_MANUAL": "▫️ Missing in manual data",
               "NEEDS_VERIFICATION": "🟠 Needs verification",
               "NOT_COMPARABLE": "▫️ Not comparable (manual cell is a placeholder)",
               "NOT_RESEARCHED": "▫️ Not researched in this run",
               "NOT_EVALUATED": "▫️ Not produced at this stage"}
STAGE_HELP = {
    "PROPOSED": "What the system extracted and put forward for each field, before any person "
                "(accepted automatically, or proposed and sent to review).",
    "AUTO": "Only the values the system accepted on its own, without a person.",
    "FINAL": "Values after researchers reviewed and corrected them.",
}
VERIFY = [("They match", {"decision": "CONFIRM_MATCH"}),
          ("Partly match", {"decision": "OVERRIDE_STATUS", "status": "PARTIAL_MATCH"}),
          ("They don't match", {"decision": "CONFIRM_MISMATCH"}),
          ("The manual value is wrong", {"decision": "REFERENCE_ERROR"})]

def ratio(num: float | None, den: float | None) -> str:
    """'n/d (x%)', or 'Not enough data' when there is nothing to divide by."""
    if not den:
        return "Not enough data"
    return f"{num:g}/{den:g} ({num / den:.0%})"

def badge(row: dict[str, Any]) -> str:
    text = GROUP_BADGE.get(row["group"], row["group"])
    if row.get("sub_label"):
        text += f" ({row['sub_label']})"
    if row.get("provisional"):
        text += " · provisional"
    return text

def render_city(client: Any, reviewer: str | None) -> None:
    cities = client.eval_cities()
    if not cities:
        st.info("No completed evaluations yet.")
        render_new_evaluation(client, "city")
        return
    keys = [c["city_key"] for c in cities]
    city_key = st.selectbox("City", keys, key="eval_city",
                            format_func=lambda k: k.split("|")[0].title()
                            + (f", {k.split('|')[1].title()}" if "|" in k else ""))
    city = next(c for c in cities if c["city_key"] == city_key)
    eval_id = city["latest_eval_run_id"]
    latest = city["evaluations"][0]
    available = [s for s in ("PROPOSED", "AUTO", "FINAL") if s in latest["stages"]]
    if not available:
        st.warning("This evaluation predates the Proposed stage: evaluate the run again "
                   "(Advanced → Overview).")
        return
    labels = {"PROPOSED": "Proposed (automated)", "AUTO": "Auto-accepted (automated)",
              "FINAL": "Final reviewed"}
    stage = st.radio("Results", available, format_func=labels.get, horizontal=True,
                     key=f"eval_stage:{city_key}", help=" ".join(
                         f"{labels[s]}: {STAGE_HELP[s]}" for s in available))
    if "FINAL" not in available:
        st.caption("Final reviewed results appear after the city's review is finished and the "
                   "run is evaluated again.")
    data = client.eval_comparison(eval_id, stage)
    render_summary(data, stage)
    rows = data["rows"]
    st.dataframe(pd.DataFrame([{
        "Field": r["field"] + (f" — {r['record_label']}" if r.get("record_label") and len(
            {x["record_key"] for x in rows if x["record_key"]}) > 1 else ""),
        "Manual Data": r["manual"] or "—",
        "Automated Data": automated_cell(r),
        "Match Status": badge(r),
    } for r in rows]), hide_index=True, width="stretch")
    st.caption(f"Evaluated {(latest['created_at'] or '')[:16].replace('T', ' ')} · research run "
               f"`{latest['research_run_id'][:8]}` · {data['metrics_basis']}")
    st.markdown("**Evidence and details** (open a field)")
    for r in rows:
        if r["status"] == "NOT_EVALUATED":
            continue
        with st.expander(f"{badge(r)} — {r['field']}"
                         + (f" ({r['record_label']})" if r.get("record_label") else "")):
            render_details(client, eval_id, r, reviewer)
    render_new_evaluation(client, "city")

def automated_cell(r: dict[str, Any]) -> str:
    text = r["automated"] or "—"
    details = r.get("details") or {}
    n = details.get("candidate_count") or 0
    if r.get("proposed_not_accepted"):
        text += " (proposed — sent to review)"
    if n > 1 and r["automated"]:
        text += f" [1 of {n} values found]"
    return text

def render_summary(data: dict[str, Any], stage: str) -> None:
    s = data["summaries"][stage]
    a, b, c = st.columns(3)
    a.metric("Accuracy (strict)", ratio(s["match"], s["answered"]),
             help="Matching fields ÷ fields the system answered. Only full matches count.")
    b.metric("Accuracy (lenient)", ratio(s["match"] + 0.5 * s["partial"], s["answered"]),
             help="Like strict, but a partial match counts as half a match.")
    c.metric("Coverage", ratio(s["answered"], s["comparable"]),
             help="Fields the system answered ÷ fields that have a manual value to compare "
                  "with. Missing automated values lower coverage, not accuracy.")
    st.markdown(f"🟢 {s['match']} match · 🟡 {s['partial']} partial · 🔴 {s['mismatch']} "
                f"mismatch · ⚪ {s['missing_automated']} missing in automated · ▫️ "
                f"{s['missing_manual']} missing in manual · 🟠 {s['needs_verification']} "
                "need verification")
    others = [x for x in ("PROPOSED", "AUTO") if x != stage and x in data["summaries"]]
    for other in others:
        o = data["summaries"][other]
        st.caption(f"{o['label']}: accuracy {ratio(o['match'], o['answered'])} strict, "
                   f"{ratio(o['match'] + 0.5 * o['partial'], o['answered'])} lenient; "
                   f"coverage {ratio(o['answered'], o['comparable'])}")
    if s["provisional"]:
        st.info(f"{s['provisional']} result(s) come from the automatic meaning check and are "
                "**provisional** until a person verifies them (open the field below)."
                + ("" if data.get("judge_validated") else
                   " The meaning check has not been validated yet."))

def render_details(client: Any, eval_id: str, r: dict[str, Any], reviewer: str | None) -> None:
    d = r.get("details") or {}
    left, right = st.columns(2)
    with left:
        st.markdown(f"**Manual data:** {r['manual'] or '—'}")
        if d.get("manual_year"):
            st.caption(f"Reporting year: {d['manual_year']}")
        for url in d.get("manual_sources") or []:
            st.markdown(f"- [{url}]({url})")
    with right:
        st.markdown(f"**Automated data:** {r['automated'] or '—'}")
        if d.get("automated_year"):
            st.caption(f"Reporting year: {d['automated_year']}")
        for cite in d.get("citations") or []:
            if cite.get("quote"):
                st.markdown("> " + cite["quote"].replace("\n", " ")[:600])
            if cite.get("url"):
                page = f", page {cite['page_number']}" if cite.get("page_number") else ""
                st.markdown(f"Source: [{cite.get('title') or cite['url']}]({cite['url']}){page}")
    if r.get("explanation"):
        st.markdown(f"**Why:** {r['explanation']}")
    cands = d.get("candidates") or []
    if len(cands) > 1:
        st.markdown(f"**All {len(cands)} values the system found** (the compared one is marked)")
        for c in cands:
            st.markdown(f"- {'**→** ' if c['selected'] else ''}{c['value']}")
    j = d.get("judgment")
    if j:
        facts = [("Agree", j.get("overlapping_facts")), ("Missing", j.get("missing_facts")),
                 ("Different", j.get("differing_facts"))]
        for label, items in facts:
            if items:
                st.caption(f"{label}: " + "; ".join(items))
        st.caption(("Provisional — " if r.get("provisional") else "Verified by a person — ")
                   + f"decided by the automatic meaning check ({j.get('model')}, "
                   f"{j.get('prompt_version')}).")
    if d.get("adjudication"):
        a = d["adjudication"]
        st.caption(f"Verified by {a['reviewer']}: {a['decision'].replace('_', ' ').lower()}")
    if r.get("result_id") is None:
        return
    st.caption("Verify this comparison:")
    cols = st.columns(len(VERIFY))
    for col, (label, body) in zip(cols, VERIFY, strict=True):
        if col.button(label, key=f"verify:{r['result_id']}:{body['decision']}"
                                  f":{body.get('status', '')}", disabled=not reviewer):
            try:
                client.adjudicate(eval_id, {"target": "FIELD_RESULT",
                                            "target_id": r["result_id"], "reviewer": reviewer,
                                            "status": body.get("status"), "root_cause": None,
                                            "note": None, "decision": body["decision"]})
                st.success("Saved.")
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))
    if not reviewer:
        st.caption("Type your name in the sidebar to verify.")

# --- research performance -------------------------------------------------------------------

def render_performance(client: Any) -> None:
    perf = client.eval_performance()
    if not perf["cities"]:
        st.info("No completed evaluations yet.")
        return
    stages = [s for s in ("PROPOSED", "AUTO", "FINAL") if s in perf["stages"]]
    labels = perf["stage_labels"]
    st.markdown("**Accuracy and coverage by city** (latest evaluation per city)")
    st.dataframe(pd.DataFrame([{
        "City": c["city_key"].split("|")[0].title(),
        **{f"{labels[s]} {k}": v for s in stages if s in c["scores"]
           for k, v in score_cells(c["scores"][s]).items()},
    } for c in perf["cities"]]), hide_index=True, width="stretch")
    chart = {c["city_key"].split("|")[0].title(): {
        "Proposed (strict)": c["scores"]["PROPOSED"]["accuracy_strict"],
        "Proposed (lenient)": c["scores"]["PROPOSED"]["accuracy_lenient"]}
        for c in perf["cities"] if "PROPOSED" in c["scores"]}
    if chart:
        st.bar_chart(pd.DataFrame(chart).T)

    st.markdown("**Accuracy and coverage by field** (pooled over cities)")
    st.dataframe(pd.DataFrame([{
        "Field": f["field"],
        **{f"{labels[s]} {k}": v for s in stages for k, v in score_cells(f[s]).items()},
        "Cities": max((f[s]["n_cities"] for s in stages), default=0),
    } for f in perf["fields"]]), hide_index=True, width="stretch")

    st.markdown("**Whole dataset**")
    rows = []
    for s in stages:
        d = perf["dataset"][s]
        rows.append({"Results": labels[s], **score_cells(d),
                     "Average of cities (strict)": pct(d["mean_city_accuracy_strict"]),
                     "Average of cities (lenient)": pct(d["mean_city_accuracy_lenient"]),
                     "Missing-data rate": pct(d["missing_data_rate"]),
                     "Cities": d["n_cities"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Averages of cities need at least two evaluated cities.")
    j = perf["judge"]
    st.markdown(f"**Automatic meaning check:** {j['judged']} result(s) decided by it, "
                f"{j['verified']} verified by a person"
                + (f", agreeing {j['agreement_with_people']:.0%} of the time"
                   if j["agreement_with_people"] is not None else "")
                + (". Validated on the Toronto calibration cases." if j["validated"] else
                   ". **Not validated yet**: its results are provisional."))
    if perf["history"]:
        st.markdown("**Earlier evaluations**")
        st.dataframe(pd.DataFrame([{
            "When": (h["created_at"] or "")[:16].replace("T", " "),
            "City": h["city_key"].split("|")[0].title(), "Rules": h["compare_policy_version"],
            **{f"{labels[s]} accuracy": fraction(h["scores"][s]["accuracy_strict"])
               for s in stages if s in h["scores"]},
            **{f"{labels[s]} coverage": fraction(h["scores"][s]["answer_coverage"])
               for s in stages if s in h["scores"]},
        } for h in perf["history"]]), hide_index=True, width="stretch")

def score_cells(s: dict[str, Any]) -> dict[str, str]:
    return {"accuracy (strict)": ratio(s["match"], s["answered"]),
            "accuracy (lenient)": ratio(s["match"] + 0.5 * s["partial"], s["answered"]),
            "coverage": ratio(s["answered"], s["comparable"])}

def pct(value: float | None) -> str:
    return "Not enough data" if value is None else f"{value:.0%}"

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

def render_new_evaluation(client: Any, where: str = "adv") -> None:
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
                                    key=f"eval_new_run:{where}")
        datasets = {"": "Latest dataset containing this city"} | {
            d["id"]: f"{d['filename']} · {', '.join(d['cities'])} · {d['id'][:8]}"
            for d in references}
        dataset = st.selectbox("Reference dataset", list(datasets), format_func=datasets.get,
                               key=f"eval_new_dataset:{where}")
        st.caption("A run waiting for review is evaluated at RAW and AUTO only; evaluate it "
                   "again after review for FINAL. Every evaluation is kept.")
        if st.button("Evaluate", key=f"eval_new_go:{where}"):
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
