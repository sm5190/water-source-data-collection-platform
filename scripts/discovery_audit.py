"""What a run's discovery did, round by round (read only).

    uv run python scripts/discovery_audit.py <run_id> [<run_id> ...] [--out reports/eval/discovery]

Writes discovery_audit.md and discovery_audit.json (every search, hit and document) to --out and
prints the Markdown. Run ids may be given as unique prefixes.
"""

import argparse
import json
import sys
from pathlib import Path
from token_audit import resolve_ids
from water_source_research.db.session import get_engine
from water_source_research.evaluation.discovery_audit import audit_runs, render_markdown

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--out", default="reports/eval/discovery")
    args = parser.parse_args()
    engine = get_engine()
    report = audit_runs(engine, resolve_ids(engine, args.run_ids))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = render_markdown(report)
    (out / "discovery_audit.md").write_text(md, encoding="utf-8")
    (out / "discovery_audit.json").write_text(json.dumps(report, indent=2, default=str),
                                              encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    print(f"\nwritten to {out / 'discovery_audit.md'} and discovery_audit.json")
    return 0

if __name__ == "__main__":
    sys.exit(main())
