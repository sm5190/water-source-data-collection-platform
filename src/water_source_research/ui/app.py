"""Water-source research dashboard (Streamlit).

Run:  uv run streamlit run src/water_source_research/ui/app.py
Needs the API (uvicorn) and at least one worker (scripts/run_research_worker.py) running.
Set RESEARCH_API_URL if the API isn't on http://localhost:8000.
"""

import json
import os
from typing import Any
import pandas as pd
import streamlit as st
from water_source_research.ui.client import ApiError, ResearchApiClient

ACTIVE = {"QUEUED", "RUNNING"}
STATUS_ICON = {"QUEUED": "⏳", "RUNNING": "🔄", "SUCCEEDED": "✅", "FAILED": "❌",
               "CANCELLED": "🚫"}
NODE_LABEL = {"plan": "Plan", "discover": "Discover sources", "acquire": "Acquire documents",
              "extract": "Extract claims", "validate": "Validate", "human_review": "Human review",
              "finalize": "Finalize"}

st.set_page_config(page_title="Water Source Research", page_icon="💧", layout="wide")

# --- client & sidebar ------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")
    api_url = st.text_input("API URL", os.environ.get("RESEARCH_API_URL",
                                                      "http://localhost:8000"))
    refresh_seconds = st.slider("Auto-refresh (seconds)", 2, 30, 3)
client: Any = st.session_state.get("client") or ResearchApiClient(api_url)

with st.sidebar:
    if client.health():
        st.success("API connected")
        try:
            stats = client.queue_stats()
            st.caption("Queue")
            cols = st.columns(2)
            for i, (status, n) in enumerate(sorted(stats.get("by_status", {}).items())):
                cols[i % 2].metric(status.title(), n)
            if stats.get("oldest_queued_age_seconds"):
                st.caption(f"Oldest queued: {stats['oldest_queued_age_seconds']:.0f}s ago")
        except ApiError as exc:
            st.warning(str(exc))
    else:
        st.error(f"API not reachable at {api_url}")
        st.stop()

st.title("💧 Water Source Research")

# --- queue a city run ------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def load_cities(url: str) -> list[dict[str, Any]]:
    return client.cities()

cities = load_cities(api_url)
labels = [c["label"] + (f" — {c['jurisdiction']}" if c.get("jurisdiction") else "")
          for c in cities]

left, right = st.columns([3, 1], vertical_alignment="bottom")
choice = left.selectbox(f"City ({len(cities)} in catalog)", range(len(cities)),
                        format_func=lambda i: labels[i], key="city_index")
priority = right.number_input("Priority", value=0, step=1, help="Higher runs first")
city = cities[choice]

if st.button("▶ Start city run", type="primary"):
    try:
        submitted = client.submit(city["city"], city["country"], int(priority))
        st.session_state["run_id"] = submitted["run_id"]
        if submitted.get("created"):
            st.success(f"Queued {city['label']} — run `{submitted['run_id']}`")
        else:
            st.info(f"{city['label']} already has an active run "
                    f"(`{submitted['run_id']}`, {submitted['status']}); showing it.")
    except ApiError as exc:
        st.error(str(exc))

# --- runs for the selected city --------------------------------------------------------

runs = client.runs(city=city["city"], country=city["country"])
if not runs:
    st.caption(f"No runs yet for {city['label']}.")
    st.stop()

run_ids = [r["run_id"] for r in runs]
default = run_ids.index(st.session_state["run_id"]) \
    if st.session_state.get("run_id") in run_ids else 0
run_id = st.selectbox(
    "Run", run_ids, index=default,
    format_func=lambda rid: next(f"{STATUS_ICON.get(r['status'], '')} {r['status']} · "
                                 f"{(r['created_at'] or '')[:19].replace('T', ' ')} · {rid[:8]}"
                                 for r in runs if r["run_id"] == rid))
st.session_state["run_id"] = run_id
is_active = next(r for r in runs if r["run_id"] == run_id)["status"] in ACTIVE

# --- dashboard -------------------------------------------------------------------------

def render_progress(data: dict[str, Any]) -> None:
    run = data["run"]
    steps = data["steps"]
    expected = data["expected_nodes"]
    latest_attempt = max((s["attempt"] for s in steps), default=0)
    done = {s["node"] for s in steps if s["attempt"] == latest_attempt}
    if run["status"] == "SUCCEEDED":
        fraction = 1.0
    else:
        fraction = len(done & set(expected)) / len(expected)

    c = st.columns(5)
    c[0].metric("Status", f"{STATUS_ICON.get(run['status'], '')} {run['status']}")
    c[1].metric("Attempt", f"{run['attempts']} / {run['max_attempts']}")
    c[2].metric("Steps completed", len(steps))
    c[3].metric("Resumed from checkpoint", "yes" if run.get("resumed") else "no")
    c[4].metric("Started", (run.get("started_at") or "—")[11:19] or "—")
    last = steps[-1]["node"] if steps else None
    label = (f"Last completed step: {NODE_LABEL.get(last, last)}" if last
             else "Waiting for a worker…")
    st.progress(fraction, text=label)
    if run.get("last_error"):
        st.error(f"Last error: {run['last_error']}")
    if run["status"] in ACTIVE and st.button("🚫 Cancel run"):
        client.cancel(run["run_id"])
        st.rerun()

    for attempt in sorted({s["attempt"] for s in steps}):
        st.markdown(f"**Attempt {attempt}**")
        for s in [s for s in steps if s["attempt"] == attempt]:
            title = (f"✅ {s['seq']}. {NODE_LABEL.get(s['node'], s['node'])} "
                     f"· {s['duration_ms']:.0f} ms")
            with st.expander(title):
                render_step_output(s["summary"])

