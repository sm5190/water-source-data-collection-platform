"""Revalidate stored runs (mode REVALIDATE; no discovery, fetching, model calls or review).

    uv run python scripts/revalidate_run.py <run_id> [<run_id> ...] [--verify] [--out file.json]

Each parent's claims as extracted (reviewer corrections left out) and its evidence go through
the current validation and resolution as a new run; the parent is only read. Prints the new
run id per parent. --verify lists every field/entity whose automatic decision differs from the
parent's (the parent's last RULES decision before any reviewer decision); --out writes
{parent: {run_id, counts, differences}} as JSON.
"""

import argparse
import json
import sys
from pathlib import Path
from water_source_research.db.session import get_engine
from water_source_research.research_queue.checkpointing import run_async
from water_source_research.services.revalidate import compare_decisions, revalidate_run

async def main(run_ids: list[str], verify: bool, out: Path | None) -> int:
    engine = get_engine()
    report: dict[str, dict] = {}
    for parent in run_ids:
        run_id, final = await revalidate_run(engine, parent)
        entry = {"run_id": run_id, "counts": final["revalidation"],
                 "completion": final.get("completion")}
        line = f"{parent} -> revalidation {run_id} ({final['revalidation']['claims']} claims)"
        if verify:
            diffs = compare_decisions(engine, parent, run_id)
            entry["differences"] = diffs
            line += f": {len(diffs)} decision difference(s)"
        print(line)
        for d in entry.get("differences", []):
            b, a = d["before"] or {}, d["after"] or {}
            print(f"    {d['field_key']} [{d['entity_key']}]: "
                  f"{b.get('status')}/{b.get('rule')} ({b.get('policy_version')}) -> "
                  f"{a.get('status')}/{a.get('rule')} ({a.get('policy_version')})")
        report[parent] = entry
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--verify", action="store_true",
                        help="compare automatic decisions with the parent's")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    sys.exit(run_async(main(args.run_ids, args.verify, args.out)))
