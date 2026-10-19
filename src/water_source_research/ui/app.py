"""Water-source research dashboard (Streamlit).

Run:  uv run streamlit run src/water_source_research/ui/app.py
Needs the API (uvicorn) and at least one worker (scripts/run_research_worker.py) running.
Set RESEARCH_API_URL if the API isn't on http://localhost:8000.
"""

import hashlib
import json
import os
from typing import Any
import pandas as pd
import streamlit as st
from water_source_research.schemas.standardized import COLUMNS, HEADERS
from water_source_research.ui.client import ApiError, ResearchApiClient
from water_source_research.ui.evaluation import render_evaluation

ACTIVE = {"QUEUED", "RUNNING"}
STATUS_ICON = {"QUEUED": "⏳", "RUNNING": "🔄", "AWAITING_REVIEW": "🧑‍⚖️", "SUCCEEDED": "✅",
               "FAILED": "❌", "CANCELLED": "🚫"}
NODE_LABEL = {"plan": "Plan", "discover": "Discover sources", "acquire": "Acquire documents",
              "extract": "Extract claims", "validate": "Validate claims",
              "resolve": "Resolve fields", "human_review": "Human review",
              "assemble": "Assemble records", "finalize": "Finalize"}

st.set_page_config(page_title="Water Source Research", page_icon="💧", layout="wide")

# --- client & sidebar ------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")
    api_url = st.text_input("API URL", os.environ.get("RESEARCH_API_URL",
                                                      "http://localhost:8000"))
    refresh_seconds = st.slider("Auto-refresh (seconds)", 2, 30, 3)
    # Recorded with every review decision (there is no login; this is the audit identity).
    reviewer = st.text_input("Reviewer", key="reviewer",
                             help="Your name or email, recorded with each review decision")
    mode = st.radio("Mode", ["Research", "Evaluation"], horizontal=True, key="mode",
                    help="Evaluation compares saved research runs with the reference workbook")
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

if mode == "Evaluation":
    render_evaluation(client, reviewer)
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
selected_run = next(r for r in runs if r["run_id"] == run_id)
is_active = selected_run["status"] in ACTIVE
if selected_run["status"] == "AWAITING_REVIEW":
    st.warning(f"🧑‍⚖️ This run is waiting for human review (round "
               f"{selected_run.get('review_round')}). Decide the open items in the "
               "**Review queue** tab, then resume the run.")

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
    if run["status"] in ACTIVE | {"AWAITING_REVIEW"} and st.button("🚫 Cancel run"):
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
    picked = st.multiselect("Reason", reasons, default=reasons,
                            key=f"review_reasons:{run_id}")
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
    pick_types = f1.multiselect("Field type", types, default=types,
                                key=f"field_types:{run_id}")
    pick_status = f2.multiselect("Status", statuses, default=statuses,
                                 key=f"field_statuses:{run_id}")
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
                       key=f"provenance_field:{run_id}")
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

DECISION_LABEL = {
    "APPROVE": "Approve / select claim(s)", "REJECT": "Reject claim(s)",
    "CORRECT": "Correct the value (with evidence)", "ASSIGN_SOURCE": "Assign to a water source",
    "MARK_UNKNOWN": "Mark Unknown (applies, not found)", "MARK_NA": "Mark N/A (does not apply)",
    "DEFER": "Defer (finalize as Unknown for now)",
}
CLAIM_DECISIONS = {"APPROVE", "REJECT", "ASSIGN_SOURCE"}