def render_step_output(summary: dict[str, Any]) -> None:
    facts = {k: v for k, v in summary.items() if k not in ("events", "events_truncated")}
    if facts:
        st.json(facts, expanded=True)
    events = summary.get("events") or []
    if events:
        st.caption(f"Workflow events ({len(events)})")
        st.dataframe(pd.DataFrame([{
            "seq": e.get("seq"), "kind": e.get("kind"), "field": e.get("field_key"),
            "capability": e.get("capability"), "result": e.get("result"),
            "strategy": e.get("strategy"), "details": json.dumps(e.get("attrs") or {})[:160],
        } for e in events]), hide_index=True, width="stretch")

def render_review(data: dict[str, Any]) -> None:
    if not data["available"]:
        st.info("The human review log is available once the run has finished.")
        return
    items = data["items"]
    if not items:
        st.success("No fields required human review.")
        return
    reasons = sorted({i["reason_code"] for i in items})
    picked = st.multiselect("Reason", reasons, default=reasons, key="review_reasons")
    rows = [i for i in items if i["reason_code"] in picked]
    st.dataframe(pd.DataFrame([{
        "field": i["field_key"], "field type": i["field_type"], "status": i["field_status"],
        "reason": i["reason"], "decision": i.get("decision") or "",
        "citations": len(i["citations"]),
    } for i in rows]), hide_index=True, width="stretch")
    for i in rows:
        with st.expander(f"{i['field_key']} — {i['reason']}"):
            if i.get("decision"):
                st.write(f"**Decision:** {i['decision']}"
                         + (f" · category: {i['correction_category']}"
                            if i.get("correction_category") else "")
                         + (f" · {i['logged_at']}" if i.get("logged_at") else ""))
            render_citations(i["citations"])

def render_citations(citations: list[dict[str, Any]]) -> None:
    if not citations:
        st.caption("No citations — no evidence-backed claim exists for this field.")
        return
    for n, c in enumerate(citations, 1):
        title = c.get("title") or c.get("url")
        page = f", p. {c['page_number']}" if c.get("page_number") is not None else ""
        value = f" — claim: **{c['claim_value']}**" if "claim_value" in c else ""
        st.markdown(f"{n}. [{title}]({c.get('url')}){page}{value}")
        if c.get("passage"):
            st.markdown("> " + c["passage"].replace("\n", " ")[:800])

def render_fields(data: dict[str, Any]) -> None:
    if not data["available"]:
        st.info("Field tracking is available once the run has finished.")
        return
    fields = data["fields"]
    statuses = sorted({f["status"] for f in fields})
    types = sorted({f["field_type"] for f in fields})
    c = st.columns(len(statuses) or 1)
    for i, status in enumerate(statuses):
        c[i].metric(status.title(), sum(f["status"] == status for f in fields))
    f1, f2 = st.columns(2)
    pick_types = f1.multiselect("Field type", types, default=types, key="field_types")
    pick_status = f2.multiselect("Status", statuses, default=statuses, key="field_statuses")
    shown = [f for f in fields if f["field_type"] in pick_types and f["status"] in pick_status]
    st.dataframe(pd.DataFrame([{
        "field": f["field_key"], "type": f["field_type"], "status": f["status"],
        "value": "" if f["value"] is None else str(f["value"]),
        "confidence": f["confidence"], "citations": len(f["citations"]),
        "candidates": len(f["candidates"]), "strategy": f["strategy"],
        "derived": f["derived"],
    } for f in shown]), hide_index=True, width="stretch")

    if not shown:
        return
    key = st.selectbox("Provenance for field", [f["field_key"] for f in shown],
                       key="provenance_field")
    f = next(x for x in shown if x["field_key"] == key)
    a, b = st.columns(2)
    with a:
        st.markdown(f"**Status:** {f['status']}  \n**Value:** {f['value']}  \n"
                    f"**Origin:** {f['value_origin'] or '—'} · "
                    f"**Confidence:** {f['confidence'] if f['confidence'] is not None else '—'}"
                    f"  \n**Strategy:** {f['strategy']}  \n"
                    f"**Context bundle:** `{f['context_bundle_id'] or '—'}`")
        if f["derived"]:
            st.caption(f"Derived by the rules engine from: {', '.join(f['depends_on'])}")
        st.markdown("**Citations (selected value)**")
        render_citations(f["citations"])
    with b:
        st.markdown("**Status history**")
        st.dataframe(pd.DataFrame(f["transitions"] or [{"at": "", "node": "", "from": "",
                                                         "to": "no transitions"}]),
                     hide_index=True, width="stretch")
        st.markdown("**Extraction attempts**")
        st.dataframe(pd.DataFrame(f["extraction_attempts"] or [{"bundle_status": "none"}]),
                     hide_index=True, width="stretch")
        if f["reviewer_decisions"]:
            st.markdown("**Reviewer decisions**")
            st.dataframe(pd.DataFrame(f["reviewer_decisions"]), hide_index=True)
    if f["candidates"]:
        st.markdown(f"**Candidate claims ({len(f['candidates'])})**")
        for n, cand in enumerate(f["candidates"], 1):
            with st.expander(f"Candidate {n}: {cand['value']} "
                             f"(confidence {cand['confidence']})"):
                render_citations(cand["citations"])

def dashboard() -> None:
    progress_tab, review_tab, fields_tab = st.tabs(
        ["📈 Progress & step outputs", "🧑‍⚖️ Human review log", "🧬 Field tracking & provenance"])
    with progress_tab:
        render_progress(client.steps(run_id))
    with review_tab:
        render_review(client.review(run_id))
    with fields_tab:
        render_fields(client.fields(run_id))

# Poll only while the run is queued/running; finished runs render once.
st.fragment(run_every=refresh_seconds if is_active else None)(dashboard)()
