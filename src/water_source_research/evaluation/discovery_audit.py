"""What a run's discovery did, round by round (read only; nothing is written).

For each discovery round: the searches issued (provider, query, results found/retained,
site-restricted target), the documents acquired (host, top-level domain, stored source type,
the query that found them, status), the hosts the run treated as "the utility" at that point
(re-derived with the rule the run used: any OK document classed UTILITY, `agents/nodes.py`
before Milestone 3), and which extraction calls each document fed.

Extraction calls are linked to documents through the bundle the model was shown: the extract
NODE event's ``context_digest`` is the first 16 characters of the stored bundle's
fingerprint (``context_bundles``), whose payload lists the passages' URLs. A document's
"calls fed" and "tokens in calls fed" count every call whose context included it, so they
overlap between documents and are never summed across documents; "sole-source calls" are calls
whose context held only that document.

Sources: ``research_jobs`` (identity, mode), the run's events (``run_events``, else the job
result), ``discovery_results``, ``source_documents``, ``context_bundles`` and, through
``observability.token_audit``, the ``provider_usage`` ledger.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import DiscoveryResultRecord, SourceDocumentRecord
from water_source_research.db.models import ContextBundleRecord
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.observability import token_audit

SITE_PREFIX = "site:"

def host_of(url: str | None) -> str:
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")

def domain_signals(host: str) -> dict[str, Any]:
    """Top-level domain and, for 'gov.co'-style hosts, the government second level."""
    labels = host.split(".") if host else []
    tld = labels[-1] if labels else ""
    second = labels[-2] if len(labels) >= 2 else ""
    gov = second in {"gov", "gob", "go", "govt", "gouv", "gv"} or tld in {"gov", "mil"}
    return {"tld": tld, "cctld": tld if len(tld) == 2 else None, "government_domain": gov}

def site_target(query: str | None) -> str | None:
    """The host of a ``site:host words`` query, else None."""
    q = (query or "").strip()
    if not q.casefold().startswith(SITE_PREFIX):
        return None
    rest = q[len(SITE_PREFIX):].split()
    return rest[0].casefold().removeprefix("www.") if rest else None

def search_calls(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Discover TOOL_CALL events with their round. A round starts at the first discover call
    that follows any other node's event (discover → acquire → extract → discover …)."""
    out: list[dict[str, Any]] = []
    rnd, previous_discover = 0, False
    for e in events:
        is_discover = e.get("node") == "discover"
        if is_discover and e.get("kind") == "TOOL_CALL":
            if not previous_discover:
                rnd += 1
            a = e.get("attrs") or {}
            query = a.get("query")
            out.append({"round": rnd, "provider": e.get("provider"),
                        "strategy": e.get("strategy"), "query": query,
                        "site_target": site_target(query), "field_key": e.get("field_key"),
                        "result": e.get("result"),
                        "found": a.get("sources_found"), "retained": a.get("sources_retained")})
        previous_discover = is_discover
    return out

def utility_hosts_by_round(documents: list[dict[str, Any]], rounds: int) -> dict[int, list[str]]:
    """The hosts the pre-Milestone-3 rule treated as the city's utility when round r began:
    hosts of OK documents classed UTILITY acquired in earlier rounds, most documents first."""
    out: dict[int, list[str]] = {}
    for r in range(1, rounds + 1):
        counts = Counter(d["host"] for d in documents
                         if d["round"] < r and d["status"] == "OK"
                         and (d["source_type"] or "").upper() == "UTILITY" and d["host"])
        out[r] = sorted(counts, key=lambda h: -counts[h])
    return out

def _urls(payload: Mapping[str, Any]) -> list[str]:
    sources = payload.get("sources") or {}
    ids = [i.get("source_id") for i in payload.get("items", [])
           if i.get("kind") == "evidence" and i.get("source_id")]
    return [u for u in dict.fromkeys((sources.get(x) or {}).get("url") for x in ids) if u]

