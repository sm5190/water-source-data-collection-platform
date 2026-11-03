"""Geographic label sheet: wrong-place inclusions vs false exclusions (evaluation only).

    uv run python scripts/geo_labels.py export <run_id> [...] [--per-stratum 8]
        [--out tests/evaluation/geo31]          # geo_labels.csv + geo_labels_key.json
    uv run python scripts/geo_labels.py fill <proposals.json> [--out ...]
        # drafted proposals {sample_id: {about_place, basis}} or passage proposals
        # {sample_id: {passages: {"1": TARGET, ...}, passage_basis}} (rows stay UNCONFIRMED)
    uv run python scripts/geo_labels.py metrics [--out ...] [--report reports/eval/geo31/x.md]

The strata come from the current geography rules with the DRAFT city identity
(CITY_IDENTITY_ALLOW_DRAFT=1, evaluation only); the reviewer sees neither. A person fills
confirmed_about_place (TARGET / SUPPLY_SOURCE / MIXED / OTHER_PLACE / UNCERTAIN), name and
date, and sets label_status CONFIRMED. Each quoted passage has its own label
(passage_<i>_place_confirmed: TARGET / SUPPLY_SOURCE / OTHER_PLACE / NOT_ABOUT_PLACE /
UNCERTAIN): a MIXED document counts as relevant only through a relevant passage. Read only on
research data.
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from token_audit import resolve_ids

DEFAULT_OUT = Path("tests/evaluation/geo31")

def _fill(sheet: Path, proposals: dict) -> int:
    from water_source_research.evaluation.geo_labels import COLUMNS
    rows = list(csv.DictReader(sheet.open(encoding="utf-8", newline="")))
    n = 0
    for r in rows:
        for k in COLUMNS:
            r.setdefault(k, "")
        p = proposals.get(r["sample_id"])
        if p:
            if "about_place" in p:
                r["proposed_about_place"], r["proposed_basis"] = p.get("about_place", ""), \
                    p.get("basis", "")
            for i, place in (p.get("passages") or {}).items():
                r[f"passage_{i}_place_proposed"] = place
            if p.get("passage_basis") and p["passage_basis"] not in r["proposed_basis"]:
                r["proposed_basis"] = "; ".join(x for x in (r["proposed_basis"],
                                                             p["passage_basis"]) if x)
            n += 1
    with sheet.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return n

def _render(m: dict) -> str:
    lines = ["# Geographic precision and false exclusions", "",
             ("Rates on the sampled documents: decided = rows labelled other than UNCERTAIN. "
              "Validated = person-confirmed rows; provisional = drafts."), "",
             ("| city | metric | status | relevant / decided | uncertain | undetermined (MIXED, "
              "no relevant passage) | unlabelled |"),
             "|---|---|---|---|---|---|---|"]
    for city, metrics in m.items():
        if city.startswith("_"):
            continue
        for metric, by_status in metrics.items():
            for status, r in by_status.items():
                lines.append(f"| {city} | {metric} | {status} | {r['about_target']}/"
                             f"{r['decided']} | {r['uncertain']} | {r['undetermined']} | "
                             f"{r['unlabelled']} |")
    lines += ["", "Pool sizes (documents per stratum before sampling): "
              + json.dumps(m.get("_pool_sizes", {}))]
    return "\n".join(lines) + "\n"

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("run_ids", nargs="+")
    e.add_argument("--per-stratum", type=int, default=8)
    e.add_argument("--seed", type=int, default=11)
    f = sub.add_parser("fill")
    f.add_argument("proposals", type=Path)
    mt = sub.add_parser("metrics")
    mt.add_argument("--report", type=Path, default=None)
    for p in sub.choices.values():
        p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sheet, key_path = args.out / "geo_labels.csv", args.out / "geo_labels_key.json"
    os.environ.setdefault("CITY_IDENTITY_ALLOW_DRAFT", "1")  # evaluation only
    from water_source_research.evaluation.geo_labels import (
        build_geo_sample, geo_metrics, pools, read_geo_labels, write_geo_sheet,
    )
    if args.cmd == "export":
        from water_source_research.db.session import get_engine
        from water_source_research.services.city_identity import load_city_identities
        engine = get_engine()
        city_pools = pools(engine, resolve_ids(engine, args.run_ids))
        sizes = {c: {s: len(v) for s, v in st.items()} for c, st in city_pools.items()}
        sample = build_geo_sample(city_pools, args.per_stratum, args.seed)
        regions = {(i.city, i.country): (i.region.name if i.region else "")
                   for i in load_city_identities()}
        write_geo_sheet(sample, regions, sheet, key_path, sizes)
        print(f"{len(sample)} documents -> {sheet}; pools {json.dumps(sizes)}")
    elif args.cmd == "fill":
        print(_fill(sheet, json.loads(args.proposals.read_text(encoding="utf-8"))),
              "rows proposed (UNCONFIRMED)")
    else:
        key = json.loads(key_path.read_text(encoding="utf-8"))
        md = _render(geo_metrics(read_geo_labels(sheet), key))
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(md, encoding="utf-8")
        print(md)
    return 0

if __name__ == "__main__":
    sys.exit(main())
