"""Monitoring mode of the dashboard: is research running well, are workers healthy, what did
model calls use and cost, and what needs attention.

Talks only to the API (``/monitoring/*``). Numbers come from stored records (see
observability/monitoring.py); "Unknown" means the information doesn't exist (a provider that
didn't report tokens, a model without a price, a run without step records) — never zero.
Technical detail (raw facts, event lists) stays in expanders."""

from typing import Any
import pandas as pd
import streamlit as st
from water_source_research.ui.client import ApiError

UNKNOWN = "Unknown"
STATUS_ICON = {"ALIVE": "🟢", "STALE": "🟠", "STOPPED": "⚪", "EXITED": "🔴"}
RUN_ICON = {"QUEUED": "⏳", "RUNNING": "🏃", "AWAITING_REVIEW": "🧑‍⚖️", "SUCCEEDED": "✅",
            "FAILED": "❌", "CANCELLED": "🚫"}
SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
NODE_LABEL = {"plan": "Plan", "discover": "Search", "acquire": "Fetch documents",
              "extract": "Extract", "validate": "Validate", "resolve": "Resolve",
              "human_review": "Human review", "assemble": "Assemble", "finalize": "Finalize"}
WINDOWS = {"Last 24 hours": 24, "Last 7 days": 24 * 7, "All time": 0}

def duration(seconds: float | None) -> str:
    if seconds is None:
        return UNKNOWN
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"

def number(value: Any) -> str:
    return UNKNOWN if value is None else f"{value:,}"

def tokens(u: dict[str, Any], key: str) -> str:
    """Known token sum, and how many calls had no usage reported."""
    known, missing = u.get(key), u.get("unknown_token_calls") or 0
    if known is None:
        return UNKNOWN if missing or u.get("invocations") else "—"
    return f"{known:,}" + (f" (+{missing} Unknown)" if missing else "")

def cost(u: dict[str, Any]) -> str:
    status = u.get("cost_status")
    if status == "NO_CALLS":
        return "—"
    if status == "UNKNOWN":
        return UNKNOWN
    text = f"${u.get('cost_usd_known') or 0:,.4f}"
    return text + (f" + Unknown for {u['unknown_cost_calls']} call(s)"
                   if status == "PARTIAL" else "")

def latency(ms: float | None) -> str:
    return UNKNOWN if ms is None else f"{ms / 1000:.1f}s"

def safe(call, default=None):
    try:
        return call()
    except ApiError as exc:
        st.warning(f"Could not load monitoring data: {exc}")
        return default

def render_monitoring(client: Any) -> None:
    st.title("📈 Monitoring")
    st.caption("Research runs, workers, model usage and costs, from what the system "
               "recorded. \"Unknown\" means the information was not reported — never zero.")
    overview_tab, runs_tab, workers_tab, usage_tab = st.tabs(
        ["Overview", "Research runs", "Workers & crashes", "Usage & costs"])
    with overview_tab:
        render_overview(client)
    with runs_tab:
        render_runs(client)
    with workers_tab:
        render_workers(client)
    with usage_tab:
        render_usage(client)

# --- overview ---------------------------------------------------------------------------

def render_alerts(client: Any) -> None:
    data = safe(client.mon_alerts, {}) or {}
    alerts = data.get("alerts") or []
    if not alerts:
        st.success("No alerts")
        return
    for a in alerts:
        text = f"{SEVERITY_ICON.get(a['severity'], '•')} **{a['title']}** — {a['detail']}"
        (st.error if a["severity"] == "critical" else st.warning)(text)
    with st.expander(f"Alert details ({len(alerts)})"):
        st.dataframe(pd.DataFrame([{"Rule": a["rule"], "Severity": a["severity"],
                                    "Since": a.get("since"), "Evidence": a.get("evidence")}
                                   for a in alerts]), hide_index=True, width="stretch")

