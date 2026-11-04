"""E7: compare controlled saved-document replays of two code arms (read only).

    uv run python scripts/extraction_comparison.py reports/eval/geo31/extraction/replays.jsonl \
        [--out reports/eval/geo31/extraction/e7_comparison.json]

Input: replays.jsonl rows {condition, repeat, city, parent, run_id, exit, commit}; each
replay must already have a cache-only evaluation (evaluate_run.py --judge-cache-only), the
newest of which is used. Evaluation reads research data only; reference values stay in eval
tables and are never given to research, extraction or prompts.

Scoring separation (PROPOSED and AUTO stages, comparable cells only):
- confirmed: a deterministic comparison (no semantic judgment) or a person's adjudication;
- provisional: the status rests on a cached, unverified semantic-judge verdict;
- unscored: NEEDS_REVIEW (uncached judge cell or a cell needing a person). Never counted
  as incorrect. Missing predictions are coverage gaps, not errors.
Water sources: the evaluation's one-to-one pairing; judge-made pairs are provisional;
predicted sources absent from the reference are "not in reference" (may be wrong or missing
from the reference: a person decides), not counted as false outright.
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from water_source_research.db.evaluation_models import (
    EvalFieldResultRecord, EvalMetricRecord, EvalRunRecord,
)
from water_source_research.db.observability_models import ProviderUsageRecord
from water_source_research.db.research_models import RunEventRecord
from water_source_research.db.session import get_engine
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.revalidate import automatic_decisions

SKIP = {"MISSING_REFERENCE", "NOT_COMPARABLE", "NOT_RESEARCHED"}
CORRECT, PARTIAL = {"MATCH"}, {"PARTIAL_MATCH"}
WRONG = {"MISMATCH", "TEMPORAL_MISMATCH", "SCOPE_MISMATCH"}
OPEN = {"CONFLICT", "HITL_REQUIRED", "UNRESOLVED", "CANDIDATE"}

def cells(session: Session, eval_id: str, stage: str) -> dict:
    rows = session.scalars(select(EvalFieldResultRecord).where(
        EvalFieldResultRecord.eval_run_id == eval_id,
        EvalFieldResultRecord.stage == stage)).all()
    out = Counter()
    wrong, by_field = [], {}
    for r in rows:
        if r.status in SKIP:
            continue
        judged = bool((r.comparison or {}).get("judgment_id"))
        adjudicated = bool((r.links or {}).get("adjudication"))
        basis = "provisional" if judged and not adjudicated else "confirmed"
        if r.status == "NEEDS_REVIEW":
            out["unscored"] += 1
            by_field[(r.field_key, r.record_key)] = "unscored"
            continue
        if r.status == "MISSING_PREDICTION":
            out["missing"] += 1
            by_field[(r.field_key, r.record_key)] = "missing"
            continue
        kind = ("correct" if r.status in CORRECT else "partial" if r.status in PARTIAL
                else "wrong")
        out[f"{basis}_{kind}"] += 1
        by_field[(r.field_key, r.record_key)] = f"{basis}_{kind}"
        if kind == "wrong" and basis == "confirmed":
            wrong.append({"field": r.field_key, "record": r.record_key, "status": r.status,
                          "predicted": str((r.predicted or {}).get("value"))[:80],
                          "reference": str((r.reference or {}).get("value"))[:80]})
    out["comparable"] = sum(1 for k, v in by_field.items())
    return {"counts": dict(out), "confirmed_wrong": wrong, "by_cell": by_field}

def sources(session: Session, eval_id: str, stage: str) -> dict:
    m = session.scalar(select(EvalMetricRecord).where(
        EvalMetricRecord.eval_run_id == eval_id, EvalMetricRecord.scope_key == stage,
        EvalMetricRecord.metric == "source_recall"))
    if m is None:
        return {}
    d = m.detail or {}
    matched = d.get("matched") or []
    confirmed = [x for x in matched if not str(x.get("method", "")).startswith("JUDGE")]
    return {"reference": m.denominator, "matched_confirmed": len(confirmed),
            "matched_provisional": len(matched) - len(confirmed),
            "recall_confirmed": (len(confirmed) / m.denominator) if m.denominator else None,
            "matched": [x.get("predicted") for x in matched],
            "missing": d.get("missing") or [], "not_in_reference": d.get("extra") or [],
            "out_of_area": d.get("out_of_area"), "non_source_kind": d.get("non_source_kind")}

def usage(session: Session, run_id: str) -> dict:
    rows = session.scalars(select(ProviderUsageRecord).where(
        ProviderUsageRecord.run_id == run_id, ProviderUsageRecord.task == "extract")).all()
    calls = [r for r in rows if not r.cache_hit and (r.requests or 0) > 0]
    known = [r for r in calls if r.input_tokens is not None]
    return {"llm_calls": len(calls), "requests": sum(r.requests or 0 for r in rows),
            "cache_hits": sum(1 for r in rows if r.cache_hit),
            "input_tokens": sum(r.input_tokens for r in known),
            "output_tokens": sum(r.output_tokens or 0 for r in known),
            "unknown_usage_calls": len(calls) - len(known)}

def skips(session: Session, run_id: str) -> list[dict]:
    rows = session.scalars(select(RunEventRecord).where(
        RunEventRecord.run_id == run_id, RunEventRecord.kind == "NODE",
        RunEventRecord.node == "extract")).all()
    out = []
    for e in rows:
        a = e.attrs or {}
        if a.get("skip_reason"):
            out.append({"field": e.field_key, "round": a.get("discovery_round"),
                        "reason": str(a["skip_reason"]).split(":", 1)[0]})
    return out

def decisions(engine, run_id: str) -> dict:
    ev = EvidenceRepository(engine)
    auto = automatic_decisions(ev.resolutions(run_id))
    claims = {c["claim_uid"]: c for c in ev.claims(run_id)}
    accepted = {}
    for (field, entity), d in auto.items():
        if d["status"] == "RESOLVED":
            accepted[f"{field}|{entity}"] = sorted(
                str((claims.get(u) or {}).get("normalized_value")
                    if (claims.get(u) or {}).get("normalized_value") is not None
                    else (claims.get(u) or {}).get("value")) for u in d["selected"])
    open_items = [k for k, d in auto.items() if d["status"] in OPEN]
    by_status = Counter(d["status"] for d in auto.values())
    return {"auto_accepted": accepted, "review_items": len(open_items),
            "decision_statuses": dict(by_status), "claims": len(claims)}

def correct(r: dict) -> int:
    return (r.get("PROPOSED") or {}).get("counts", {}).get("confirmed_correct", 0)

def summarize(values: list[float]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "mean": round(statistics.mean(vals), 3), "min": min(vals),
            "max": max(vals)}

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("replays")
    p.add_argument("--out")
    a = p.parse_args()
    engine = get_engine()
    rows = [json.loads(line) for line in Path(a.replays).read_text().splitlines() if line]
    rows = [r for r in rows if r.get("exit") == 0 and r.get("run_id")]
    per = []
    with Session(engine) as s:
        for r in rows:
            ev = s.scalars(select(EvalRunRecord).where(
                EvalRunRecord.research_run_id == r["run_id"]).order_by(
                EvalRunRecord.created_at.desc())).first()
            item = {**r, "eval_run_id": ev.id if ev else None, "usage": usage(s, r["run_id"]),
                    "skips": skips(s, r["run_id"]), **decisions(engine, r["run_id"])}
            if ev is not None:
                for stage in ("PROPOSED", "AUTO"):
                    item[stage] = cells(s, ev.id, stage)
                    item[f"sources_{stage}"] = sources(s, ev.id, stage)
            per.append(item)
    # per city and arm: mean and spread over repeats
    table = defaultdict(dict)
    for city in sorted({r["city"] for r in per}):
        for arm in ("baseline", "improved"):
            rs = [r for r in per if r["city"] == city and r["condition"] == arm]
            if not rs:
                continue
            def g(f, rs=rs):
                return summarize([f(r) for r in rs])
            table[city][arm] = {
                "repeats": len(rs),
                "source_recall_confirmed": g(lambda r: (r.get("sources_PROPOSED") or {})
                                             .get("recall_confirmed")),
                "sources_matched_provisional": g(lambda r: (r.get("sources_PROPOSED") or {})
                                                 .get("matched_provisional")),
                "sources_not_in_reference": g(lambda r: len((r.get("sources_PROPOSED") or {})
                                                            .get("not_in_reference") or [])),
                "proposed_confirmed_correct": g(correct),
                "proposed_confirmed_wrong": g(lambda r: (r.get("PROPOSED") or {}).get(
                    "counts", {}).get("confirmed_wrong", 0)),
                "proposed_provisional": g(lambda r: sum(v for k, v in (r.get("PROPOSED") or {})
                                                        .get("counts", {}).items()
                                                        if k.startswith("provisional"))),
                "proposed_unscored": g(lambda r: (r.get("PROPOSED") or {}).get(
                    "counts", {}).get("unscored", 0)),
                "proposed_missing": g(lambda r: (r.get("PROPOSED") or {}).get(
                    "counts", {}).get("missing", 0)),
                "auto_accepted": g(lambda r: len(r["auto_accepted"])),
                "auto_confirmed_correct": g(lambda r: (r.get("AUTO") or {}).get(
                    "counts", {}).get("confirmed_correct", 0)),
                "auto_confirmed_wrong": g(lambda r: (r.get("AUTO") or {}).get(
                    "counts", {}).get("confirmed_wrong", 0)),
                "review_items": g(lambda r: r["review_items"]),
                "llm_calls": g(lambda r: r["usage"]["llm_calls"]),
                "input_tokens": g(lambda r: r["usage"]["input_tokens"]),
                "output_tokens": g(lambda r: r["usage"]["output_tokens"]),
                "tokens_per_confirmed_correct": g(
                    lambda r: ((r["usage"]["input_tokens"] + r["usage"]["output_tokens"])
                               / correct(r)) if correct(r) else None),
                "off_topic_skips": g(lambda r: sum(x["reason"] == "OFF_TOPIC"
                                                   for x in r["skips"])),
            }
    # useful evidence skipped: fields the improved arm skipped as OFF_TOPIC where a
    # baseline replay of the same city has a confirmed-correct PROPOSED cell
    lost = []
    for r in per:
        if r["condition"] != "improved":
            continue
        skipped = {x["field"] for x in r["skips"] if x["reason"] == "OFF_TOPIC"}
        base = [b for b in per if b["city"] == r["city"] and b["condition"] == "baseline"]
        mine = (r.get("PROPOSED") or {}).get("by_cell", {})
        for f in sorted(skipped):
            for b in base:
                for (field, rec), v in (b.get("PROPOSED") or {}).get("by_cell", {}).items():
                    if field == f and v.endswith("correct") and not str(
                            mine.get((field, rec), "")).endswith("correct"):
                        lost.append({"city": r["city"], "improved_run": r["run_id"][:8],
                                     "baseline_run": b["run_id"][:8], "field": f,
                                     "record": rec, "baseline": v,
                                     "improved": mine.get((field, rec))})
    # automatic acceptance: values accepted by one arm and not the other (per city)
    auto_diff = {}
    for city in sorted({r["city"] for r in per}):
        sets = {arm: [{f"{k}={v}" for k, v in r["auto_accepted"].items()}
                      for r in per if r["city"] == city and r["condition"] == arm]
                for arm in ("baseline", "improved")}
        if sets["baseline"] and sets["improved"]:
            b_all = set.intersection(*sets["baseline"])
            i_all = set.intersection(*sets["improved"])
            auto_diff[city] = {"stable_in_baseline_only": sorted(b_all - i_all),
                               "stable_in_improved_only": sorted(i_all - b_all),
                               "stable_in_both": len(b_all & i_all)}
    for r in per:
        for stage in ("PROPOSED", "AUTO"):
            if stage in r:
                r[stage].pop("by_cell", None)
    out = {"replays": per, "by_city": table, "useful_evidence_skipped": lost,
           "auto_acceptance_differences": auto_diff}
    text = json.dumps(out, indent=1, default=str, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(json.dumps({"by_city": table, "useful_evidence_skipped": lost,
                      "auto_acceptance_differences": auto_diff}, indent=1, default=str,
                     ensure_ascii=False)[:30000])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
