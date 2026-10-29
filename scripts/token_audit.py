"""Where a run's extraction tokens went: per round, field and stage (read only).

    uv run python scripts/token_audit.py <run_id> [<run_id> ...] [--out reports/eval/tokens]

Writes token_audit.md and token_audit.json (with every slot) to --out and prints the Markdown.
Run ids may be given as unique prefixes.
"""

import argparse
import json
import sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.db.session import get_engine
from water_source_research.observability.token_audit import audit_runs, render_markdown

def resolve_ids(engine, prefixes: list[str]) -> list[str]:
    out = []
    with Session(engine) as session:
        for p in prefixes:
            ids = session.scalars(select(ResearchJobRecord.run_id)
                                  .where(ResearchJobRecord.run_id.like(f"{p}%"))).all()
            if len(ids) != 1:
                raise SystemExit(f"run id {p!r} matches {len(ids)} runs")
            out.append(ids[0])
    return out

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--out", default="reports/eval/tokens")
    args = parser.parse_args()
    engine = get_engine()
    report = audit_runs(engine, resolve_ids(engine, args.run_ids))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = render_markdown(report)
    (out / "token_audit.md").write_text(md, encoding="utf-8")
    (out / "token_audit.json").write_text(json.dumps(report, indent=2, default=str),
                                          encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    print(f"\nwritten to {out / 'token_audit.md'} and token_audit.json")
    return 0

if __name__ == "__main__":
    sys.exit(main())
