"""Before/after comparison of full research runs, city by city (read only on research data).

    uv run python scripts/compare_research_runs.py --pairs pairs.json [--dataset <id>]
        [--judge live|cache-only] [--out reports/eval/discovery/new_runs]

pairs.json: [{"city": ..., "before": <run id>, "after": <run id>}, ...]. Each run is evaluated
(scripts/evaluate_run.py, the same dataset, comparison policy and judge mode for both; the
judge's cached verdicts are shared, so the same cell gets the same verdict), audited
(discovery_audit, token_audit) and summarised with absolute counts:
- PROPOSED (the value put forward before any person) and AUTO (accepted by the rules):
  answered / comparable cells, correct / answered (strict), lenient;
- water sources and utility name at PROPOSED against the reference (MATCH / answered);
- discovery: searches, site: searches and their hosts, documents fetched (OK), documents on
  hosts of another country (country-code or government host);
- extraction model calls and tokens;
- fields left for a person (review items of the latest round).
Cities without reference data get the research-side columns only.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def evaluate(run_id: str, dataset: str | None, judge: str, out: Path) -> str | None:
    cmd = ["uv", "run", "python", "scripts/evaluate_run.py", run_id, "--out", str(out)]
    if dataset:
        cmd += ["--dataset", dataset]
    if judge == "cache-only":
        cmd.append("--judge-cache-only")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       check=False)
    m = re.search(r"evaluation ([0-9a-f-]{36})", r.stdout)
    if not m:
        print(f"  no evaluation for {run_id[:8]}: {(r.stdout + r.stderr)[-300:]}",
              file=sys.stderr)
    return m.group(1) if m else None

def metric(ev: dict, scope: str, name: str) -> tuple[float, float] | None:
    for x in ev.get("metrics", []):
        if x["scope_key"] == scope and x["metric"] == name:
            return x.get("numerator"), x.get("denominator")
    return None

def frac(t) -> str:
    if not t or t[0] is None:
        return "—"
    n, d = t
    return f"{n:g}/{d:g}" if d else f"{n:g}"

def field_score(ev: dict, stage: str, field: str) -> str:
    rows = [r for r in ev.get("results", []) if r["stage"] == stage and r["field_key"] == field]
    answered = [r for r in rows if r["status"] not in ("MISSING_PREDICTION", "NOT_RESEARCHED",
                                                       "NO_REFERENCE")]
    match = sum(r["status"] == "MATCH" for r in answered)
    return f"{match}/{len(answered)} (of {len(rows)})" if rows else "—"

def research_side(run_id: str) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    from water_source_research.db.session import get_engine
    from water_source_research.evaluation.discovery_audit import audit_run
    from water_source_research.db.research_models import ResearchJobRecord
    from sqlalchemy.orm import Session
    from sqlalchemy import func, select
    from water_source_research.db.review_models import ReviewItemRecord
    engine = get_engine()
    a = audit_run(engine, run_id)
    with Session(engine) as s:
        job = s.get(ResearchJobRecord, run_id)
        latest = s.scalar(select(func.max(ReviewItemRecord.round)).where(
            ReviewItemRecord.run_id == run_id))
        items = s.scalar(select(func.count()).select_from(ReviewItemRecord).where(
            ReviewItemRecord.run_id == run_id, ReviewItemRecord.round == latest)) if latest \
            else 0
    country_hosts = Counter()
    for d in a["documents"]:
        if d["status"] == "OK" and d["cctld"]:
            country_hosts[d["cctld"]] += 1
    return {"status": job.status, "searches": a["searches"],
            "site_queries": a["site_queries"], "docs": a["documents_attempted"],
            "docs_ok": a["documents_ok"], "cctlds": dict(country_hosts),
            "calls": a["extraction"]["llm_calls"],
            "input_tokens": a["extraction"]["input_tokens"],
            "output_tokens": a["extraction"]["output_tokens"],
            "valid_claims": a["extraction"]["valid_claims"], "review_items": items}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--judge", choices=["live", "cache-only"], default="live")
    ap.add_argument("--out", type=Path, default=Path("reports/eval/discovery/new_runs"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))
    evals_dir = args.out / "evaluations"
    evals_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in pairs:
        entry = {"city": p["city"]}
        for side in ("before", "after"):
            run = p[side]
            ev_id = evaluate(run, args.dataset, args.judge, evals_dir) if p.get(
                "reference", True) else None
            ev = (json.loads((evals_dir / f"evaluation_{ev_id}.json").read_text(
                encoding="utf-8")) if ev_id else {})
            entry[side] = {"run": run, "eval": ev_id, "research": research_side(run),
                           "proposed": (frac(metric(ev, "PROPOSED", "answer_coverage")),
                                        frac(metric(ev, "PROPOSED", "accuracy_strict")),
                                        frac(metric(ev, "PROPOSED", "accuracy_lenient"))),
                           "auto": (frac(metric(ev, "AUTO", "coverage")),
                                    frac(metric(ev, "AUTO", "precision"))),
                           "water_source": field_score(ev, "PROPOSED", "water_source"),
                           "utility": field_score(ev, "PROPOSED", "utility_name"),
                           "any_candidate": frac(metric(ev, "RAW", "any_candidate_recall"))}
            print(p["city"], side, run[:8], ev_id and ev_id[:8], flush=True)
        rows.append(entry)
    (args.out / "comparison.json").write_text(json.dumps(rows, indent=1, default=str),
                                              encoding="utf-8")
    lines = ["# Research runs before and after Milestone 3", "",
             (f"Evaluation: dataset {args.dataset or '(latest)'}, judge {args.judge}. "
             "Answered/comparable cells and correct/answered are absolute counts; judged "
             "cells are provisional until adjudicated."), "",
             "## Answers against the reference", "",
             ("| City | | PROPOSED answered | correct (strict) | lenient | AUTO answered | "
             "AUTO correct | water source correct/answered | utility correct/answered |"),
             "|---|---|---|---|---|---|---|---|---|"]
    for e in rows:
        for side in ("before", "after"):
            s = e[side]
            lines.append(f"| {e['city'] if side == 'before' else ''} | {side} "
                         f"`{s['run'][:8]}` | {s['proposed'][0]} | {s['proposed'][1]} | "
                         f"{s['proposed'][2]} | {s['auto'][0]} | {s['auto'][1]} | "
                         f"{s['water_source']} | {s['utility']} |")
    lines += ["", "## Research behaviour", "",
              ("| City | | status | searches | site: searches (hosts) | documents (OK) | "
              "country-code hosts of OK documents | extraction calls | input / output "
              "tokens | VALID claims | review items |"), "|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in rows:
        for side in ("before", "after"):
            r = e[side]["research"]
            site = ", ".join(f"{h} ×{n}" for h, n in r["site_queries"].items()) or "—"
            cc = ", ".join(f".{k} {v}" for k, v in sorted(r["cctlds"].items())) or "—"
            lines.append(f"| {e['city'] if side == 'before' else ''} | {side} | {r['status']} | "
                         f"{r['searches']} | {site} | {r['docs']} ({r['docs_ok']}) | {cc} | "
                         f"{r['calls']} | {r['input_tokens'] or 0:,} / "
                         f"{r['output_tokens'] or 0:,} | {r['valid_claims']} | "
                         f"{r['review_items']} |")
    md = "\n".join(lines) + "\n"
    (args.out / "COMPARISON.md").write_text(md, encoding="utf-8")
    print(md)
    return 0

if __name__ == "__main__":
    sys.exit(main())
