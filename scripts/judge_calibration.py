"""Check the semantic judge against manually labelled cases before trusting it.

    uv run python scripts/judge_calibration.py tests/evaluation/judge_cases_toronto.yaml
        [--out reports/eval/judge] [--no-cache]

Calls the REAL configured LLM provider (VT: needs the VT VPN; never falls back to OpenAI).
Judgments are stored in eval_judgments like any other (so a repeat run is free unless
--no-cache). Prints, per case, the expected and judged status, and a confusion table.

Exit status: 0 = VALIDATED (every label confirmed by a person, agreement >= min_agreement and
no false equivalence); 1 = NOT VALIDATED (labels unconfirmed or criteria not met); 2 = the
judge was unavailable. Only a VALIDATED result supports saying semantic matching was checked;
it is still a check on these cases, not a guarantee.
"""

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
import yaml
from water_source_research.db.session import get_engine
from water_source_research.evaluation.judge import (
    JUDGE_PROMPT_VERSION, JudgmentStore, build_judge, eligible,
)

STATUSES = ("MATCH", "PARTIAL_MATCH", "MISMATCH", "NEEDS_REVIEW")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--no-cache", action="store_true",
                        help="ask the model again even if this exact question was judged")
    args = parser.parse_args()
    data = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    engine = get_engine()
    judge = build_judge(engine)
    if judge.llm is None:
        print(f"judge unavailable: {judge.unavailable}")
        return 2
    if args.no_cache:
        judge.store = _NoCache(engine)

    rows = []
    for c in data["cases"]:
        x = judge.make_input(c["field"], str(c["manual"]), c.get("manual_year"),
                             str(c["automated"]), c.get("automated_year"),
                             [c["quote"]] if c.get("quote") else [])
        j = judge.judge(x)
        got = j["status"] if j else "ERROR"
        rows.append({"id": c["id"], "kind": c["kind"], "field": c["field"],
                     "expected": c["expected"], "judged": got,
                     "agree": got == c["expected"], "confirmed": bool(c.get("confirmed")),
                     "verdict": j["output"]["verdict"] if j else "",
                     "justification": j["output"]["justification"] if j else
                     (judge.errors[-1] if judge.errors else "")})
    leaks = [n["id"] for n in data.get("never_judged", []) if eligible({
        "field_key": n["field"], "status": n["status"],
        "comparison": {"tags": n.get("tags", [])}})]

    confirmed = [r for r in rows if r["confirmed"]]
    scored = confirmed or rows
    agreement = sum(r["agree"] for r in scored) / len(scored) if scored else 0.0
    false_eq = [r["id"] for r in scored if r["expected"] == "MISMATCH" and r["judged"] == "MATCH"]
    crit = data.get("pass_criteria", {})
    all_confirmed = len(confirmed) == len(rows) and bool(data.get("labels_confirmed_by"))
    validated = (all_confirmed and not leaks and agreement >= crit.get("min_agreement", 0.9)
                 and len(false_eq) <= crit.get("max_false_equivalence", 0)
                 and not any(r["judged"] == "ERROR" for r in rows))

    lines = [f"# Semantic judge calibration ({datetime.now(UTC).date()})", "",
             (f"- Cases: `{args.cases}` ({len(rows)}; {len(confirmed)} confirmed by a person"
              f"{', by ' + data['labels_confirmed_by'] if data.get('labels_confirmed_by') else ''})"),
             f"- Judge: {judge.provider} / {judge.model}, prompt {JUDGE_PROMPT_VERSION}",
             (f"- Agreement on {'confirmed' if confirmed else 'DRAFT (unconfirmed)'} labels: "
              f"{sum(r['agree'] for r in scored)}/{len(scored)} ({agreement:.0%})"),
             f"- False equivalence (expected MISMATCH, judged MATCH): {false_eq or 'none'}",
             f"- Hard-rule cases sent to the judge (must be none): {leaks or 'none'}",
             f"- Result: **{'VALIDATED' if validated else 'NOT VALIDATED'}**"
             + ("" if all_confirmed else " (labels are not all confirmed by a person)"), "",
             "| case | field | expected | judged | agree | confirmed | justification |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['field']} | {r['expected']} | {r['judged']} | "
              f"{'yes' if r['agree'] else 'NO'} | {'yes' if r['confirmed'] else 'no'} | "
              f"{r['justification'].replace('|', '/')[:160]} |" for r in rows]
    confusion = Counter((r["expected"], r["judged"]) for r in scored)
    lines += ["", "Confusion (rows: expected, columns: judged)", "",
              "| expected \\ judged | " + " | ".join(STATUSES) + " | ERROR |",
              "|---|" + "---|" * (len(STATUSES) + 1)]
    lines += [f"| {e} | " + " | ".join(str(confusion[(e, g)]) for g in (*STATUSES, "ERROR"))
              + " |" for e in STATUSES]
    text = "\n".join(lines)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"judge_calibration_{datetime.now(UTC):%Y%m%d_%H%M}.md"
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0 if validated else 1

class _NoCache(JudgmentStore):
    def get(self, key):  # always ask the model; storing still dedupes by key
        return None

if __name__ == "__main__":
    sys.exit(main())