def slot_contexts(session: Session, job: ResearchJobRecord,
                  slots: list[dict[str, Any]]) -> tuple[dict[int, list[str]], str]:
    """slot index -> passage URLs of the bundle it was shown, and how they were linked.

    ``digest``: the extract event's context_digest = the stored bundle's fingerprint prefix.
    Runs from before the digest existed fall back to ``time_window``: the run city's EXTRACT
    bundles created while the run was active, aligned to the field's slots in order, used only
    when the field has exactly as many bundles as slots (else that field stays unlinked)."""
    out: dict[int, list[str]] = {}
    if any(s.get("context_digest") for s in slots):
        for n, s in enumerate(slots):
            d = s.get("context_digest")
            if not (s["called"] and d):
                continue
            row = session.scalars(select(ContextBundleRecord)
                                  .where(ContextBundleRecord.fingerprint.like(f"{d}%"))
                                  .limit(1)).first()
            if row is not None:
                out[n] = _urls(row.payload or {})
        return out, "digest"
    end = job.finished_at or job.updated_at
    rows = list(session.scalars(
        select(ContextBundleRecord)
        .where(ContextBundleRecord.city_name == job.city_name,
               ContextBundleRecord.purpose == "EXTRACT",
               ContextBundleRecord.created_at >= job.created_at,
               ContextBundleRecord.created_at <= end)
        .order_by(ContextBundleRecord.created_at, ContextBundleRecord.id)))
    by_field: dict[str, list[ContextBundleRecord]] = defaultdict(list)
    for r in rows:
        if len(r.target_fields or []) == 1:
            by_field[r.target_fields[0]].append(r)
    slot_ids: dict[str, list[int]] = defaultdict(list)
    for n, s in enumerate(slots):
        slot_ids[s["field"]].append(n)
    for field, ids in slot_ids.items():
        bundles = by_field.get(field, [])
        if len(bundles) != len(ids):
            continue
        for n, b in zip(ids, bundles, strict=True):
            if slots[n]["called"]:
                out[n] = _urls(b.payload or {})
    return out, "time_window"

def _attribute_calls(documents: list[dict[str, Any]], slots: list[dict[str, Any]],
                     contexts: Mapping[int, list[str]]) -> dict[str, Any]:
    by_url: dict[str, dict[str, Any]] = {}
    for d in documents:
        for u in {d["requested_url"], d["final_url"], d["canonical_url"]} - {None}:
            by_url[u] = d
    for d in documents:
        d.update(calls_fed=0, sole_source_calls=0, tokens_in_calls_fed=0,
                 fields_fed=set(), claims_in_calls_fed=0)
    linked = unlinked = 0
    for n, s in enumerate(slots):
        if not s["called"]:
            continue
        urls = contexts.get(n)
        if not urls:
            unlinked += 1
            continue
        linked += 1
        docs = {id(by_url[u]): by_url[u] for u in urls if u in by_url}
        tokens = (s.get("input_tokens") or 0) + (s.get("output_tokens") or 0)
        for d in docs.values():
            d["calls_fed"] += 1
            d["tokens_in_calls_fed"] += tokens
            d["fields_fed"].add(s["field"])
            d["claims_in_calls_fed"] += s.get("claims") or 0
            if len(docs) == 1:
                d["sole_source_calls"] += 1
    for d in documents:
        d["fields_fed"] = sorted(d["fields_fed"])
    return {"calls_linked_to_documents": linked, "calls_without_stored_bundle": unlinked}