def render_overview(client: Any) -> None:
    window = st.selectbox("Period", list(WINDOWS)[:2], key="mon_overview_window")
    render_alerts(client)
    o = safe(lambda: client.mon_overview(WINDOWS[window]))
    if not o:
        return
    runs, w, d, u = o["runs"], o["workers"], o["durations"], o["usage"]
    st.subheader("Research runs")
    c = st.columns(4)
    c[0].metric("Active (queued or running)", runs["active"])
    c[1].metric("Awaiting review", runs["awaiting_review"])
    c[2].metric("Succeeded (all time)", runs["succeeded_total"])
    c[3].metric("Failed (all time)", runs["failed_total"])
    finished = runs["finished_in_window"] or {}
    st.caption("Finished in this period: " + (", ".join(
        f"{n} {status.lower().replace('_', ' ')}" for status, n in sorted(finished.items()))
        or "none"))
    st.subheader("Workers")
    c = st.columns(4)
    for i, status in enumerate(("ALIVE", "STALE", "EXITED", "STOPPED")):
        c[i].metric(f"{STATUS_ICON[status]} {status.title()}", w.get(status, 0))
    st.subheader(f"Research duration (median of {d['runs']} run(s) finished in this period)")
    c = st.columns(4)
    c[0].metric("Total time", duration(d["median_wall_s"]))
    c[1].metric("Processing", duration(d["median_active_s"]))
    c[2].metric("Waiting in queue", duration(d["median_queue_wait_s"]))
    c[3].metric("Waiting for review", duration(d["median_review_wait_s"]))
    st.subheader("Model usage in this period")
    c = st.columns(4)
    c[0].metric("Model calls", u["invocations"], help="Cache hits are not model calls")
    c[1].metric("Input tokens", tokens(u, "input_tokens"))
    c[2].metric("Output tokens", tokens(u, "output_tokens"))
    c[3].metric("Estimated cost", cost(u))
    backends = o.get("backends") or []
    if backends:
        st.subheader("Monitoring backends")
        st.dataframe(pd.DataFrame([{"Backend": b["backend"], "Status": b["status"],
                                    "Detail": b.get("detail")} for b in backends]),
                     hide_index=True, width="stretch")
    st.subheader("Recent errors")
    if o["recent_errors"]:
        st.dataframe(pd.DataFrame([{"When": e.get("at"), "City": e.get("city") or "—",
                                    "Error": (e.get("error") or "")[:200]}
                                   for e in o["recent_errors"]]),
                     hide_index=True, width="stretch")
    else:
        st.caption("None in this period.")

# --- research runs ----------------------------------------------------------------------

def render_runs(client: Any) -> None:
    runs = safe(client.mon_runs, []) or []
    if not runs:
        st.info("No research runs yet.")
        return
    cities = sorted({r["city_name"] for r in runs})
    city = st.selectbox("City", cities, key="mon_city")
    mine = [r for r in runs if r["city_name"] == city]
    choice = st.selectbox(
        "Run", range(len(mine)), key="mon_run",
        format_func=lambda i: f"{RUN_ICON.get(mine[i]['status'], '')} "
                              f"{mine[i]['created_at'][:16].replace('T', ' ')} · "
                              f"{mine[i]['status']} · {mine[i]['run_id'][:8]}")
    d = safe(lambda: client.mon_run(mine[choice]["run_id"]))
    if d:
        render_run(d)

MODE_LABEL = {"REPROCESS": "Reprocess of an earlier run",
              "REPLAY": "Replay on saved documents",
              "REVALIDATE": "Revalidation (stored claims re-checked; not a research run)"}

