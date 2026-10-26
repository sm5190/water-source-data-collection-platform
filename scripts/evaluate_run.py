"""Evaluate a saved research run against a reference dataset (no research, no model calls;
research data is only read). Each invocation stores a new evaluation run.

    uv run python scripts/evaluate_run.py <research_run_id> [--dataset <version>] [--out dir]

Prints per-field results (RAW / AUTO / FINAL), diagnoses and the main metrics with their
numerators and denominators. --out writes the full evaluation as JSON. Cells the deterministic
comparison can't decide (descriptions, name variants) go to the semantic judge on the configured
LLM provider (cached in eval_judgments; provisional until a person verifies them); --no-judge
keeps the evaluation fully deterministic.

    uv run python scripts/evaluate_run.py --compare-manual tests/evaluation/toronto_manual_findings.yaml
        [--eval-run <id>] [--out dir]

checks the latest evaluation of the findings' research run (or --eval-run; one is created if
none exists) against independently written manual findings and lists every disagreement.
"""

import argparse
import json
import sys
from pathlib import Path
from water_source_research.db.session import get_engine
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.judge import build_judge
from water_source_research.evaluation.manual import compare_manual, load_findings, manual_markdown
from water_source_research.evaluation.report import effective
from water_source_research.evaluation.store import EvaluationStore

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--dataset", default=None,
                        help="reference dataset version (default: latest with this city)")
    parser.add_argument("--out", type=Path, default=None, help="directory for the JSON result")
    parser.add_argument("--compare-manual", type=Path, default=None,
                        help="YAML manual findings to check the evaluation against")
    parser.add_argument("--eval-run", default=None,
                        help="with --compare-manual: the evaluation to check")
    parser.add_argument("--no-judge", action="store_true",
                        help="deterministic comparison only (no semantic LLM judge)")
    args = parser.parse_args()
    engine = get_engine()
    if args.compare_manual:
        return compare(engine, args)
    if not args.run_id:
        parser.error("run_id is required (unless --compare-manual)")
    judge = None if args.no_judge else build_judge(engine)
    if judge is not None and judge.llm is None:
        print(f"semantic judge unavailable ({judge.unavailable}): undecided cells will need "
              "a person")
    try:
        eval_id = run_evaluation(engine, args.run_id, args.dataset, judge=judge)
    except LookupError as exc:
        print(f"cannot evaluate: {exc}")
        return 2
    store = EvaluationStore(engine)
    run = store.run(eval_id)
    results = store.field_results(eval_id)
    diagnoses = store.diagnoses(eval_id)
    metrics = store.metrics(eval_id)
    print(f"evaluation {eval_id} of research run {run['research_run_id']} (origin "
          f"{run['origin_run_id'][:8]}) against dataset {run['dataset_id']}, stages "
          f"{', '.join(run['stages'])}, policy {run['compare_policy_version']}, snapshot "
          f"{run['snapshot_sha256'][:12]}\n")
    stages = run["stages"]
    cells: dict[tuple[str, str], dict[str, str]] = {}
    for r in results:
        cells.setdefault((r["record_key"], r["field_key"]), {})[r["stage"]] = r["status"]
    print(f"{'field':<48} " + " ".join(f"{s:<18}" for s in stages))
    for (record, field), by_stage in cells.items():
        label = field if not record else f"{field} [{record}]"
        print(f"{label[:48]:<48} " + " ".join(f"{by_stage.get(s, ''):<18}" for s in stages))
    print("\nDiagnoses:")
    for d in diagnoses:
        where = f"{d['field_key']} [{d['record_key']}]" if d["record_key"] else d["field_key"]
        print(f"  {d['stage']:<5} {where[:50]:<50} {d['root_cause']:<26} {d['confidence']:<9} "
              f"{','.join(d['symptoms'])}")
    print("\nMetrics:")
    for m in metrics:
        if m["scope"] in ("stage", "reliability") and m["denominator"] is not None:
            print(f"  {m['scope_key']:<12} {m['metric']:<30} {m['numerator']:g}/"
                  f"{m['denominator']:g} = {m['value']}")
    print(f"\nInstrumentation gaps: {len(run['gaps'])} (see the evaluation record)")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"evaluation_{eval_id}.json"
        path.write_text(json.dumps({
            "run": run, "results": results, "diagnoses": diagnoses, "metrics": metrics,
            "evidence": store.evidence_checks(eval_id)}, indent=1, default=str,
            ensure_ascii=False), encoding="utf-8")
        print(f"wrote {path}")
    return 0

def compare(engine, args) -> int:
    manual = load_findings(args.compare_manual)
    store = EvaluationStore(engine)
    run_id = args.run_id or manual["research_run_id"]
    eval_id = args.eval_run or next(
        (r["id"] for r in store.runs(run_id) if r["status"] == "COMPLETED"), None)
    if eval_id is None:
        try:
            eval_id = run_evaluation(engine, run_id, args.dataset,
                                     judge=None if args.no_judge else build_judge(engine))
        except LookupError as exc:
            print(f"cannot evaluate: {exc}")
            return 2
    view = effective(store, eval_id)
    if view is None:
        print(f"unknown evaluation {eval_id}")
        return 2
    rows = compare_manual(view, manual)
    text = manual_markdown(rows, f"Evaluator vs manual findings: evaluation {eval_id} "
                                 f"({view['metrics_basis']}) of research run {run_id}")
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"manual_comparison_{eval_id[:8]}.md"
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0

if __name__ == "__main__":
    from water_source_research.observability.otel import configure_telemetry, shutdown_telemetry
    configure_telemetry("evaluation")  # optional (OBSERVABILITY__OTEL_ENABLED)
    try:
        code = main()
    finally:
        shutdown_telemetry()
    sys.exit(code)
