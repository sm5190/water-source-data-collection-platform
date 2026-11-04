"""Simple human review: one card per field, plain decisions, no typing unless correcting.

Designed for reviewers with no training on the system. Each card shows the value the system
proposes, its quote and a link to the source, any other values found, and five choices:
Accept, Reject (none of the values found is right -> the field needs more research), Correct
(pick another value found, or enter one with a source link), Unknown, Not applicable.
Decisions are saved one at a time (the API keeps every decision; the latest one per field
counts), so a reviewer can stop and come back. Claim ids, confidence and validation codes are
never shown here; the technical form is behind a toggle.

Everything the audit trail needs (reviewer, decision, claims, values, evidence, time, status
before and after) is filled in by the API from these choices.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any
import streamlit as st
from water_source_research.schemas.standardized import COLUMNS, HEADERS
from water_source_research.services.field_state import load_field_definitions
from water_source_research.services.review import proposal_key
from water_source_research.ui.client import ApiError

ORDER = {key: n for n, (key, _) in enumerate(COLUMNS)}
OFFICIAL_DEFAULT = ("UTILITY", "GOVERNMENT", "REGULATORY")
ACCEPT, REJECT, CORRECT, UNKNOWN, NA = ("Accept", "Reject", "Correct", "Unknown",
                                        "Not applicable")
CHOICE_HELP = {
    ACCEPT: "The proposed value is correct and supported by the source.",
    REJECT: "None of the values found is right. The field will be marked "
            "'Needs more research'.",
    CORRECT: "Pick another value that was found, or enter the right value with a source link.",
    UNKNOWN: "The information applies to this city but no reliable value exists.",
    NA: "The information does not apply to this city or water source.",
}
SAVED_LABEL = {"APPROVE": "Accepted", "REJECT": "Rejected (needs more research)",
               "CORRECT": "Corrected", "MARK_UNKNOWN": "Unknown", "MARK_NA": "Not applicable",
               "DEFER": "Deferred", "ASSIGN_SOURCE": "Assigned to a water source"}
# Plain-language hints for failed or warned checks (codes are never shown).
CHECK_HINT = {
    "VALUE_IN_QUOTE": "the number in the quote looks different from this value",
    "CITATION_LINKED": "the quote could not be found in the source text",
    "GEOGRAPHIC_SCOPE": "the source may describe a different area than the city",
    "MEASUREMENT_BASIS": "the source may measure something slightly different",
    "APPROXIMATE_VALUE": "the source gives an approximate value",
    "REFERENCE_YEAR": "the year of this value may be old or unclear",
    "TYPE": "the value has an unexpected format", "ENUM": "the value is not an allowed option",
    "RANGE": "the value is outside the expected range",
    # semantic checks (services/semantics.py): the value appears, but does it answer the field?
    "CITY_NOT_NAMED": "the source doesn't say this is about the city",
    "WRONG_PLACE": "the source seems to be about a different place",
    "JURISDICTION_LEVEL": "this looks like a county/district, not the state or province",
    "JURISDICTION_LEVEL_UNKNOWN": "check this is the state or province (first level)",
    "MEASUREMENT_TERM": "the source doesn't say this number is a population",
    "SERVICE_AREA_POPULATION": "this may be the utility's service-area population, "
                               "not the city's",
    "MEASUREMENT_TYPE_MISMATCH": "this may be water produced, treated or delivered (or a "
                                 "capacity), not water withdrawn",
    "MEASUREMENT_TYPE_UNKNOWN": "the source doesn't say this is water withdrawn",
    "MEASUREMENT_YEAR": "the source doesn't give the year of this number",
    "FRESHNESS_UNKNOWN": "it's unclear how recent this number is",
    "POSSIBLY_SUPERSEDED": "this number is old; a more recent one may exist",
    "YEAR_NOT_LINKED": "the year isn't clearly the year of the measurement "
                       "(it may be a page or publication date)",
    "DESIGNATED_SOURCE_MISSING": "city population needs the designated population list; "
                                 "check the value and its year",
    "UTILITY_SERVICE_NOT_STATED": "the source doesn't show this utility serves the city",
}

def field_label(field_key: str) -> str:
    return HEADERS.get(field_key, field_key.replace("_", " ").capitalize())

def is_invalid(cand: dict[str, Any]) -> bool:
    return ((cand.get("validation") or {}).get("outcome")) == "INVALID"

def ordered_candidates(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Proposed value first: passes validation, then an official source type, then the value
    re-found in its quote, then the order the claims were found. Confidence is never used."""
    d = load_field_definitions().get(item["field_key"])
    official = set(d.official_sources) if d and d.official_sources else set(OFFICIAL_DEFAULT)
    cands = list(item["payload"].get("candidates") or [])

    def rank(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
        n, c = pair
        v = c.get("validation") or {}
        types = {x.get("source_type") for x in c.get("citations") or []} | set(
            (c.get("qualifiers") or {}).get("source_types") or [])
        checks = {k["code"]: k["result"] for k in v.get("checks", [])}
        return proposal_key(v.get("outcome"), types, checks.get("VALUE_IN_QUOTE"), official, n)
    return [c for _, c in sorted(enumerate(cands), key=rank)]

def show_value(value: Any, unit: str | None = None) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int | float):
        text = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.3f}".rstrip("0")
    elif isinstance(value, list):
        text = ", ".join(str(v) for v in value)
    else:
        text = str(value)
    pretty_unit = {"m3/day": "m³/day", "million_people": "million people",
                   "percent": "%"}.get(unit or "", "")
    return f"{text} {pretty_unit}".strip()