def render_run(d: dict[str, Any]) -> None:
    run, t = d["run"], d["time"]
    st.markdown(f"**{RUN_ICON.get(run['status'], '')} {run['status']}**"
                + (f" · {run['completion']}" if run.get("completion") else "")
                + f" · attempts {run['attempts']} (retries {d['retries']})"
                + (" · " + MODE_LABEL.get(run["mode"], "mode " + run["mode"])
                   if run.get("mode") not in (None, "RESEARCH") else ""))
    done = [p for p in d["progress"] if p["done"]]
    st.progress(len(done) / len(d["progress"]),
                text=" → ".join(("✅ " if p["done"] else "⬜ ") + NODE_LABEL.get(p["node"],
                                                                             p["node"])
                                for p in d["progress"]))
    c = st.columns(5)
    c[0].metric("Total time", duration(t["wall_s"]))
    c[1].metric("Processing", duration(t["active_s"]))
    c[2].metric("Waiting in queue", duration(t["queue_wait_s"]))
    c[3].metric("Waiting for review", duration(t["review_wait_s"]))
    c[4].metric("Waiting after errors", duration(t["other_wait_s"]),
                help="Retry backoff and time lost to worker crashes or expired leases")
    if d["stage_totals"]:
        st.markdown("**Time per stage**")
        st.dataframe(pd.DataFrame([{"Stage": NODE_LABEL.get(k, k), "Times run": v["runs"],
                                    "Total": duration(v["seconds"])}
                                   for k, v in d["stage_totals"].items()]),
                     hide_index=True, width="stretch")
    p = d["pipeline"]
    st.markdown("**Pipeline**")
    c = st.columns(5)
    c[0].metric("Search results (kept)", f"{p['search_results']} ({p['search_results_kept']})")
    c[1].metric("Documents fetched OK", p["documents"].get("OK", 0),
                help=f"All attempts by status: {p['documents']}")
    c[2].metric("Evidence chunks", p["chunks"])
    claims = sum(p["claims_by_validation"].values())
    c[3].metric("Claims", claims)
    c[4].metric("Failed validation", p["validation_failures"])
    if p["extraction"]:
        st.dataframe(pd.DataFrame([{"Field": k, "Extraction attempts": v["attempts"],
                                    "Re-extractions": v["reextractions"],
                                    "Claims": v["claims"],
                                    "Rounds": ", ".join(str(x) for x in v["rounds"] if x)}
                                   for k, v in p["extraction"].items()]),
                     hide_index=True, width="stretch")
    st.markdown("**Model calls**")
    llm = d.get("llm")
    if not llm:
        st.caption("No model calls recorded for this run.")
    else:
        if llm.get("source") != "ledger":
            st.caption("From the run's extraction events (recorded before the usage ledger "
                       "existed): extraction only, no cost.")
        c = st.columns(4)
        c[0].metric("Model calls", llm.get("invocations"))
        c[1].metric("Input tokens", tokens(llm, "input_tokens"))
        c[2].metric("Output tokens", tokens(llm, "output_tokens"))
        c[3].metric("Estimated cost", cost(llm))
        if d.get("llm_by_field"):
            st.dataframe(usage_table(d["llm_by_field"], "Field"), hide_index=True,
                         width="stretch")
    if d["errors"]:
        st.markdown("**Errors**")
        for e in d["errors"]:
            st.error(f"{e['what']}: {e.get('error')}")
    links = [t for t in d["traces"] if t["sampled"]]
    if links:
        st.markdown("**Traces**")
        for t in links:
            line = f"- {t['kind'].title()} (attempt {t['attempt']}): [Jaeger]({t['jaeger_url']})"
            if t.get("langfuse_url"):
                line += f" · [Langfuse]({t['langfuse_url']})"
            st.markdown(line)
    else:
        st.caption("Not traced (tracing is off, or the run was not selected for a trace).")
    with st.expander("Steps (all attempts)"):
        st.dataframe(pd.DataFrame(d["stages"]), hide_index=True, width="stretch")
    with st.expander(f"Worker events for this run ({len(d['lifecycle'])})"):
        for e in d["lifecycle"]:
            st.markdown(f"- {e['occurred_at'][:19].replace('T', ' ')} · {e['label']}"
                        + (f" · {e['inferred_cause']}" if e.get("inferred_cause") else ""))

# --- workers & crashes ------------------------------------------------------------------

def render_workers(client: Any) -> None:
    ws = safe(client.mon_workers, []) or []
    st.subheader("Workers")
    if not ws:
        st.info("No worker has reported yet.")
    else:
        st.dataframe(pd.DataFrame([{
            "": STATUS_ICON.get(w["status"], ""), "Status": w["status"],
            "Worker": w["instance_id"], "PID": w.get("pid"),
            "CPU %": UNKNOWN if w.get("cpu_percent") is None else w["cpu_percent"],
            "Memory (RSS MB)": UNKNOWN if w.get("rss_mb") is None else w["rss_mb"],
            "Private MB": UNKNOWN if w.get("private_mb") is None else w["private_mb"],
            "Last heartbeat": ("—" if w.get("heartbeat_age_s") is None
                               else f"{duration(w['heartbeat_age_s'])} ago"),
            # what its last heartbeat said; for a worker that is gone, what it was doing
            "Runs (last heartbeat)": ((("was running " if w["status"] != "ALIVE" else "")
                                       + ", ".join(f"{k[:8]} ({v.get('stage')})" for k, v in
                                                   (w.get("active_runs") or {}).items()))
                                      if w.get("active_runs") else "—"),
            "Ended as": w.get("termination_class") or "—"} for w in ws]),
            hide_index=True, width="stretch")
    st.subheader("Crashes")
    crashes = safe(client.mon_crashes, []) or []
    if not crashes:
        st.caption("No unexpected worker exits in the last 7 days.")
    for c in crashes:
        f = c["facts"]
        title = (f"🔴 {c['occurred_at'][:19].replace('T', ' ')} · exit {f.get('exit_code')} "
                 f"({f.get('exit_hex')}) · {c['inferred_cause']} [{c['cause_confidence']}]")
        with st.expander(title):
            st.markdown(f"**Why this cause:** {c.get('rationale') or '—'}")
            st.caption("Cause and confidence are an interpretation of the recorded facts "
                       "below; nothing is assumed beyond them.")
            st.json(f, expanded=False)
    st.subheader("Recovery")
    incidents = safe(client.mon_recovery, []) or []
    if not incidents:
        st.caption("No recovery incidents in the last 7 days.")
    for inc in incidents:
        crash = inc["crash"]
        st.markdown(f"**{crash['occurred_at'][:19].replace('T', ' ')} — worker "
                    f"{crash['instance_id']} ended unexpectedly**")
        restart = {"RESTARTED": "🔄 Worker restarted (a new worker process started)",
                   "GAVE_UP": "⛔ Worker not restarted: the supervisor gave up",
                   "NOT_RESTARTED": "⏸️ No restart recorded"}[inc["worker_restart"]]
        st.markdown(f"- {restart}")
        if not inc["runs"]:
            st.markdown("- No research run was in progress.")
        for r in inc["runs"]:
            outcome = {"RESUMED": "✅ Research resumed from its checkpoint",
                       "RECOVERY_FAILED": "❌ Research recovery failed",
                       "PENDING": "⏳ Research not resumed yet"}[r["research_outcome"]]
            st.markdown(f"- Run {r['run_id'][:8]}: {outcome} (now {r['current_status']})")
            for e in r["steps"]:
                st.caption(f"    {e['occurred_at'][11:19]} · {e['label']}")

