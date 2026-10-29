"""What would the current extraction scheduling and gating do on a run's saved documents?
No model calls, nothing written to the database (services/counterfactual.py).

    uv run python scripts/counterfactual_replay.py <run_id> [<run_id> ...] [--out DIR]

Writes counterfactual.md / counterfactual.json to --out (default reports/eval/tokens/
counterfactual) and prints the Markdown. Run ids may be unique prefixes.
"""

import argparse
import json
import sys
from pathlib import Path
from water_source_research.db.session import get_engine
from water_source_research.research_queue.checkpointing import run_async
from water_source_research.services.counterfactual import counterfactual, render_markdown

sys.path.insert(0, str(Path(__file__).parent))
from token_audit import resolve_ids

async def main(run_ids: list[str], out: Path) -> int:
    engine = get_engine()
    reports = [await counterfactual(engine, r) for r in resolve_ids(engine, run_ids)]
    out.mkdir(parents=True, exist_ok=True)
    md = render_markdown(reports)
    (out / "counterfactual.md").write_text(md, encoding="utf-8")
    (out / "counterfactual.json").write_text(json.dumps(reports, indent=2, default=str),
                                             encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    print(f"\nwritten to {out / 'counterfactual.md'}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--out", default="reports/eval/tokens/counterfactual")
    args = parser.parse_args()
    sys.exit(run_async(main(args.run_ids, Path(args.out))))