# --- context for the reviewer: geography, entity type, relationships (information only) ------

PLACE_TEXT = {
    "CONFIRMED": "names the city (with its region or country)",
    "PROBABLE": "probably about the city",
    "SECTION": "the section it is in is about the city",
    "AMBIGUOUS": "names the city, but another place has the same name",
    "HOMONYM": "seems to be about another place with the same name",
    "OTHER_PLACE": "seems to be about another place",
    "NONE": "doesn't name the city",
}
SIGNAL_TEXT = {
    "PASSAGE_OTHER_PLACE": "a cited passage seems to be about another place",
    "PASSAGE_PLACE_UNCERTAIN": "no cited passage clearly places this in the city",
    "OUTSIDE_SOURCE_LOCATED": "the source is located outside the city (normal for distant "
                              "or upstream sources: check it supplies the city)",
}
KIND_TEXT = {
    "river": "river", "spring": "spring", "aquifer": "aquifer (groundwater)",
    "natural_lake": "natural lake", "lake": "lake", "wellfield": "wellfield (groundwater)",
    "groundwater": "groundwater (general)", "sea": "sea", "reclaimed": "reclaimed water",
    "desalination": "desalinated water",
    "lake_unspecified": "lake (not shown to be natural; it may be a reservoir)",
    "reservoir": "reservoir (man-made; not a natural source)",
    "infrastructure": "infrastructure (plant, intake or pipeline; not a water source)",
    "supply_system": "supply system (not a single water source)",
    "region": "region or area (not a water source)",
}
STATUS_TEXT = {
    "VERIFIED": "✅ Verified (government or regulator statement)",
    "SUPPORTED": "🟡 Supported (one source, or the organisation's own description)",
    "CANDIDATE": "⚪ Candidate (not confirmed)",
    "HISTORICAL": "🕰 Historical (no longer current)",
    "CONTRADICTED": "⛔ Contradicted",
}
RELATION_TEXT = {
    "UTILITY_SERVES_CITY": "serves", "SUPPLIES_WHOLESALE_TO": "supplies water wholesale to",
    "SOURCE_SUPPLIES_UTILITY": "supplies", "SOURCE_SUPPLIES_CITY": "supplies",
    "SOURCE_PART_OF_SYSTEM": "is part of", "PREDECESSOR_OF": "was succeeded by",
    "DEPARTMENT_OF": "is a department of", "SOURCE_LOCATED_IN": "is located in",
}
STATUS_ORDER = {"VERIFIED": 0, "SUPPORTED": 1, "CANDIDATE": 2, "HISTORICAL": 3,
                "CONTRADICTED": 4}

def _norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())