def audit_run(engine: Engine, run_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        job = session.get(ResearchJobRecord, run_id)
        if job is None:
            raise LookupError(f"unknown research run {run_id}")
        events, events_source = token_audit._events(session, job)
        hits = list(session.scalars(select(DiscoveryResultRecord)
                                    .where(DiscoveryResultRecord.run_id == run_id)
                                    .order_by(DiscoveryResultRecord.id)))
        docs = list(session.scalars(select(SourceDocumentRecord)
                                    .where(SourceDocumentRecord.run_id == run_id)
                                    .order_by(SourceDocumentRecord.id)))
        info = {"run_id": run_id, "city": job.city_name, "country": job.country,
                "mode": job.mode, "status": job.status, "parent_run_id": job.parent_run_id}
        documents = [{
            "document_id": d.id, "round": d.round, "requested_url": d.requested_url,
            "final_url": d.final_url, "canonical_url": d.canonical_url,
            "host": host_of(d.final_url or d.requested_url), "title": d.title,
            "source_type": d.source_type, "status": d.status, "error_kind": d.error_kind,
            "discovery_query": d.discovery_query, "discovery_provider": d.discovery_provider,
            "found_by_site_query": site_target(d.discovery_query),
            "chunk_count": d.chunk_count, "media_type": d.media_type,
        } for d in docs]
        for d in documents:
            d.update(domain_signals(d["host"]))
        tokens = token_audit.audit_run(engine, run_id)
        slots = tokens["slots"]
        contexts, linkage_method = slot_contexts(session, job, slots)
    calls = search_calls(events)
    rounds = max([c["round"] for c in calls] + [d["round"] for d in documents]
                 + [h.round for h in hits] + [1])
    linkage = {**_attribute_calls(documents, slots, contexts), "linkage": linkage_method}
    hosts = utility_hosts_by_round(documents, rounds)
    hit_rows = [{"round": h.round, "provider": h.provider, "query": h.query, "url": h.url,
                 "host": host_of(h.url), "title": h.title, "source_type": h.source_type,
                 "retained": h.retained, "retrieved_at": h.retrieved_at} for h in hits]
    by_round = []
    for r in range(1, rounds + 1):
        rc = [c for c in calls if c["round"] == r]
        rd = [d for d in documents if d["round"] == r]
        rh = [h for h in hit_rows if h["round"] == r]
        by_round.append({
            "round": r, "searches": len(rc),
            "site_queries": sorted({c["site_target"] for c in rc if c["site_target"]}),
            "hits": len(rh), "hits_retained": sum(1 for h in rh if h["retained"]),
            "documents_attempted": len(rd),
            "documents_ok": sum(1 for d in rd if d["status"] == "OK"),
            "hosts": dict(Counter(d["host"] for d in rd)),
            "cctlds": dict(Counter(d["cctld"] or d["tld"] for d in rd)),
            "utility_hosts_at_start": hosts.get(r, []),
        })
    site_on = Counter(c["site_target"] for c in calls if c["site_target"])
    return {**info, "events_source": events_source, "rounds": rounds,
            "searches": len(calls), "site_queries": dict(site_on),
            "documents_attempted": len(documents),
            "documents_ok": sum(1 for d in documents if d["status"] == "OK"),
            "extraction": tokens["total"], **linkage,
            "by_round": by_round, "calls": calls, "documents": documents,
            "discovery_hits": hit_rows}

def audit_runs(engine: Engine, run_ids: Iterable[str]) -> dict[str, Any]:
    return {"runs": [audit_run(engine, r) for r in run_ids]}

# --- rendering ----------------------------------------------------------------------------

def _n(v: Any) -> str:
    if v is None:
        return "Unknown"
    return f"{v:,}" if isinstance(v, int) else str(v)

def render_markdown(report: Mapping[str, Any]) -> str:
    intro = ("Read-only audit of discovery. *Utility hosts at start* re-derives the rule the "
             "run used (any OK document classed UTILITY in earlier rounds). *Calls fed* counts "
             "extraction calls whose context included the document; documents share calls, "
             "so these numbers are not additive across documents.")
    lines = ["# Discovery audit", "", intro, ""]
    for r in report["runs"]:
        e = r["extraction"]
        site = ", ".join(f"{h} ×{n}" for h, n in r["site_queries"].items()) or "none"
        heading = (f"## {r['city']}, {r['country']} — {r['run_id'][:8]} "
                   f"({r['mode']}, {r['status']})")
        summary = (f"- Rounds {r['rounds']}; searches {r['searches']}; documents attempted "
                   f"{r['documents_attempted']} (OK {r['documents_ok']}); events from "
                   f"{r['events_source']}")
        calls = (f"- Extraction calls {e['llm_calls']} (linked to documents "
                 f"{r['calls_linked_to_documents']}, no stored bundle "
                 f"{r['calls_without_stored_bundle']}, by {r['linkage']}); input {_n(e['input_tokens'])}, "
                 f"output {_n(e['output_tokens'])}; VALID claims {e['valid_claims']}")
        header = ("| round | searches | site: targets | hits (retained) | docs (OK) | "
                  "utility hosts at start | TLDs |")
        lines += [heading, "", summary, f"- Site-restricted searches: {site}", calls, "",
                  header, "|---|---|---|---|---|---|---|"]
        for b in r["by_round"]:
            tlds = ", ".join(f"{k} {v}" for k, v in sorted(b["cctlds"].items())) or "—"
            lines.append(f"| {b['round']} | {b['searches']} | "
                         f"{', '.join(b['site_queries']) or '—'} | {b['hits']} "
                         f"({b['hits_retained']}) | {b['documents_attempted']} "
                         f"({b['documents_ok']}) | {', '.join(b['utility_hosts_at_start']) or '—'}"
                         f" | {tlds} |")
        doc_header = ("| round | host | type | status | found by | calls fed (sole) | "
                      "tokens in calls fed | fields |")
        lines += ["", "### Documents", "", doc_header, "|---|---|---|---|---|---|---|---|"]
        for d in sorted(r["documents"], key=lambda d: (d["round"], d["host"])):
            q = (d["discovery_query"] or "")[:60].replace("|", "/")
            lines.append(f"| {d['round']} | {d['host']} | {d['source_type'] or '—'} | "
                         f"{d['status']} | {q or '—'} | {d['calls_fed']} "
                         f"({d['sole_source_calls']}) | {_n(d['tokens_in_calls_fed'])} | "
                         f"{', '.join(d['fields_fed']) or '—'} |")
        lines.append("")
    return "\n".join(lines)

def documents_by_host(report: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """host -> {documents, ok, calls_fed} across runs (for comparisons)."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"documents": 0, "ok": 0,
                                                           "calls_fed": 0})
    for r in report["runs"]:
        for d in r["documents"]:
            h = out[d["host"]]
            h["documents"] += 1
            h["ok"] += d["status"] == "OK"
            h["calls_fed"] += d["calls_fed"]
    return dict(out)
