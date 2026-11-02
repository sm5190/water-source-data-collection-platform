"""Discovery-only evaluation on stored runs (Milestone 3, step A): no network, no model call,
nothing written.

For each run and discovery round, the current deterministic rules are applied to the evidence
the run had *at the start of that round* (documents from earlier rounds, utility_name and
water_source claims extracted in earlier rounds):
- the utility registry: which utilities would be VERIFIED, so which ``site:`` targets the new
  rules would search, against the ones the run actually searched;
- host suppression: which of the round's fetched documents would not have been fetched;
- geography: which of them are about another place (approved/draft identity);
then, on everything the run acquired: the final registry against the utility expectations
(correct / wrong / missed), the source classification (stored classify-1 type and current
publisher kind) against the document labels, and acquisition precision from the labels —
validated (confirmed labels) and provisional (unconfirmed proposals) kept apart, with
absolute counts and denominators.

The labels and expectations are evaluation data (tests/evaluation/discovery/): read here
only, never by research.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import yaml
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import (
    EvidenceChunkRecord, FieldClaimRecord, SourceDocumentRecord,
)
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.discovery.normalize import classify
from water_source_research.evaluation import discovery_audit
from water_source_research.evaluation.discovery_labels import labels_by_document
from water_source_research.schemas.research import EvidenceChunk, EvidenceRef, FieldClaim
from water_source_research.services.field_state import initialize_city_state
from water_source_research.services.geo_match import document_scopes, matcher_for
from water_source_research.services.utility_registry import build_registry, utility_key

def _host(url: str | None) -> str:
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")

def load_expectations(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {(c["city"], c["country"]): c | {"status": data.get("status", "UNCONFIRMED")}
            for c in data.get("cities", [])}

def _load(engine: Engine, run_id: str) -> dict[str, Any]:
    with Session(engine) as s:
        job = s.get(ResearchJobRecord, run_id)
        docs = s.scalars(select(SourceDocumentRecord).where(
            SourceDocumentRecord.run_id == run_id).order_by(SourceDocumentRecord.id)).all()
        chunks = s.scalars(select(EvidenceChunkRecord).where(
            EvidenceChunkRecord.run_id == run_id).order_by(EvidenceChunkRecord.id)).all()
        claims = s.scalars(select(FieldClaimRecord).where(
            FieldClaimRecord.run_id == run_id,
            FieldClaimRecord.field_key.in_(["utility_name", "water_source",
                                            "reservoir_names"]))).all()
        by_doc = {d.id: d for d in docs}
        return {
            "city": job.city_name, "country": job.country,
            "documents": [{"document_id": d.id, "round": d.round, "status": d.status,
                           "url": d.final_url or d.requested_url, "title": d.title,
                           "source_type": d.source_type,
                           "discovery_provider": d.discovery_provider} for d in docs],
            "chunks": [{"chunk_id": c.chunk_id, "url": c.url, "text": c.text,
                        "page_number": c.page_number, "round": by_doc[c.document_id].round,
                        "title": by_doc[c.document_id].title,
                        "source_type": by_doc[c.document_id].source_type} for c in chunks],
            "claims": [{"field_key": c.field_key, "value": c.value,
                        "round": int(((c.qualifiers or {}).get("extraction_round")) or 1),
                        "review_status": c.review_status} for c in claims
                       if c.review_status != "CORRECTED" and c.value is not None],
        }

def _state(data: Mapping[str, Any], before_round: int | None):
    s = initialize_city_state(data["city"], data["country"])
    for c in data["claims"]:
        if before_round is not None and c["round"] >= before_round:
            continue
        s.fields[c["field_key"]].candidate_claims.append(FieldClaim(
            field_key=c["field_key"], value=c["value"], confidence=0.5,
            evidence=[EvidenceRef(url="https://claim.example", passage=str(c["value"]))]))
    return s

def _chunks(data: Mapping[str, Any], before_round: int | None) -> list[EvidenceChunk]:
    return [EvidenceChunk(chunk_id=c["chunk_id"], url=c["url"], text=c["text"],
                          title=c["title"], page_number=c["page_number"],
                          source_type=c["source_type"])
            for c in data["chunks"] if before_round is None or c["round"] < before_round]

def counterfactual(engine: Engine, run_id: str) -> dict[str, Any]:
    """Round by round: the current rules' utility hosts, suppressed hosts and other-place
    documents against what the run did."""
    from water_source_research.agents.nodes import suppressed_hosts, utility_hosts
    data = _load(engine, run_id)
    audit = discovery_audit.audit_run(engine, run_id)
    docs = data["documents"]
    rows = []
    matcher = matcher_for(data["city"], data["country"])
    all_chunks = _chunks(data, None)
    scopes = document_scopes(matcher, all_chunks) if matcher is not None else {}
    for b in audit["by_round"]:
        r = b["round"]
        state = _state(data, r)
        chunks = _chunks(data, r)
        documents = {d["url"]: d for d in docs if d["round"] < r}
        registry = build_registry(state, chunks, documents).model_dump(mode="json")
        earlier = [{**d, "final_url": d["url"], "publisher_kind": classify(
            d["url"], d["title"], city=data["city"]).publisher_kind}
            for d in docs if d["round"] < r]
        sim = {"research_state": state.model_dump(mode="json"), "acquired_documents": earlier,
               "evidence_chunks": [c.model_dump(mode="json") for c in chunks],
               "utility_registry": registry, "suppressed_hosts": []}
        suppressed = suppressed_hosts(sim) if chunks else {}
        sim["suppressed_hosts"] = sorted(suppressed)
        new_hosts = utility_hosts(sim)
        fetched = [d for d in docs if d["round"] == r]
        rows.append({
            "round": r, "site_targets_run": b["site_queries"],
            "site_targets_new_rules": new_hosts[:1],
            "site_targets_dropped": [h for h in b["site_queries"] if h not in new_hosts],
            "utility_hosts_old_rule": b["utility_hosts_at_start"],
            "utility_hosts_new_rule": new_hosts,
            "verified": [c["name"] for c in registry["candidates"]
                         if c["status"] == "VERIFIED"],
            "suppressed": suppressed,
            "fetched": len(fetched),
            "fetched_from_suppressed": [d["url"] for d in fetched
                                        if _host(d["url"]) in suppressed],
            "fetched_about_elsewhere": [d["url"] for d in fetched if d["status"] == "OK"
                                        and scopes.get(d["url"]) is not None
                                        and scopes[d["url"]].elsewhere],
        })
    final = build_registry(_state(data, None), all_chunks,
                           {d["url"]: d for d in docs}).model_dump(mode="json")
    return {"run_id": run_id, "city": data["city"], "country": data["country"],
            "identity": "approved/draft" if matcher is not None else "catalog only",
            "rounds": rows, "final_registry": final, "audit": audit, "data": data}

def utility_accuracy(final_registry: Mapping[str, Any], expected: Mapping[str, Any] | None
                     ) -> dict[str, Any]:
    """VERIFIED candidates against the expected serving utilities (names or aliases)."""
    if not expected:
        return {"expected": None}
    want = {utility_key(n): s for s in expected.get("serving", [])
            for n in [s["name"], *s.get("aliases", [])]}
    not_serving = {utility_key(s["name"]) for s in expected.get("not_serving", [])}
    got = [c for c in final_registry["candidates"] if c["status"] == "VERIFIED"]
    def keys(c):
        return {utility_key(c["name"]), *(utility_key(a) for a in c.get("aliases", []))}
    correct = [c["name"] for c in got if keys(c) & set(want)]
    wrong = [c["name"] for c in got if not keys(c) & set(want)]
    wrong_known = [c["name"] for c in got if keys(c) & not_serving]
    found = {k for c in got for k in keys(c)}
    missed = [s["name"] for s in expected.get("serving", [])
              if not {utility_key(n) for n in [s["name"], *s.get("aliases", [])]} & found]
    expected_hosts = {h for s in expected.get("serving", []) for h in s.get("hosts", [])}
    verified_hosts = {h for c in got for h in c.get("bound_hosts", [])}
    statuses = {c["name"]: c["status"] for c in final_registry["candidates"]}
    return {"expected_status": expected.get("status"), "verified": len(got),
            "correct": correct, "wrong": wrong, "wrong_known_non_serving": wrong_known,
            "missed": missed, "expected_serving": len(expected.get("serving", [])),
            "verified_hosts_correct": sorted(verified_hosts & expected_hosts),
            "verified_hosts_wrong": sorted(verified_hosts - expected_hosts),
            "statuses": statuses}

def label_metrics(run: Mapping[str, Any], labels: Sequence[Mapping], key: Mapping
                  ) -> dict[str, Any]:
    """Acquisition precision and classification against the labels, validated (confirmed)
    and provisional (unconfirmed proposals) apart."""
    by_doc = labels_by_document(labels, key)
    out: dict[str, Any] = {}
    data = run["data"]
    for kind in ("validated", "provisional"):
        rel = Counter()
        cls_v1 = Counter()
        cls_v2 = Counter()
        n = 0
        for d in data["documents"]:
            if d["status"] != "OK":
                continue
            lab = by_doc.get((data["city"], d["url"]))
            if lab is None or (kind == "validated") != bool(lab["confirmed"]):
                continue
            n += 1
            rel[lab["relevant"] or "UNLABELLED"] += 1
            if lab["publisher_kind"] and lab["publisher_kind"] != "UNKNOWN":
                v2 = classify(d["url"], d["title"], d.get("discovery_provider") or "",
                              city=data["city"]).publisher_kind
                cls_v2["match" if v2 == lab["publisher_kind"] else "differ"] += 1
                v1_kind = {"UTILITY": "UTILITY_OPERATOR", "GOVERNMENT": "GOVERNMENT",
                           "REGULATORY": "REGULATOR", "ACADEMIC": "ACADEMIC",
                           "TECHNICAL": "TECHNICAL"}.get((d["source_type"] or "").upper(),
                                                         "OTHER")
                cls_v1["match" if v1_kind == lab["publisher_kind"] else "differ"] += 1
        judged = rel["YES"] + rel["NO"]
        out[kind] = {"labelled": n, "relevant": rel["YES"], "irrelevant": rel["NO"],
                     "uncertain": rel["UNCERTAIN"],
                     "acquisition_precision": f"{rel['YES']}/{judged}" if judged else None,
                     "publisher_kind_v1": f"{cls_v1['match']}/{sum(cls_v1.values())}",
                     "publisher_kind_v2": f"{cls_v2['match']}/{sum(cls_v2.values())}"}
    return out

def summary_rows(run: Mapping[str, Any]) -> dict[str, Any]:
    rounds = run["rounds"]
    audit = run["audit"]
    return {
        "site_queries_run": sum(sum(r["site_targets_run"].values()) if isinstance(
            r["site_targets_run"], dict) else len(r["site_targets_run"]) for r in rounds),
        "site_queries_run_on_unverified": sum(
            n for h, n in audit["site_queries"].items()
            if h not in {x for r in rounds for x in r["utility_hosts_new_rule"]}),
        "documents_fetched": audit["documents_attempted"],
        "would_skip_suppressed": sum(len(r["fetched_from_suppressed"]) for r in rounds),
        "about_elsewhere": sum(len(r["fetched_about_elsewhere"]) for r in rounds),
        "searches": audit["searches"], "extraction_calls": audit["extraction"]["llm_calls"],
        "input_tokens": audit["extraction"]["input_tokens"],
        "output_tokens": audit["extraction"]["output_tokens"],
    }

def render(results: Sequence[Mapping[str, Any]], title: str) -> str:
    lines = [f"# {title}", "",
             ("Stored runs, current deterministic rules, no network or model calls. *Validated* "
             "counts use person-confirmed labels only; *provisional* ones use the assistant's "
             "unconfirmed proposals. Utility expectations are UNCONFIRMED drafts."), ""]
    for r in results:
        s, u, lm = r["summary"], r["utilities"], r["labels"]
        lines += [f"## {r['city']} — {r['run_id'][:8]} (identity: {r['identity']})", "",
                  (f"- Searches {s['searches']}; site: queries run {s['site_queries_run']}, of "
                  f"which on hosts the new rules never verify: "
                  f"{s['site_queries_run_on_unverified']}"),
                  (f"- Documents fetched {s['documents_fetched']}; would not be fetched "
                  f"(suppressed host) {s['would_skip_suppressed']}; about another place "
                  f"{s['about_elsewhere']}"),
                  (f"- Extraction calls {s['extraction_calls']} (input {s['input_tokens']:,}, "
                  f"output {s['output_tokens']:,} tokens)")]
        if u.get("expected") is not None or "verified" in u:
            lines += [(f"- Utilities verified by the new rules: {u.get('verified', 0)} — "
                      f"correct {u.get('correct')}, wrong {u.get('wrong')}, missed "
                      f"{u.get('missed')} (of {u.get('expected_serving')} expected, "
                      f"{u.get('expected_status')}); verified hosts wrong "
                      f"{u.get('verified_hosts_wrong')}")]
        for kind in ("validated", "provisional"):
            m = lm[kind]
            lines.append(f"- Labels ({kind}): {m['labelled']} documents — relevant "
                         f"{m['relevant']}, irrelevant {m['irrelevant']}, uncertain "
                         f"{m['uncertain']}; acquisition precision "
                         f"{m['acquisition_precision'] or '—'}; publisher kind classify-1 "
                         f"{m['publisher_kind_v1']}, classify-2 {m['publisher_kind_v2']}")
        lines += ["", ("| round | site: run | utility hosts old rule | new rule (verified) | "
                  "suppressed | fetched from suppressed | about elsewhere |"),
                  "|---|---|---|---|---|---|---|"]
        for row in r["rounds"]:
            lines.append(
                f"| {row['round']} | {', '.join(row['site_targets_run']) or '—'} | "
                f"{', '.join(row['utility_hosts_old_rule']) or '—'} | "
                f"{', '.join(row['utility_hosts_new_rule']) or '—'} | "
                f"{', '.join(row['suppressed']) or '—'} | {len(row['fetched_from_suppressed'])}"
                f" | {len(row['fetched_about_elsewhere'])} |")
        statuses = r["utilities"].get("statuses") or {
            c["name"]: c["status"] for c in r["final_registry"]["candidates"]}
        if statuses:
            lines += ["", "Final registry: " + "; ".join(f"{n} {st}" for n, st in
                                                          list(statuses.items())[:15])]
        lines.append("")
    return "\n".join(lines)

def evaluate(engine: Engine, run_ids: Sequence[str], labels: Sequence[Mapping],
             key: Mapping, expectations: Mapping) -> list[dict[str, Any]]:
    out = []
    for run_id in run_ids:
        run = counterfactual(engine, run_id)
        exp = expectations.get((run["city"], run["country"]))
        out.append({"run_id": run_id, "city": run["city"], "identity": run["identity"],
                    "rounds": run["rounds"], "final_registry": run["final_registry"],
                    "summary": summary_rows(run),
                    "utilities": utility_accuracy(run["final_registry"], exp),
                    "labels": label_metrics(run, labels, key)})
    return out

def totals(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    agg: dict[str, int] = defaultdict(int)
    for r in results:
        for k, v in r["summary"].items():
            if isinstance(v, int):
                agg[k] += v
    return dict(agg)