def related(value: Any, relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Relationships whose subject or object is the candidate value (same name, case and
    spacing aside), best status first. Nothing is merged or inferred."""
    v = _norm(value)
    if not v:
        return []
    found = [r for r in relationships
             if v in (_norm(r["subject"]["name"]), _norm(r["object"]["name"]))]
    return sorted(found, key=lambda r: STATUS_ORDER.get(r["status"], 9))

def entity_kind(value: Any, q: dict[str, Any], relationships: list[dict[str, Any]]
                ) -> str | None:
    """The graph's type for this entity (explicit evidence), else the claim's source kind."""
    v = _norm(value)
    for r in relationships:
        for e in (r["subject"], r["object"]):
            if _norm(e["name"]) == v and e.get("type") == "source" and e.get("kind"):
                return e["kind"]
    return q.get("source_kind")

def render_context(c: dict[str, Any], relationships: list[dict[str, Any]]) -> None:
    """Where the cited passages are, the entity's type and its relationships, in plain words.
    Information for the reviewer only: none of it accepted or rejected anything."""
    q = c.get("qualifiers") or {}
    geo = q.get("geo") or {}
    lines = []
    places = [PLACE_TEXT.get(p["place"], p["place"]) for p in geo.get("passages") or []]
    if places:
        lines.append("**Where the passage is:** " + "; ".join(dict.fromkeys(places)))
    if geo.get("source_locations"):
        lines.append("**The source is located in:** " + ", ".join(geo["source_locations"]))
    value = candidate_value(c)
    kind = entity_kind(value, q, relationships) if isinstance(value, str) else None
    if kind:
        lines.append("**Type:** " + KIND_TEXT.get(kind, kind.replace("_", " ")))
    if lines:
        st.caption("  \n".join(lines))
    for s in geo.get("signals") or []:
        if s in SIGNAL_TEXT:
            st.caption(f"Note: {SIGNAL_TEXT[s]}.")
    rels = related(value, relationships) if isinstance(value, str) else []
    if rels:
        with st.expander(f"Relationships found ({len(rels)})"):
            for r in rels:
                st.markdown(f"{STATUS_TEXT.get(r['status'], r['status'])}: **{r['subject']['name']}**"
                            f" {RELATION_TEXT.get(r['kind'], r['kind'])} "
                            f"**{r['object']['name']}**"
                            + (f" — {r['temporal_scope']}" if r.get("temporal_scope") else ""))
                for ev in (r.get("evidence") or [])[:2]:
                    st.markdown("> " + ev["quote"].replace("\n", " ")[:300])
                    st.caption(f"Source: [{ev.get('host') or ev['url']}]({ev['url']})")

def run_relationships(client: Any, run_id: str) -> list[dict[str, Any]]:
    """The run's relationship graph, fetched once per review screen (built on request for a
    run awaiting review); an unavailable graph just shows no relationships."""
    key = f"relationships:{run_id}"
    if key not in st.session_state:
        try:
            st.session_state[key] = (client.relationships(run_id, build=True)
                                     .get("relationships") or [])
        except (ApiError, AttributeError):
            st.session_state[key] = []
    return st.session_state[key]

def candidate_value(c: dict[str, Any]) -> Any:
    value = c.get("normalized_value")
    return c.get("value") if value is None else value

def parse_value(text: str) -> Any:
    """Numbers as numbers ("980,000" -> 980000), anything else as text."""
    raw = text.strip()
    try:
        number = float(raw.replace(",", "").replace(" ", "").replace(" ", ""))
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw

def decision_key(body: dict[str, Any]) -> str:
    """Same content -> same key: a double click doesn't record two decisions."""
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:32]

# --- page -----------------------------------------------------------------------------------

