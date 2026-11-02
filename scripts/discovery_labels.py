"""Document labels for discovery evaluation (read-only on research data).

    uv run python scripts/discovery_labels.py export <run_id> [...] [--per-city 25]
        [--out tests/evaluation/discovery]      # labels_sample.csv + labels_key.json
    uv run python scripts/discovery_labels.py fill <proposals.json> [--out ...]
        # write drafted proposals {sample_id: {relevant, publisher_kind, serving_utility,
        # basis}} into the sheet (rows stay UNCONFIRMED)
    uv run python scripts/discovery_labels.py summary [--out ...]

A person reviews labels_sample.csv: fills confirmed_* (YES/NO/UNCERTAIN, a publisher kind
or UNKNOWN, YES/NO/UNKNOWN/N_A), notes, name and date, and sets label_status to CONFIRMED.
Only confirmed rows count as validated. The sheet shows no system output; labels_key.json
(which run and document each row is) is used only when computing metrics.
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from token_audit import resolve_ids
from water_source_research.db.session import get_engine
from water_source_research.evaluation.discovery_labels import (
    build_sample, candidate_documents, fill_proposals, read_labels, write_sheet,
)

DEFAULT_OUT = Path("tests/evaluation/discovery")

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("run_ids", nargs="+")
    e.add_argument("--per-city", type=int, default=25)
    e.add_argument("--seed", type=int, default=7)
    f = sub.add_parser("fill")
    f.add_argument("proposals", type=Path)
    sub.add_parser("summary")
    for p in sub.choices.values():
        p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    sheet, key = args.out / "labels_sample.csv", args.out / "labels_key.json"
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "export":
        # Strata and regions use the drafted identity (evaluation only; never research).
        os.environ.setdefault("CITY_IDENTITY_ALLOW_DRAFT", "1")
        from water_source_research.services.city_identity import load_city_identities
        engine = get_engine()
        docs = candidate_documents(engine, resolve_ids(engine, args.run_ids))
        sample = build_sample(docs, args.per_city, args.seed)
        identities = {(i.city, i.country): i for i in load_city_identities()}
        write_sheet(sample, identities, sheet, key)
        print(f"{len(sample)} documents -> {sheet} (key: {key})")
        print(dict(Counter(d["city"] for d in sample)))
    elif args.cmd == "fill":
        n = fill_proposals(sheet, json.loads(args.proposals.read_text(encoding="utf-8")))
        print(f"proposals written for {n} rows (UNCONFIRMED)")
    else:
        rows = read_labels(sheet)
        confirmed = sum(r["confirmed"] for r in rows)
        print(f"{len(rows)} rows, {confirmed} confirmed, {len(rows) - confirmed} unconfirmed")
        for dim in ("relevant", "publisher_kind", "serving_utility"):
            print(dim, dict(Counter(r[dim] for r in rows)))
    return 0

if __name__ == "__main__":
    sys.exit(main())
