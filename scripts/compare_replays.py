"""Before/after comparison of controlled replays (Milestone 2, step 3).

    uv run python scripts/compare_replays.py reports/eval/tokens/replays/replays.jsonl \
        [--out reports/eval/tokens/replays]

Input: one JSON line per replay ({"condition", "repeat", "city", "run_id", "seconds", ...};
conditions "baseline", "optimized", "optimized_cache"). Per replay: the token audit, claims,
water_source candidates, automatic decisions and wall time; for cities in the reference
dataset, a new evaluation with cached judge verdicts only (no new judge calls; uncached cells
count as needing a person): PROPOSED / AUTO accuracy and coverage (city_name / country
reported apart: catalog-filled now, extracted before), source precision / recall, and tokens
per correct answer. Repeats of a condition show the model's run-to-run variation.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import FieldClaimRecord
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.db.session import get_engine
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.judge import build_judge
from water_source_research.evaluation.metrics import simple_scores
from water_source_research.evaluation.report import effective
from water_source_research.evaluation.store import EvaluationStore
from water_source_research.evaluation.views import source_scores
from water_source_research.observability.token_audit import audit_run

IDENTITY = ("city_name", "country")
ORDER = {"baseline": 0, "optimized": 1, "optimized_cache": 2, "optimized_v2": 3,
         "optimized_v2_cache": 4}
STAGES = ("PROPOSED", "AUTO")

def claims_summary(engine, run_id: str) -> dict[str, Any]:
    with Session(engine) as s:
        rows = s.scalars(select(FieldClaimRecord).where(FieldClaimRecord.run_id == run_id)).all()
        job = s.get(ResearchJobRecord, run_id)
    rows = [r for r in rows if r.review_status != "CORRECTED"]
    usable = [r for r in rows if r.validation_outcome in ("VALID", "VALID_WITH_WARNINGS")]
    fields_with_claims = {r.field_key for r in usable}
    states = (job.result or {}).get("research_state", {}).get("fields", {}) if job else {}
    resolved = sorted(k for k, f in states.items() if f.get("status") == "RESOLVED")
    graph_seconds = ((job.finished_at - job.started_at).total_seconds()
                     if job and job.finished_at and job.started_at else None)
    return {"claims": len(rows), "usable_claims": len(usable),
            "fields_with_usable_claims": len(fields_with_claims),
            "fields_resolved_automatically": len(resolved), "resolved_fields": resolved,
            "water_source_candidates": sorted({str(r.value) for r in rows
                                               if r.field_key == "water_source"}),
            "graph_seconds": graph_seconds}

def evaluation_summary(engine, run_id: str) -> dict[str, Any] | None:
    judge = build_judge(engine, cache_only=True)
    try:
        eval_id = run_evaluation(engine, run_id, None, judge=judge)
    except LookupError:
        return None  # city not in the reference dataset
    view = effective(EvaluationStore(engine), eval_id)
    out: dict[str, Any] = {"eval_run_id": eval_id, "sources": source_scores(view)}
    for stage in STAGES:
        rows = [r for r in view["results"] if r["stage"] == stage]
        out[stage] = simple_scores([r for r in rows if r["field_key"] not in IDENTITY])
        out[stage]["identity"] = {r["field_key"]: r["status"] for r in rows
                                  if r["field_key"] in IDENTITY}
        out[stage]["needs_person"] = sum(r["status"] == "NEEDS_REVIEW" for r in rows)
    return out

def summarize(engine, row: dict[str, Any]) -> dict[str, Any]:
    run_id = row["run_id"]
    audit = audit_run(engine, run_id)
    t = audit["total"]
    spent = (t["input_tokens"] or 0) + (t["output_tokens"] or 0)
    result = {**row, "audit": {k: t[k] for k in (
        "llm_calls", "model_calls", "app_cache_hits", "tokens_avoided", "input_tokens",
        "output_tokens", "provider_cached_tokens", "zero_claim_calls", "productive_calls",
        "skipped")}, "by_round": [{k: g[k] for k in ("round", "llm_calls", "input_tokens",
                                                      "output_tokens", "zero_claim_calls")}
                                   for g in audit["by_round"]],
              "tokens_spent": spent, **claims_summary(engine, run_id)}
    ev = evaluation_summary(engine, run_id)
    result["evaluation"] = ev
    if ev:
        for stage in STAGES:
            match = ev[stage].get("match") or 0
            result[f"tokens_per_correct_{stage.lower()}"] = round(spent / match) if match else None
    return result

def mean(values: list[Any]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.mean(vals), 3) if vals else None

def spread(values: list[Any]) -> str:
    vals = [v for v in values if v is not None]
    return f"{min(vals):g}–{max(vals):g}" if len(vals) > 1 else "—"

METRICS = [
    ("Model calls", lambda r: r["audit"]["model_calls"]),
    ("App-cache hits", lambda r: r["audit"]["app_cache_hits"]),
    ("Input tokens", lambda r: r["audit"]["input_tokens"]),
    ("Output tokens", lambda r: r["audit"]["output_tokens"]),
    ("Provider-cached input tokens", lambda r: r["audit"]["provider_cached_tokens"]),
    ("Tokens avoided by app cache", lambda r: r["audit"]["tokens_avoided"]),
    ("Zero-claim calls", lambda r: r["audit"]["zero_claim_calls"]),
    ("Usable (VALID) claims", lambda r: r["usable_claims"]),
    ("Fields with a usable claim (of 32)", lambda r: r["fields_with_usable_claims"]),
    ("Fields resolved automatically", lambda r: r["fields_resolved_automatically"]),
    ("Wall time (s)", lambda r: r["seconds"]),
    ("PROPOSED accuracy (strict)", lambda r: (r["evaluation"] or {}).get("PROPOSED", {})
     .get("accuracy_strict")),
    ("PROPOSED coverage", lambda r: (r["evaluation"] or {}).get("PROPOSED", {}).get("coverage")),
    ("AUTO accuracy (strict)", lambda r: (r["evaluation"] or {}).get("AUTO", {})
     .get("accuracy_strict")),
    ("AUTO coverage", lambda r: (r["evaluation"] or {}).get("AUTO", {}).get("coverage")),
    ("PROPOSED source recall", lambda r: ((r["evaluation"] or {}).get("sources", {})
                                         .get("PROPOSED", {}).get("source_recall") or {})
     .get("value")),
    ("PROPOSED source precision", lambda r: ((r["evaluation"] or {}).get("sources", {})
                                            .get("PROPOSED", {}).get("source_precision") or {})
     .get("value")),
    ("Tokens per correct PROPOSED answer", lambda r: r.get("tokens_per_correct_proposed")),
    ("Tokens per correct AUTO answer", lambda r: r.get("tokens_per_correct_auto")),
]

def render(results: list[dict[str, Any]]) -> str:
    intro = ("Same saved documents in their original acquisition rounds, real VT model calls. "
             "Baseline = code before Milestone 2 (4c63007); optimized = milestone code with the "
             "persistent cache in write_only (every call paid); *_cache = one more optimized "
             "replay reading the persistent cache; optimized_v2 = after restoring the "
             "target-field header (extract-5). Values: mean of repeats, with the range across "
             "repeats (the model's run-to-run variation). Evaluation uses cached judge verdicts "
             "only; city_name / country are scored apart (catalog-filled after).")
    lines = ["# Controlled replays: before / after", "", intro, ""]
    for city in sorted({r["city"] for r in results}):
        rows = [r for r in results if r["city"] == city]
        conds = list(dict.fromkeys(r["condition"] for r in sorted(
            rows, key=lambda r: ORDER.get(r["condition"], 99))))
        lines += [f"## {city}", "", "| metric | " + " | ".join(
            f"{c} (mean) | {c} range" for c in conds) + " |",
                  "|---|" + "---|---|" * len(conds)]
        for name, get in METRICS:
            cells = []
            for c in conds:
                vals = [get(r) for r in rows if r["condition"] == c]
                cells += [str(mean(vals)) if mean(vals) is not None else "—", spread(vals)]
            if all(x in ("—",) for x in cells[::2]):
                continue
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines.append("")
        for r in rows:
            ev = r["evaluation"] or {}
            lines.append(f"- {r['condition']} #{r['repeat']} `{r['run_id'][:8]}`: water_source "
                         f"{', '.join(r['water_source_candidates']) or '—'}; skipped "
                         f"{r['audit']['skipped']}"
                         + (f"; identity AUTO {ev['AUTO']['identity']}" if ev else ""))
        lines.append("")
    return "\n".join(lines)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("replays", type=Path)
    parser.add_argument("--out", type=Path, default=Path("reports/eval/tokens/replays"))
    args = parser.parse_args()
    engine = get_engine()
    rows = [json.loads(line) for line in args.replays.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    rows = [r for r in rows if r.get("run_id") and r.get("exit") == 0]
    results = [summarize(engine, r) for r in rows]
    args.out.mkdir(parents=True, exist_ok=True)
    md = render(results)
    (args.out / "comparison.md").write_text(md, encoding="utf-8")
    (args.out / "comparison.json").write_text(json.dumps(results, indent=1, default=str),
                                             encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    return 0

if __name__ == "__main__":
    sys.exit(main())
