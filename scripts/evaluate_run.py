"""Evaluate a saved research run against a reference dataset (no research, no model calls;
research data is only read). Each invocation stores a new evaluation run.

    uv run python scripts/evaluate_run.py <research_run_id> [--dataset <version>] [--out dir]

Prints per-field results (RAW / AUTO / FINAL), diagnoses and the main metrics with their
numerators and denominators. --out writes the full evaluation as JSON.
"""

import argparse
import json
import sys
from pathlib import Path
from water_source_research.db.session import get_engine
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.store import EvaluationStore

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--dataset", default=None,
                        help="reference dataset version (default: latest with this city)")
    parser.add_argument("--out", type=Path, default=None, help="directory for the JSON result")
    args = parser.parse_args()
    engine = get_engine()
    try:
        eval_id = run_evaluation(engine, args.run_id, args.dataset)
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

if __name__ == "__main__":
    sys.exit(main())
