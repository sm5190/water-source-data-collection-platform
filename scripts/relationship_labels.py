"""Relationship label sheet and precision (evaluation only; never imported by research).

    uv run python scripts/relationship_labels.py export <graphs.json> [--out tests/evaluation/geo31]
    uv run python scripts/relationship_labels.py fill <proposals.json> [--out ...]
    uv run python scripts/relationship_labels.py metrics <graphs.json> [--out ...] [--report f.md]

One row per relationship the graphs contain (any status), with up to two evidence quotes and
URLs. The sheet hides the system's status, basis and reasons (key file only). A person fills
confirmed_label: CORRECT (the relationship holds, currently or with unknown time), OUTDATED
(held only in the past), INCORRECT (does not hold, or an entity is not a real organisation /
source), UNCERTAIN; sets label_status CONFIRMED with name and date. Precision = CORRECT /
decided rows (UNCERTAIN excluded), per kind and status; validated (confirmed rows) and
provisional (drafts) apart.
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

LABELS = ("CORRECT", "OUTDATED", "INCORRECT", "UNCERTAIN")
COLUMNS = ["sample_id", "city", "kind", "subject", "object", "quote_1", "url_1", "quote_2",
           "url_2", "proposed_label", "proposed_basis", "label_status", "confirmed_label",
           "reviewer_notes", "reviewer", "reviewed_on"]
DEFAULT_OUT = Path("tests/evaluation/geo31")

def _rows(graphs: dict):
    for run_id, x in graphs.items():
        for r in x["graph"]["relationships"]:
            yield run_id, x["city"], r

def sample_id(run_id: str, key: str) -> str:
    return "R" + hashlib.sha1(f"{run_id}|{key}".encode()).hexdigest()[:8]

def export(graphs: dict, out: Path) -> int:
    sheet, key_path = out / "relationship_labels.csv", out / "relationship_labels_key.json"
    old, by_content = {}, {}
    if sheet.exists():  # keep labels already written (also when a relationship's key changed)
        for r in csv.DictReader(sheet.open(encoding="utf-8")):
            old[r["sample_id"]] = r
            by_content[(r["city"], r["kind"], r["subject"], r["object"])] = r
    rows, key = [], {}
    for run_id, city, r in _rows(graphs):
        sid = sample_id(run_id, r["key"])
        ev = r["evidence"]
        previous = old.get(sid) or by_content.get((city, r["kind"], r["subject"]["name"],
                                                   r["object"]["name"]), {})
        row = {c: "" for c in COLUMNS} | {k: v for k, v in previous.items()
                                          if k not in ("sample_id",)}
        row.update(sample_id=sid, city=city, kind=r["kind"], subject=r["subject"]["name"],
                   object=r["object"]["name"],
                   quote_1=ev[0]["quote"][:400] if ev else "", url_1=ev[0]["url"] if ev else "",
                   quote_2=ev[1]["quote"][:400] if len(ev) > 1 else "",
                   url_2=ev[1]["url"] if len(ev) > 1 else "")
        row["label_status"] = row.get("label_status") or "UNCONFIRMED"
        rows.append(row)
        key[sid] = {"run_id": run_id, "key": r["key"], "kind": r["kind"],
                    "status": r["status"], "basis": r["basis"]}
    rows.sort(key=lambda r: (r["city"], r["kind"], r["subject"]))
    out.mkdir(parents=True, exist_ok=True)
    with sheet.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    key_path.write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
    return len(rows)

def fill(proposals: dict, out: Path) -> int:
    sheet = out / "relationship_labels.csv"
    rows = list(csv.DictReader(sheet.open(encoding="utf-8")))
    n = 0
    for r in rows:
        p = proposals.get(r["sample_id"])
        if p:
            if p["label"] not in LABELS:
                raise ValueError(f"{r['sample_id']}: {p['label']!r} not in {LABELS}")
            r["proposed_label"], r["proposed_basis"] = p["label"], "DRAFT: " + p.get("basis", "")
            n += 1
    with sheet.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return n

def metrics(out: Path) -> dict:
    rows = list(csv.DictReader((out / "relationship_labels.csv").open(encoding="utf-8")))
    key = json.loads((out / "relationship_labels_key.json").read_text(encoding="utf-8"))
    groups: dict = defaultdict(Counter)
    for r in rows:
        k = key.get(r["sample_id"])
        if k is None:
            continue
        confirmed = r["label_status"].strip().upper() == "CONFIRMED"
        label = (r["confirmed_label"] if confirmed else r["proposed_label"]).strip().upper()
        status = "validated" if confirmed else "provisional"
        for g in ((k["kind"], k["status"], status), (k["kind"], "ALL", status),
                  ("ALL", k["status"], status), ("ALL", "ALL", status)):
            groups[g][label or "UNLABELLED"] += 1
            # OUTDATED is right when the graph calls the relationship HISTORICAL
            if label == "CORRECT" or (label == "OUTDATED" and k["status"] == "HISTORICAL"):
                groups[g]["_right"] += 1
    out_m = {}
    for (kind, st, status), c in sorted(groups.items()):
        decided = c["CORRECT"] + c["OUTDATED"] + c["INCORRECT"]
        right = c["_right"]
        out_m[f"{kind}|{st}|{status}"] = {
            **{k: v for k, v in c.items() if not k.startswith("_")}, "decided": decided,
            "right": right,
            "precision": round(right / decided, 3) if decided else None}
    return out_m

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("graphs", type=Path)
    f = sub.add_parser("fill")
    f.add_argument("proposals", type=Path)
    m = sub.add_parser("metrics")
    m.add_argument("--report", type=Path, default=None)
    for p in sub.choices.values():
        p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "export":
        print(export(json.loads(args.graphs.read_text(encoding="utf-8")), args.out), "rows")
    elif args.cmd == "fill":
        print(fill(json.loads(args.proposals.read_text(encoding="utf-8")), args.out),
              "rows proposed (UNCONFIRMED)")
    else:
        result = metrics(args.out)
        lines = ["| kind | system status | labels | right / decided | precision |",
                 "|---|---|---|---|---|"]
        for k, v in result.items():
            kind, st, status = k.split("|")
            counts = ", ".join(f"{x} {v[x]}" for x in (*LABELS, "UNLABELLED") if v.get(x))
            lines.append(f"| {kind} | {st} ({status}) | {counts} | "
                         f"{v['right']}/{v['decided']} | {v['precision']} |")
        md = "\n".join(lines) + "\n"
        if args.report:
            args.report.write_text(md, encoding="utf-8")
        print(md)
    return 0

if __name__ == "__main__":
    sys.exit(main())
