"""Deterministic counterfactual replay: what would the current scheduling and gating have done
on a run's saved documents, with no model calls?

The run's documents come back in their original acquisition rounds (services/replay.py, in
memory: nothing is written). Extraction is answered by ``RecordedGateway``: for each (field,
round) it returns the claims the parent run got in that slot, re-grounded against the passages
the *current* code shows (a claim whose quotes are no longer shown is not returned and is
listed as ``unreproduced``). So the replay follows the parent's path as closely as recorded
answers allow, and the comparison tells, per slot, whether the current code would still call
the model there and what was lost where it wouldn't.

Token figures for the counterfactual are estimates from the parent's own slots: a slot the
current code still calls costs what it cost the parent; a slot the parent never called costs
the parent's average for that field (else for the run). They are labelled as estimates.
"""

from collections import defaultdict
from collections.abc import Mapping
from typing import Any
from sqlalchemy import Engine
from water_source_research.llm.extraction import normalize_quote
from water_source_research.observability.llm_telemetry import llm_context
from water_source_research.observability.token_audit import audit_run, slots_from_events
from water_source_research.schemas.common import ValueOrigin, ValueStatus
from water_source_research.schemas.research import ClaimQualifiers, FieldClaim
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.replay import replay_run

Slot = tuple[str, int]  # (field, round)

class RecordedGateway:
    """Answers each extraction with the parent's recorded claims for that (field, round)."""

    def __init__(self, claims: list[Mapping[str, Any]]) -> None:
        self.slots: dict[Slot, list[Mapping[str, Any]]] = defaultdict(list)
        for c in claims:
            if c.get("review_status") == "CORRECTED":
                continue
            rnd = int(((c.get("qualifiers") or {}).get("extraction_round")) or 1)
            self.slots[(c["field_key"], rnd)].append(c)
        self.calls: list[Slot] = []
        self.unreproduced: list[dict[str, Any]] = []

    async def extract(self, bundle: Any, run_id: str, field_key: str) -> list[FieldClaim]:
        rnd = int((llm_context.get() or {}).get("discovery_round") or 1)
        self.calls.append((field_key, rnd))
        shown = {sid: normalize_quote("\n".join(src.passages))
                 for sid, src in bundle.sources.items()}
        out = []
        for c in self.slots.get((field_key, rnd), []):
            refs = []
            for cite in c.get("citations") or []:
                q = normalize_quote(cite.get("quote") or "")
                sid = next((s for s, text in shown.items() if q and q in text), None)
                if sid is not None:
                    refs.append(bundle.evidence_ref(sid, passage=cite["quote"]))
            value = c.get("original_value") if c.get("original_value") is not None \
                else c.get("value")
            if not refs:
                self.unreproduced.append({"field": field_key, "round": rnd, "value": value})
                continue
            q = c.get("qualifiers") or {}
            out.append(FieldClaim(
                field_key=field_key, value=value,
                value_status=ValueStatus(c.get("value_status") or "KNOWN"),
                value_origin=ValueOrigin(c.get("value_origin") or "REPORTED"),
                confidence=float(c.get("confidence") or 0.5), evidence=refs,
                qualifiers=ClaimQualifiers(source_kind=q.get("source_kind"),
                                           relations=q.get("relations") or [])))
        return out

def _claims_by_slot(claims: list[Mapping[str, Any]]) -> dict[Slot, list[Mapping[str, Any]]]:
    out: dict[Slot, list[Mapping[str, Any]]] = defaultdict(list)
    for c in claims:
        if c.get("review_status") == "CORRECTED":
            continue
        rnd = int(((c.get("qualifiers") or {}).get("extraction_round")) or 1)
        out[(c["field_key"], rnd)].append(c)
    return out

