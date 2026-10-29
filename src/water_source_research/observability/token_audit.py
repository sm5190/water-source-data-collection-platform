"""Where a run's extraction tokens went (read only; nothing is written).

One *slot* = one field in one extraction round (the extract node's NODE event). For each slot:
initial extraction or re-extraction, the bundle status (READY = the model was asked; anything
else = no model call, with the reason), model requests, app-cache hits, input / output /
provider-cached tokens, the claims it produced, and how many of those were VALID or selected.

Sources, in order of authority:
- ``provider_usage`` (the ledger) for tokens and provider-cached tokens. Ledger rows carry the
  field but not the round, so they are matched to the field's slots in order; when the counts
  differ the slot keeps the event's own token numbers and ``ledger_matched`` is False.
- the run's trusted events: ``run_events`` once the run finished, else the events stored in
  ``research_jobs.result`` (a run waiting for review has not recorded its events yet).
- ``field_claims`` for claim outcomes (reviewer-entered CORRECTED claims excluded), attributed
  to a slot by ``qualifiers.extraction_round``.

Spent and avoided tokens are never added together: a cache hit spends nothing (requests 0); the
tokens it avoided are reported separately, from the persistent cache entry it reused
(``provider_usage.cache_source`` -> ``llm_response_cache``, which keeps the original call's
usage). A hit whose original usage isn't known (in-process cache) is counted in
``avoided_unknown_hits``, never as 0 tokens.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import FieldClaimRecord
from water_source_research.db.observability_models import (
    LLMResponseCacheRecord, ProviderUsageRecord,
)
from water_source_research.db.research_models import ResearchJobRecord, RunEventRecord

USABLE = ("VALID", "VALID_WITH_WARNINGS")
NO_CALL = "no model call"

def _events(session: Session, job: ResearchJobRecord) -> tuple[list[dict[str, Any]], str]:
    rows = session.scalars(select(RunEventRecord).where(RunEventRecord.run_id == job.run_id)
                           .order_by(RunEventRecord.seq)).all()
    if rows:
        return [{"seq": r.seq, "kind": r.kind, "node": r.node, "field_key": r.field_key,
                 "strategy": r.strategy, "attrs": r.attrs or {}} for r in rows], "run_events"
    return list((job.result or {}).get("events") or []), "job result"

def slots_from_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in events:
        if e.get("kind") != "NODE" or e.get("node") != "extract" or not e.get("field_key"):
            continue
        a = e.get("attrs") or {}
        strategy = e.get("strategy") or ""
        requests = int(a.get("llm_requests") or 0)
        hits = int(a.get("cache_hits") or 0)
        out.append({
            "seq": e.get("seq"), "field": e["field_key"],
            "round": int(a.get("discovery_round") or 1),
            "stage": "reextraction" if strategy.startswith("reextract:") else "initial",
            "bundle_status": a.get("bundle_status") or strategy.split(":")[-1],
            "skip_reason": a.get("skip_reason"),
            "requests": requests, "app_cache_hits": hits, "called": requests > 0 or hits > 0,
            "input_tokens": a.get("prompt_tokens") if requests else 0,
            "output_tokens": a.get("completion_tokens") if requests else 0,
            "provider_cached_tokens": a.get("cached_tokens"),
            "tokens_avoided": None,
            "claims": int(a.get("claims") or 0),
            "context_digest": a.get("context_digest"),
            "evidence_items": a.get("evidence_items"),
            "ledger_matched": False,
        })
    return out

def _match_ledger(slots: list[dict[str, Any]], rows: list[ProviderUsageRecord],
                  avoided: Mapping[str, LLMResponseCacheRecord]) -> None:
    by_field: dict[str, list[ProviderUsageRecord]] = defaultdict(list)
    for r in rows:
        if r.field_key:
            by_field[r.field_key].append(r)
    called: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in slots:
        if s["called"]:
            called[s["field"]].append(s)
    for field, field_slots in called.items():
        ledger = by_field.get(field, [])
        if len(ledger) != len(field_slots):
            continue
        for s, r in zip(sorted(field_slots, key=lambda x: x["seq"] or 0), ledger, strict=True):
            s["ledger_matched"] = True
            if r.cache_hit:
                s["input_tokens"] = s["output_tokens"] = 0
                s["cache_layer"] = r.cache_layer
                src = avoided.get(r.cache_source or "")
                if src is not None and src.input_tokens is not None:
                    s["tokens_avoided"] = src.input_tokens + (src.output_tokens or 0)
                continue
            s["input_tokens"] = r.input_tokens
            s["output_tokens"] = r.output_tokens
            s["provider_cached_tokens"] = r.cached_tokens

def _attach_claims(slots: list[dict[str, Any]], claims: list[FieldClaimRecord]) -> None:
    index = {(s["field"], s["round"]): s for s in slots if s["called"]}
    for s in slots:
        s.update(valid_claims=0, selected_claims=0, stored_claims=0)
    for c in claims:
        rnd = int(((c.qualifiers or {}).get("extraction_round")) or 1)
        s = index.get((c.field_key, rnd))
        if s is None:  # e.g. a reservoir copied from water_source in a round with no call
            continue
        s["stored_claims"] += 1
        if c.validation_outcome in USABLE:
            s["valid_claims"] += 1
        if c.is_selected:
            s["selected_claims"] += 1

def _sum(xs: Iterable[Any]) -> int | None:
    vals = list(xs)
    return None if any(v is None for v in vals) else sum(vals)

def summarize(slots: list[dict[str, Any]]) -> dict[str, Any]:
    called = [s for s in slots if s["called"]]
    model = [s for s in called if s["requests"]]
    skipped = Counter(s["bundle_status"] for s in slots if not s["called"])
    digests = Counter(s["context_digest"] for s in called if s["context_digest"])
    avoided = [s["tokens_avoided"] for s in called if s["app_cache_hits"]]
    return {
        "slots": len(slots), "llm_calls": len(called), "model_calls": len(model),
        "requests": sum(s["requests"] for s in called),
        "app_cache_hits": sum(s["app_cache_hits"] for s in called),
        "input_tokens": _sum(s["input_tokens"] for s in model),
        "output_tokens": _sum(s["output_tokens"] for s in model),
        "provider_cached_tokens": _sum(s["provider_cached_tokens"] for s in model),
        "tokens_avoided": sum(a for a in avoided if a is not None),
        "avoided_unknown_hits": sum(1 for a in avoided if a is None),
        "zero_claim_calls": sum(1 for s in called if s["claims"] == 0),
        "productive_calls": sum(1 for s in called if s.get("valid_claims")),
        "claims": sum(s["claims"] for s in called),
        "valid_claims": sum(s.get("valid_claims", 0) for s in called),
        "selected_claims": sum(s.get("selected_claims", 0) for s in called),
        "skipped": dict(sorted(skipped.items())),
        "duplicate_contexts": (sum(n - 1 for n in digests.values() if n > 1)
                               if digests else None),
    }

def _group(slots: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for s in slots:
        groups[s[key]].append(s)
    return [{key: k, **summarize(v)} for k, v in sorted(groups.items())]

def audit_run(engine: Engine, run_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        job = session.get(ResearchJobRecord, run_id)
        if job is None:
            raise LookupError(f"unknown research run {run_id}")
        events, source = _events(session, job)
        ledger = list(session.scalars(
            select(ProviderUsageRecord).where(ProviderUsageRecord.run_id == run_id,
                                              ProviderUsageRecord.task == "extract")
            .order_by(ProviderUsageRecord.id)))
        reused = {r.cache_source for r in ledger} - {None}
        originals = {r.cache_key: r for r in session.scalars(select(LLMResponseCacheRecord)
                     .where(LLMResponseCacheRecord.cache_key.in_(reused)))} if reused else {}
        claims = list(session.scalars(
            select(FieldClaimRecord).where(FieldClaimRecord.run_id == run_id)))
        claims = [c for c in claims if c.review_status != "CORRECTED"]
        info = {"run_id": run_id, "city": job.city_name, "country": job.country,
                "mode": job.mode, "status": job.status, "parent_run_id": job.parent_run_id}
    slots = slots_from_events(events)
    _match_ledger(slots, ledger, originals)
    _attach_claims(slots, claims)
    return {**info, "events_source": source, "ledger_rows": len(ledger),
            "ledger_matched": all(s["ledger_matched"] for s in slots if s["called"]),
            "total": summarize(slots), "by_round": _group(slots, "round"),
            "by_stage": _group(slots, "stage"), "by_field": _group(slots, "field"),
            "slots": slots}

def audit_runs(engine: Engine, run_ids: Iterable[str]) -> dict[str, Any]:
    return {"runs": [audit_run(engine, r) for r in run_ids]}

# --- rendering ----------------------------------------------------------------------------

def _n(v: Any) -> str:
    if v is None:
        return "Unknown"
    return f"{v:,}" if isinstance(v, int) else str(v)

_COLS = ("llm_calls", "zero_claim_calls", "productive_calls", "input_tokens", "output_tokens",
         "provider_cached_tokens", "app_cache_hits", "tokens_avoided", "valid_claims")
_HEAD = ("calls", "zero-claim", "productive", "input tok", "output tok", "provider-cached",
         "app-cache hits", "tok avoided", "valid claims")

def _table(rows: list[dict[str, Any]], key: str) -> list[str]:
    out = [f"| {key} | " + " | ".join(_HEAD) + " | skipped (no call) |",
           "|" + "---|" * (len(_HEAD) + 2)]
    for r in rows:
        skipped = ", ".join(f"{k} {v}" for k, v in r["skipped"].items()) or "—"
        out.append(f"| {r[key]} | " + " | ".join(_n(r[c]) for c in _COLS) + f" | {skipped} |")
    return out

def render_markdown(report: Mapping[str, Any]) -> str:
    intro = ("One slot = one field in one extraction round. *Productive* = produced at least "
             "one VALID claim. Provider-cached tokens are part of input tokens (billed by the "
             "provider's prompt cache); app-cache hits made no model request. Unknown = not "
             "reported, never 0.")
    lines = ["# Extraction token audit", "", intro, ""]
    for r in report["runs"]:
        t = r["total"]
        matched = ("" if r["ledger_matched"]
                   else " (not all matched to slots: event token counts used there)")
        calls = (f"- Calls {t['llm_calls']} (model {t['model_calls']}, app-cache hits "
                 f"{t['app_cache_hits']}), zero-claim {t['zero_claim_calls']}, productive "
                 f"{t['productive_calls']}")
        tokens = (f"- Input {_n(t['input_tokens'])} (provider-cached "
                  f"{_n(t['provider_cached_tokens'])}), output {_n(t['output_tokens'])}; "
                  f"avoided by app cache {_n(t['tokens_avoided'])}"
                  + (f" (+{t['avoided_unknown_hits']} in-process hit(s) of unknown size)"
                     if t["avoided_unknown_hits"] else ""))
        claims = (f"- Claims {t['claims']} (VALID {t['valid_claims']}, selected "
                  f"{t['selected_claims']}); repeated identical contexts "
                  f"{_n(t['duplicate_contexts'])}")
        lines += [f"## {r['city']} — {r['run_id'][:8]} ({r['mode']}, {r['status']})", "",
                  f"Events from {r['events_source']}; ledger rows {r['ledger_rows']}{matched}.",
                  "", calls, tokens, claims, "",
                  "### By round", "", *_table(r["by_round"], "round"), "",
                  "### Initial vs re-extraction", "", *_table(r["by_stage"], "stage"), "",
                  "### By field", "", *_table(r["by_field"], "field"), ""]
        waste = [s for s in r["slots"] if s["called"] and not s["claims"]]
        if waste:
            lines += ["### Calls that returned no claims", "",
                      "| round | field | stage | input tok | output tok |", "|---|---|---|---|---|"]
            lines += [f"| {s['round']} | {s['field']} | {s['stage']} | {_n(s['input_tokens'])} "
                      f"| {_n(s['output_tokens'])} |"
                      for s in sorted(waste, key=lambda s: (s["round"], s["field"]))]
            lines.append("")
    return "\n".join(lines)