def render_simple_review(client: Any, run: dict[str, Any], reviewer: str,
                         advanced: Callable[[], None] | None = None) -> None:
    run_id = run["run_id"]
    if run["status"] != "AWAITING_REVIEW":
        st.info(f"This run is {run['status']}: nothing is waiting for review.")
        if advanced and st.toggle("Show review history", key=f"adv_hist:{run_id}"):
            advanced()
        return
    pending = client.review_pending(run_id)
    items = sorted(pending["items"], key=lambda i: (ORDER.get(i["field_key"], 99),
                                                    i.get("label") or i["entity_key"]))
    decided = [i for i in items if i.get("decision")]
    city = ", ".join(x for x in (run.get("city_name"), run.get("country")) if x)
    st.markdown(f"### {city}")
    st.progress(len(decided) / len(items) if items else 1.0,
                text=f"{len(decided)} of {len(items)} fields reviewed")
    if reviewer:
        st.caption(f"Reviewing as **{reviewer}** (change it in the sidebar). Each decision is "
                   "saved as you go; you can leave and come back.")
    else:
        st.warning("Type your name in the sidebar (**Your name**) to start reviewing.")
    show = st.radio("Show", ["All", "To do", "Done"], horizontal=True,
                    key=f"review_filter:{run_id}", label_visibility="collapsed")
    multi_source = len({i.get("label") for i in items if i.get("label")}) > 1
    relationships = run_relationships(client, run_id)
    for item in items:
        done = bool(item.get("decision"))
        if (show == "To do" and done) or (show == "Done" and not done):
            continue
        render_card(client, run_id, item, reviewer, multi_source, relationships)

    st.divider()
    remaining = len(items) - len(decided)
    if st.button("✅ Submit review", type="primary", key=f"submit_review:{run_id}",
                 disabled=not reviewer or remaining > 0):
        try:
            client.resume_review(run_id, reviewer)
            st.success("Review submitted. The run will now be finished automatically.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))
    if remaining:
        st.caption(f"{remaining} field{'s' if remaining != 1 else ''} still "
                   f"need{'s' if remaining == 1 else ''} a decision before you can submit.")
    if advanced and st.toggle("Show technical review form and history",
                              key=f"adv:{run_id}"):
        advanced()

def render_card(client: Any, run_id: str, item: dict[str, Any], reviewer: str,
                multi_source: bool, relationships: list[dict[str, Any]] | None = None) -> None:
    p = item["payload"]
    current = item.get("decision")
    title = field_label(item["field_key"])
    if multi_source and item.get("label"):
        title += f" — {item['label']}"
    with st.expander(f"{'✅' if current else '⏳'} {title}", expanded=not current):
        if current:
            st.success(f"Saved: **{SAVED_LABEL.get(current['decision'], current['decision'])}**"
                       f" by {current['reviewer']}. You can change it below.")
        cands = ordered_candidates(item)
        d = load_field_definitions().get(item["field_key"])
        unit = (p.get("field") or {}).get("unit") or (d.unit if d else None)
        if cands:
            proposed, others = cands[0], cands[1:]
            st.markdown("**Proposed value**")
            render_candidate(proposed, unit, big=True, relationships=relationships)
            if others:
                st.markdown(f"**Other values found ({len(others)})**")
                for c in others:
                    render_candidate(c, unit, relationships=relationships)
        else:
            proposed, others = None, []
            st.info("Nothing was found in the sources for this field.")
        decision_controls(client, run_id, item, reviewer, proposed, others)

def render_candidate(c: dict[str, Any], unit: str | None, big: bool = False,
                     relationships: list[dict[str, Any]] | None = None) -> None:
    q = c.get("qualifiers") or {}
    year = q.get("reference_year")
    value = show_value(candidate_value(c), unit)
    # A short value reads as a headline; a long description stays normal text.
    line = (f"### {value}" if len(value) <= 80 else f"**{value}**") if big else f"- **{value}**"
    year_text = (f"  \n*Publication year: {year} (the source doesn't give the year of the "
                 "number)*" if q.get("year_basis") == "document"
                 else f"  \n*Reporting year: {year}*") if year else ""
    st.markdown(line + year_text)
    hints = [CHECK_HINT[k["code"]] for k in (c.get("validation") or {}).get("checks", [])
             if k["result"] != "PASS" and k["code"] in CHECK_HINT]
    if is_invalid(c):
        st.warning("⚠ The quote may not clearly support this value"
                   + (f" ({'; '.join(dict.fromkeys(hints))})" if hints else "") + ".")
    elif hints:
        st.caption("Note: " + "; ".join(dict.fromkeys(hints)) + ".")
    for cite in c.get("citations") or []:
        if cite.get("quote"):
            st.markdown("> " + cite["quote"].replace("\n", " ")[:600])
        page = f", page {cite['page_number']}" if cite.get("page_number") is not None else ""
        st.markdown(f"Source: [{cite.get('title') or cite['url']}]({cite['url']}){page}")
    render_context(c, relationships or [])

def decision_controls(client: Any, run_id: str, item: dict[str, Any], reviewer: str,
                      proposed: dict[str, Any] | None, others: list[dict[str, Any]]) -> None:
    iid = item["id"]
    p = item["payload"]
    allowed = set(p.get("allowed_decisions") or [])
    choices = ([ACCEPT, REJECT] if proposed else []) + [CORRECT, UNKNOWN, NA]
    choice = st.radio("Your decision", choices, key=f"choice:{iid}", index=None,
                      horizontal=True, captions=[CHOICE_HELP[c] for c in choices])
    body: dict[str, Any] | None = None
    ready = True
    sources = {s["key"]: s["label"] for s in p.get("sources") or []}
    needs_source = item["entity_key"] == "unattributed" and len(sources) > 1

    if choice == ACCEPT and proposed is not None:
        if needs_source:
            target = st.selectbox("Which water source is this value for?", [""] + list(sources),
                                  format_func=lambda k: sources.get(k, "Choose…"),
                                  key=f"source:{iid}")
            ready = bool(target)
            body = {"decision": "ASSIGN_SOURCE", "claim_uids": [proposed["claim_uid"]],
                    "entity_key": target or None}
        else:
            body = {"decision": "APPROVE", "claim_uids": [proposed["claim_uid"]]}
            if is_invalid(proposed):
                ok = st.checkbox("I checked the source and this value is correct",
                                 key=f"override:{iid}")
                ready = ok
                body["override_validation"] = True
    elif choice == REJECT and proposed is not None:
        body = {"decision": "REJECT",
                "claim_uids": [proposed["claim_uid"]] + [c["claim_uid"] for c in others]}
        st.caption("The field will be marked **Needs more research** (not Unknown).")
    elif choice == CORRECT:
        options = [c["claim_uid"] for c in others] + ["__enter__"]
        labels = {c["claim_uid"]: f"Use: {show_value(candidate_value(c))}"
                  for c in others} | {"__enter__": "Enter a different value"}
        how = st.radio("Correct value", options, format_func=labels.get, key=f"how:{iid}") \
            if others else "__enter__"
        if how != "__enter__":
            picked = next(c for c in others if c["claim_uid"] == how)
            body = {"decision": "APPROVE", "claim_uids": [how],
                    "rejected_claim_uids": [proposed["claim_uid"]] if proposed else []}
            if is_invalid(picked):
                ready = st.checkbox("I checked the source and this value is correct",
                                    key=f"override:{iid}")
                body["override_validation"] = True
        else:
            value = st.text_input("Correct value", key=f"value:{iid}")
            url = st.text_input("Source link (required)", key=f"url:{iid}",
                                placeholder="https://…",
                                help="A page or document that states this value.")
            year = st.text_input("Reporting year (optional)", key=f"year:{iid}",
                                 placeholder="e.g. 2024",
                                 help="The year the value refers to, if the source says.")
            url_ok = url.strip().startswith(("http://", "https://"))
            if url.strip() and not url_ok:
                st.caption("The link must start with http:// or https://")
            year_ok = not year.strip() or (year.strip().isdigit() and len(year.strip()) == 4)
            if not year_ok:
                st.caption("The year must be four digits, e.g. 2024")
            ready = bool(value.strip()) and url_ok and year_ok
            evidence: dict[str, Any] = {"url": url.strip()}
            if year.strip() and year_ok:
                evidence["reference_year"] = int(year.strip())
            body = {"decision": "CORRECT", "value": parse_value(value) if value.strip() else None,
                    "claim_uids": [proposed["claim_uid"]] if proposed else [],
                    "evidence": evidence}
    elif choice == UNKNOWN:
        body = {"decision": "MARK_UNKNOWN"}
    elif choice == NA:
        body = {"decision": "MARK_NA"}

    if body is not None and body["decision"] not in allowed:
        st.caption("This choice isn't available for this field.")
        ready = False
    if st.button("Save", key=f"save:{iid}", disabled=body is None or not ready or not reviewer):
        full = {"item_id": iid, "round": item["round"], **body}
        full["decision_key"] = decision_key(full)
        try:
            client.submit_decisions(run_id, reviewer, [full])
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))