def compare(parent: Mapping[str, Any], cf_slots: list[dict[str, Any]],
            claims: list[Mapping[str, Any]], unreproduced: list[dict[str, Any]]
            ) -> dict[str, Any]:
    """Slot by slot: parent called / current code would call; what the skipped slots had."""
    p_called = {(s["field"], s["round"]): s for s in parent["slots"] if s["called"]}
    c_called = {(s["field"], s["round"]): s for s in cf_slots
                if s["bundle_status"] == "READY"}
    by_slot = _claims_by_slot(claims)
    per_field: dict[str, list[int]] = defaultdict(list)
    for s in p_called.values():
        if s["input_tokens"] is not None:
            per_field[s["field"]].append((s["input_tokens"] or 0) + (s["output_tokens"] or 0))
    every = [t for ts in per_field.values() for t in ts]
    run_avg = round(sum(every) / len(every)) if every else 0

    def cost(slot: Slot) -> int:
        s = p_called.get(slot)
        if s is not None and s["input_tokens"] is not None:
            return (s["input_tokens"] or 0) + (s["output_tokens"] or 0)
        ts = per_field.get(slot[0])
        return round(sum(ts) / len(ts)) if ts else run_avg

    skipped, added = [], []
    for slot, s in sorted(p_called.items(), key=lambda x: (x[0][1], x[0][0])):
        if slot in c_called:
            continue
        cf = next((x for x in cf_slots if (x["field"], x["round"]) == slot), None)
        had = by_slot.get(slot, [])
        skipped.append({
            "field": slot[0], "round": slot[1], "stage": s["stage"],
            "tokens": cost(slot), "status_now": cf["bundle_status"] if cf else "NOT_SCHEDULED",
            "skip_reason": cf.get("skip_reason") if cf else None,
            "claims": [{"value": c.get("value"), "validation": c.get("validation_outcome"),
                        "selected": c.get("is_selected")} for c in had],
        })
    for slot in sorted(set(c_called) - set(p_called), key=lambda x: (x[1], x[0])):
        added.append({"field": slot[0], "round": slot[1], "tokens_estimate": cost(slot)})
    parent_tokens = sum(cost(sl) for sl in p_called)
    cf_tokens = sum(cost(sl) for sl in c_called)
    # A field pruned because the catalog or assemble fills it is replaced, not lost.
    for x in skipped:
        x["replaced"] = x["status_now"] == "NOT_NEEDED" and bool(x["skip_reason"])
    lost = [x for x in skipped if not x["replaced"] and any(
        c["selected"] or c["validation"] in ("VALID", "VALID_WITH_WARNINGS")
        for c in x["claims"])]
    return {
        "parent_calls": len(p_called), "counterfactual_calls": len(c_called),
        "parent_tokens": parent_tokens, "counterfactual_tokens_estimate": cf_tokens,
        "token_change_pct": (round(100 * (cf_tokens - parent_tokens) / parent_tokens, 1)
                             if parent_tokens else None),
        "skipped_slots": skipped, "added_slots": added,
        "skipped_with_usable_claims": lost,
        "unreproduced_claims": unreproduced,
    }

async def counterfactual(engine: Engine, parent_run_id: str) -> dict[str, Any]:
    parent = audit_run(engine, parent_run_id)
    claims = EvidenceRepository(engine).claims(parent_run_id)
    gateway = RecordedGateway(claims)
    _, final = await replay_run(engine, parent_run_id, gateway, persist=False)
    cf_slots = slots_from_events(final.get("events") or [])
    report = compare(parent, cf_slots, claims, gateway.unreproduced)
    ws = (final["research_state"]["fields"].get("water_source") or {}).get("candidate_claims")
    return {"run_id": parent_run_id, "city": parent["city"], **report,
            "water_source_candidates": sorted({str(c["value"]) for c in ws or []}),
            "parent_water_source_candidates": sorted(
                {str(c.get("value")) for c in claims if c["field_key"] == "water_source"
                 and c.get("review_status") != "CORRECTED"}),
            "skip_events": [{"field": s["field"], "round": s["round"],
                             "status": s["bundle_status"], "reason": s.get("skip_reason")}
                            for s in cf_slots if s["bundle_status"] != "READY"]}

def render_markdown(reports: list[Mapping[str, Any]]) -> str:
    lines = ["# Counterfactual replay (recorded answers, no model calls)", "",
             "Each run's saved documents replayed in their original rounds with the current "
             "code; extraction answered with the claims the run got in the same (field, round). "
             "Counterfactual tokens are estimates from the run's own slots.", ""]
    for r in reports:
        lines += [f"## {r['city']} — {r['run_id'][:8]}", "",
                  f"- Model calls: {r['parent_calls']} → {r['counterfactual_calls']}",
                  f"- Tokens (input+output): {r['parent_tokens']:,} → "
                  f"~{r['counterfactual_tokens_estimate']:,} ({r['token_change_pct']}%)",
                  f"- Skipped slots that had usable claims: "
                  f"**{len(r['skipped_with_usable_claims'])}**",
                  f"- Claims not reproducible from the passages now shown: "
                  f"{len(r['unreproduced_claims'])}",
                  "- water_source candidates: "
                  f"{', '.join(r['parent_water_source_candidates']) or '—'} → "
                  f"{', '.join(r['water_source_candidates']) or '—'}", ""]
        if r["skipped_slots"]:
            lines += ["| round | field | stage | now | tokens | claims the run got there |",
                      "|---|---|---|---|---|---|"]
            for s in r["skipped_slots"]:
                got = "; ".join(
                    f"{c['value']} ({c['validation']}{', selected' if c['selected'] else ''})"
                    for c in s["claims"]) or "—"
                now = s["status_now"] + (" (filled at assemble)" if s["replaced"] else "")
            lines.append(f"| {s['round']} | {s['field']} | {s['stage']} | "
                         f"{now} | {s['tokens']:,} | {got} |")
            lines.append("")
        if r["added_slots"]:
            lines += ["Calls the current code adds: " + ", ".join(
                f"{a['field']} (round {a['round']})" for a in r["added_slots"]), ""]
        if r["unreproduced_claims"]:
            lines += ["Not reproducible: " + ", ".join(
                f"{u['field']} r{u['round']} = {u['value']}" for u in r["unreproduced_claims"]),
                ""]
    return "\n".join(lines)