def render_decisions(run: dict[str, Any]) -> None:
    """Review queue: pending items of the round the run is waiting on, and decision forms."""
    if run["status"] != "AWAITING_REVIEW":
        st.info(f"This run is {run['status']}: nothing is waiting for review.")
        render_history()
        return
    pending = client.review_pending(run_id)
    items = pending["items"]
    decided = sum(1 for i in items if i.get("decision"))
    st.markdown(f"**Review round {pending['round']}** · {len(items)} item(s) · "
                f"{decided} decided")
    if not reviewer:
        st.warning("Enter your name or email as **Reviewer** in the sidebar to decide.")
    for item in items:
        render_item(item)
    st.divider()
    if st.button("✅ Submit decisions & resume the run", type="primary",
                 disabled=not reviewer or decided < len(items)):
        try:
            client.resume_review(run_id, reviewer)
            st.success("Run resumed: it is back in the queue.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))
    if decided < len(items):
        st.caption("Every item needs a decision before the run can resume.")
    render_history()

def render_item(item: dict[str, Any]) -> None:
    p = item["payload"]
    entity = p["entity"].get("label") or p["entity"]["key"]
    current = item.get("decision")
    title = (f"{'✅' if current else '⏳'} {item['field_key']} · {entity} · "
             f"{', '.join(item['reason_codes'])}")
    with st.expander(title, expanded=not current):
        if current:
            st.success(f"Current decision: **{current['decision']}** by {current['reviewer']}"
                       + (f" — {current['reason']}" if current.get("reason") else ""))
        candidates = p.get("candidates", [])
        if candidates:
            cols = st.columns(len(candidates))
            for col, cand in zip(cols, candidates, strict=True):
                with col:
                    render_candidate(cand)
        else:
            st.caption("No candidate claim: nothing in the acquired evidence supported this "
                       "field.")
        decision_form(item, candidates)

def render_candidate(cand: dict[str, Any]) -> None:
    v = cand.get("validation") or {}
    badge = {"VALID": "🟢", "VALID_WITH_WARNINGS": "🟡", "INVALID": "🔴"}.get(
        v.get("outcome"), "⚪")
    st.markdown(f"{badge} **{cand['value']}**  \n`{cand['claim_uid']}`")
    q = cand.get("qualifiers") or {}
    st.caption(f"normalized: {cand.get('normalized_value')} · origin: {cand['value_origin']} · "
               f"year: {q.get('reference_year') or '—'} · sources: "
               f"{', '.join(q.get('source_types') or []) or '—'} · "
               f"confidence: {cand['confidence']}")
    for check in v.get("checks", []):
        if check["result"] != "PASS":
            st.caption(f"{'❌' if check['result'] == 'FAIL' else '⚠️'} {check['code']}: "
                       f"{check.get('detail') or ''}")
    for cite in cand.get("citations", []):
        page = f", p. {cite['page_number']}" if cite.get("page_number") is not None else ""
        st.markdown(f"[{cite.get('title') or cite['url']}]({cite['url']}){page} · "
                    f"{cite.get('source_type') or '—'} · {cite.get('source_date') or 'n.d.'}")
        st.markdown("> " + (cite.get("quote") or "").replace("\n", " ")[:600])
        st.link_button("Open source", cite["url"])

def decision_form(item: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    p = item["payload"]
    allowed = p.get("allowed_decisions", [])
    with st.form(f"decide:{item['id']}"):
        kind = st.radio("Decision", allowed, format_func=lambda d: DECISION_LABEL.get(d, d),
                        key=f"kind:{item['id']}")
        labels = {c["claim_uid"]: f"{c['value']} ({c['claim_uid'][:8]})" for c in candidates}
        claims = st.multiselect("Claim(s) — for approve / reject / assign", list(labels),
                                format_func=labels.get, key=f"claims:{item['id']}")
        sources = {s["key"]: s["label"] for s in p.get("sources") or []}
        target = st.selectbox("Water source — for assign", [""] + list(sources),
                              format_func=lambda k: sources.get(k, "—"),
                              key=f"target:{item['id']}")
        value = st.text_input("Corrected value", key=f"value:{item['id']}")
        origin = st.selectbox("Value origin", ["REPORTED", "CALCULATED", "ESTIMATED",
                                               "ALLOCATED"], key=f"origin:{item['id']}")
        category = st.text_input("Correction category (optional)", key=f"cat:{item['id']}")
        reason = st.text_area("Reason (required for correct / unknown / N/A / defer)",
                              key=f"reason:{item['id']}")
        chunks = sorted({c["chunk_id"] for cand in candidates for c in cand["citations"]
                         if c.get("chunk_id")})
        source_kind = st.radio("Evidence for a correction",
                               ["Quote from the run's evidence", "Reviewer-supplied source"],
                               horizontal=True, key=f"evkind:{item['id']}")
        chunk_id = st.selectbox("Evidence chunk", [""] + chunks, key=f"chunk:{item['id']}")
        quote = st.text_area("Verbatim quote from that chunk", key=f"quote:{item['id']}")
        url = st.text_input("Source URL", key=f"url:{item['id']}")
        source_title = st.text_input("Source title", key=f"title:{item['id']}")
        note = st.text_input("What the source says (note)", key=f"note:{item['id']}")
        if not st.form_submit_button("Save decision", disabled=not reviewer):
            return
    body: dict[str, Any] = {"item_id": item["id"], "round": item["round"], "decision": kind,
                            "reason": reason or None,
                            "correction_category": category or None}
    if kind in CLAIM_DECISIONS:
        body["claim_uids"] = claims
    if kind == "ASSIGN_SOURCE":
        body["entity_key"] = target or None
    if kind == "CORRECT":
        body.update(value=parse_value(value), value_origin=origin, claim_uids=claims)
        body["evidence"] = ({"chunk_id": chunk_id or None, "quote": quote}
                            if source_kind.startswith("Quote")
                            else {"url": url, "title": source_title or None,
                                  "note": note or None})
    # Same content -> same key: a double-click doesn't record two decisions.
    body["decision_key"] = hashlib.sha1(
        json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:32]
    try:
        client.submit_decisions(run_id, reviewer, [body])
        st.success("Decision saved.")
        st.rerun()
    except ApiError as exc:
        st.error(str(exc))

def parse_value(text: str) -> Any:
    """Numbers as numbers ("980000" -> 980000), anything else as text."""
    try:
        value = json.loads(text)
        return value if isinstance(value, int | float) else text
    except ValueError:
        return text

def render_history() -> None:
    history = client.review_history(run_id)
    if not history["decisions"]:
        return
    st.markdown("**Review history (append-only)**")
    st.dataframe(pd.DataFrame([{
        "round": d["round"], "at": (d["created_at"] or "")[:19].replace("T", " "),
        "reviewer": d["reviewer"], "field": d["field_key"], "entity": d["entity_key"],
        "decision": d["decision"], "from": json.dumps(d["original_value"]),
        "to": json.dumps(d["reviewed_value"]), "reason": d["reason"] or "",
        "supersedes": d["supersedes_id"] or "",
    } for d in history["decisions"]]), hide_index=True, width="stretch")

COMPLETION_ICON = {"FINALIZED": "✅", "FINALIZED_WITH_GAPS": "🟡", "INCOMPLETE": "❌"}

def render_records(run: dict[str, Any]) -> None:
    """SupplyRecords as exported, quality checks, downloads, and a per-cell provenance trace."""
    if run["status"] != "SUCCEEDED":
        st.info(f"Records are assembled when the run finishes (it is {run['status']}).")
        return
    data = client.records(run_id)
    completion = data.get("completion")
    if not data["records"]:
        st.info("This run finished before records were assembled.")
        return
    st.markdown(f"**Completion:** {COMPLETION_ICON.get(completion, '')} {completion} · "
                f"{len(data['records'])} supply record(s)")
    failed = [c for c in data["quality"] if c["result"] != "PASS"]
    if failed:
        st.dataframe(pd.DataFrame(failed), hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame([r["row"] for r in data["records"]]), hide_index=True,
                 width="stretch")

    if completion == "INCOMPLETE":
        st.error("Export is blocked until the open fields / failed checks are resolved.")
    else:
        # Each generated file is recorded in the export audit trail, so files are generated
        # on request (Prepare), not on every page render.
        cols = st.columns(3)
        for col, (label, fmt, kind) in zip(cols, [
                ("Standardized CSV", "csv", "standardized"),
                ("Workbook (XLSX)", "xlsx", "standardized"),
                ("Provenance CSV", "csv", "provenance")], strict=True):
            slot = f"export:{fmt}:{kind}:{run_id}"
            if col.button(f"Prepare {label}", key=f"prepare:{slot}"):
                try:
                    st.session_state[slot] = client.export(run_id, fmt, kind, reviewer or None)
                except ApiError as exc:
                    col.error(str(exc))
            if slot in st.session_state:
                content, filename, media = st.session_state[slot]
                col.download_button(f"⬇️ {label}", content, file_name=filename, mime=media,
                                    key=f"download:{slot}")

    st.markdown("**Trace a value to its evidence**")
    labels = {r["record_key"]: r["water_source"] or "Unknown source" for r in data["records"]}
    a, b = st.columns(2)
    record = a.selectbox("Record", list(labels), format_func=labels.get,
                         key=f"trace_record:{run_id}")
    field = b.selectbox("Column", [k for k, _ in COLUMNS], format_func=HEADERS.get,
                        key=f"trace_field:{run_id}")
    trace = client.trace(run_id, record, field)
    value = trace["value"]
    st.markdown(f"**{trace['column']}:** {value.get('value')} · {value['value_status']} · "
                f"origin {value.get('value_origin') or '—'} · {value['resolution_status']}")
    if value.get("calculation"):
        calc = value["calculation"]
        st.caption(f"Calculated ({calc['rule']}): {calc['formula']}")
    if value.get("note"):
        st.caption(value["note"])
    for d in trace["review_decisions"]:
        st.caption(f"Reviewer {d['reviewer']}: {d['decision']} — {d.get('reason') or ''}")
    for claim in trace["claims"] + [c for i in trace["inputs"] for c in i["claims"]]:
        for cite in claim["citations"]:
            doc = cite.get("document") or {}
            st.markdown(f"Claim `{claim['claim_uid']}` = **{claim['value']}** → "
                        f"[{cite.get('title') or cite['url']}]({cite['url']}) · "
                        f"{doc.get('source_type') or '—'} · {doc.get('acquisition_method') or ''}")
            st.markdown("> " + (cite.get("quote") or "").replace("\n", " ")[:600])

def dashboard() -> None:
    progress_tab, queue_tab, review_tab, fields_tab, records_tab = st.tabs(
        ["📈 Progress & step outputs", "✍️ Review queue", "🧑‍⚖️ Human review log",
         "🧬 Field tracking & provenance", "📦 Records & export"])
    with progress_tab:
        render_progress(client.steps(run_id))
    with queue_tab:
        render_decisions(selected_run)
    with review_tab:
        render_review(client.review(run_id))
    with fields_tab:
        render_fields(client.fields(run_id))
    with records_tab:
        render_records(selected_run)

# Poll only while the run is queued/running; finished runs render once.
st.fragment(run_every=refresh_seconds if is_active else None)(dashboard)()
