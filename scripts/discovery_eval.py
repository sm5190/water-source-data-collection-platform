"""Discovery-only evaluation on stored runs (read only; no network, no model calls).

    uv run python scripts/discovery_eval.py <run_id> [...] [--identity off|draft]
        [--labels tests/evaluation/discovery] [--out reports/eval/discovery/offline]

``--identity draft`` evaluates as if the drafted city identities were approved
(CITY_IDENTITY_ALLOW_DRAFT); ``off`` (default) is production until approval. Writes
discovery_eval_<identity>.md / .json.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from token_audit import resolve_ids

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="+")
    ap.add_argument("--identity", choices=["off", "draft"], default="off")
    ap.add_argument("--labels", type=Path, default=Path("tests/evaluation/discovery"))
    ap.add_argument("--out", type=Path, default=Path("reports/eval/discovery/offline"))
    args = ap.parse_args()
    if args.identity == "draft":
        os.environ["CITY_IDENTITY_ALLOW_DRAFT"] = "1"
    else:
        os.environ.pop("CITY_IDENTITY_ALLOW_DRAFT", None)
    from water_source_research.db.session import get_engine
    from water_source_research.evaluation.discovery_labels import read_labels
    from water_source_research.evaluation.discovery_metrics import (
        evaluate, load_expectations, render, totals,
    )
    engine = get_engine()
    labels = read_labels(args.labels / "labels_sample.csv")
    key = json.loads((args.labels / "labels_key.json").read_text(encoding="utf-8"))
    expectations = load_expectations(args.labels / "utility_expectations.yaml")
    results = evaluate(engine, resolve_ids(engine, args.run_ids), labels, key, expectations)
    args.out.mkdir(parents=True, exist_ok=True)
    md = render(results, f"Discovery evaluation, stored runs (identity {args.identity})")
    md += "\n\nTotals: " + json.dumps(totals(results)) + "\n"
    (args.out / f"discovery_eval_{args.identity}.md").write_text(md, encoding="utf-8")
    (args.out / f"discovery_eval_{args.identity}.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    return 0

if __name__ == "__main__":
    sys.exit(main())
