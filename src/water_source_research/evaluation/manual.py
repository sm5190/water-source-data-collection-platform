"""Check an evaluation against independently written manual findings (a YAML file).

Each finding states, for one field and stage, the acceptable comparison statuses and,
optionally, the root cause and symptoms a person concluded. Every stated attribute becomes one
row with verdict AGREE, DISAGREE or NOT_EVALUATED (the evaluation has no such cell). Nothing is
forced to agree: disagreements are the output, to be resolved by adjudication or by fixing the
evaluator (never by editing the findings to match).
"""

import json
from pathlib import Path
from typing import Any
import yaml

def load_findings(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise TypeError(f"{path}: expected a mapping with a 'findings' list")
    return data

def _row(f: dict[str, Any], attribute: str, expected: Any, actual: Any, verdict: str,
         detail: str = "") -> dict[str, Any]:
    return {"field": f.get("field", ""), "record": f.get("record", ""),
            "stage": f.get("stage", ""), "attribute": attribute, "expected": expected,
            "evaluator": actual, "verdict": verdict, "note": f.get("note", ""),
            "detail": detail}

def compare_manual(view: dict[str, Any], manual: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows comparing the (adjudicated) evaluation view with the manual findings."""
    out: list[dict[str, Any]] = []
    for f in manual["findings"]:
        stage = f.get("stage", "RAW")
        results = [r for r in view["results"] if r["field_key"] == f["field"]
                   and r["stage"] == stage
                   and (not f.get("record") or r["record_key"] == f["record"])]
        if "status" in f:
            if not results:
                out.append(_row(f, "status", f["status"], None, "NOT_EVALUATED",
                                f"no {stage} result for this field"))
            else:
                actual = sorted({r["status"] for r in results})
                ok = all(a in f["status"] for a in actual)
                out.append(_row(f, "status", f["status"], actual,
                                "AGREE" if ok else "DISAGREE"))
        diagnoses = [d for d in view["diagnoses"] if d["field_key"] == f["field"]
                     and d["stage"] == f.get("diagnosis_stage", stage)
                     and (not f.get("record") or d["record_key"] == f["record"])]
        if "root_cause" in f:
            actual = sorted({d["root_cause"] for d in diagnoses})
            if not diagnoses:
                verdict = "NOT_EVALUATED"
            else:
                verdict = "AGREE" if set(actual) & set(f["root_cause"]) else "DISAGREE"
            detail = "; ".join(f"{d['confidence']}: {d['explanation']}" for d in diagnoses)
            out.append(_row(f, "root_cause", f["root_cause"], actual, verdict, detail))
        if "symptoms" in f:
            actual = sorted({s for d in diagnoses for s in d["symptoms"]})
            missing = [s for s in f["symptoms"] if s not in actual]
            out.append(_row(f, "symptoms", f["symptoms"], actual,
                            "DISAGREE" if missing else "AGREE",
                            f"missing: {', '.join(missing)}" if missing else ""))
    for f in manual.get("run_findings") or []:
        hits = [d for d in view["diagnoses"] if d["stage"] == "RUN"
                and f["symptom"] in d["symptoms"]
                and (not f.get("mentions")
                     or f["mentions"] in json.dumps(d["probes"], default=str))]
        out.append(_row({"field": "(run)", "stage": "RUN", "note": f.get("note", "")},
                        "symptom", f"{f['symptom']} ({f.get('mentions', '')})",
                        len(hits), "AGREE" if hits else "DISAGREE"))
    return out

def manual_markdown(rows: list[dict[str, Any]], title: str) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines = [f"# {title}", "", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())), "",
             "| verdict | field | stage | attribute | manual | evaluator | note / detail |",
             "|---|---|---|---|---|---|---|"]
    order = {"DISAGREE": 0, "NOT_EVALUATED": 1, "AGREE": 2}
    for r in sorted(rows, key=lambda r: order.get(r["verdict"], 3)):
        text = " ".join(x for x in (r["note"], r["detail"]) if x).replace("|", "\\|")
        lines.append(f"| {r['verdict']} | {r['field']} | {r['stage']} | {r['attribute']} | "
                     f"{r['expected']} | {r['evaluator']} | {text[:300]} |")
    return "\n".join(lines) + "\n"