# --- usage & costs ----------------------------------------------------------------------

def usage_table(groups: list[dict[str, Any]], label: str) -> pd.DataFrame:
    key = "field" if label == "Field" else "group"
    return pd.DataFrame([{
        label: g[key] + (f" ({g['city']})" if g.get("city") else ""),
        "Model calls": g["invocations"], "Cache hits": g["cache_hits"],
        "Input tokens": tokens(g, "input_tokens"), "Output tokens": tokens(g, "output_tokens"),
        "Retries": g["retries"], "Failed": g["failed_calls"],
        "Median latency": latency(g["latency_p50_ms"]), "p95": latency(g["latency_p95_ms"]),
        "Estimated cost": cost(g)} for g in groups])

def cache_note(t: dict[str, Any]) -> str:
    """Application cache (no model call) and provider prompt cache (a model call whose repeated
    prompt prefix the provider billed as cached input), kept apart."""
    app = (f"Application cache: {t.get('cache_hits', 0)} call(s) answered without the model "
           f"({t.get('memory_cache_hits', 0)} in process, "
           f"{t.get('persistent_cache_hits', 0)} from the persistent cache")
    if t.get("tokens_avoided"):
        app += f"; {t['tokens_avoided']:,} tokens not spent"
    if t.get("avoided_unknown_hits"):
        app += f"; {t['avoided_unknown_hits']} hit(s) of unknown size"
    provider = t.get("cached_tokens")
    return app + "). Provider prompt cache: " + (
        f"{provider:,} input tokens were billed as cached (they are part of input tokens)."
        if provider is not None else "Unknown (not reported).")

def render_usage(client: Any) -> None:
    c = st.columns(2)
    window = c[0].selectbox("Period", list(WINDOWS), key="mon_usage_window")
    group = c[1].radio("Group by", ["Provider & model", "City", "Run", "Spend category"],
                       horizontal=True, key="mon_usage_group")
    group_by = {"Provider & model": "provider_model", "City": "city", "Run": "run",
                "Spend category": "category"}[group]
    u = safe(lambda: client.mon_usage(WINDOWS[window] or None, group_by))
    if not u:
        return
    t = u["total"]
    c = st.columns(5)
    c[0].metric("Model calls", t["invocations"])
    c[1].metric("Cache hits", t["cache_hits"], help="Answered from the cache: no model call")
    c[2].metric("Input tokens", tokens(t, "input_tokens"))
    c[3].metric("Output tokens", tokens(t, "output_tokens"))
    c[4].metric("Estimated cost", cost(t))
    c = st.columns(4)
    c[0].metric("Retries", t["retries"])
    c[1].metric("Failed calls", t["failed_calls"])
    c[2].metric("Median latency", latency(t["latency_p50_ms"]))
    c[3].metric("p95 latency", latency(t["latency_p95_ms"]))
    st.caption(cache_note(t))
    if t["attempt_errors"]:
        st.caption("Errors on individual attempts (retried or final): "
                   + ", ".join(f"{k}: {v}" for k, v in t["attempt_errors"].items()))
    if t["unknown_cost_calls"]:
        st.info(f"Cost is Unknown for {t['unknown_cost_calls']} model call(s): their "
                "provider/model has no price in config/llm_prices.yaml (the VT models have "
                "none). Unknown costs are not counted as zero.")
    if u["groups"]:
        st.dataframe(usage_table(u["groups"], group), hide_index=True, width="stretch")
    else:
        st.caption("No model calls recorded in this period.")
