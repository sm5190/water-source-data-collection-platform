"""Before/after report on automatic acceptance (Milestone 1), from two revalidation sets.

    uv run python scripts/acceptance_report.py <before.json> <after.json> [--out dir]

Each JSON lists {"cities": [{"city", "parent", "run", "eval"}]}: the revalidation run of each
parent and its evaluation (scripts/revalidate_run.py, then evaluate_run.py --judge-cache-only).
Reads only (research and evaluation tables); writes acceptance_report.md and .json:
per city and per field AUTO precision (strict, lenient) and coverage with n/d, every cell whose
AUTO outcome changed, the previously correct values that were lost with the rule that sent
them to review, the positive controls (correct values still accepted), wrong values that
remain, and every automatic decision that moved between accepted and review (also for cities
without a reference).
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from water_source_research.db.session import get_engine
from water_source_research.evaluation.compare import NOT_SCORED
from water_source_research.evaluation.metrics import simple_scores
from water_source_research.evaluation.report import effective
from water_source_research.evaluation.store import EvaluationStore
from water_source_research.schemas.standardized import HEADERS
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.revalidate import automatic_decisions

# Why a value now goes to a person: the rule, and the Standardization Rules text behind it.
RULES = {
    "DESIGNATED_SOURCE_MISSING": "City Population (col. 6-7) is 'based on a list provided in "
        "the Appendix'; no designated population dataset is configured, so a person checks it.",
    "DESIGNATED_SOURCE": "City Population (col. 6-7) comes from the designated dataset.",
    "JURISDICTION_LEVEL": "Col. 2 is 'the state the city is located' in; a lower-level unit "
        "(per the country's hierarchy) is not.",
    "JURISDICTION_LEVEL_UNKNOWN": "Col. 2: the country's administrative hierarchy isn't "
        "configured, so the level can't be established.",
    "CITY_NAMED": "The value must be the city's: the evidence has to name the city.",
    "CITY_NOT_NAMED": "The value must be the city's: the evidence doesn't name the city.",
    "WRONG_PLACE": "The evidence is about another place.",
    "MEASUREMENT_TYPE": "Col. 24/26 are 'water withdrawals'; the figure must be stated as water "
        "withdrawn.",
    "MEASUREMENT_TYPE_MISMATCH": "Col. 24/26 are withdrawals; production, treatment, delivery, "
        "consumption or capacity figures are not.",
    "MEASUREMENT_TYPE_UNKNOWN": "Col. 24/26 are withdrawals; the evidence doesn't say what the "
        "figure measures.",
    "MEASUREMENT_TERM": "Col. 6/8: the figure must be stated as a population.",
    "SERVICE_AREA_POPULATION": "Col. 6 vs 8: a utility's service-area population is recorded "
        "separately from the city population.",
    "MEASUREMENT_YEAR": "Col. 7/9/25/27: the year the measurement refers to; a publication date "
        "doesn't establish it.",
    "FRESHNESS": "Col. 25/27/31: 'Report the most current ... data'.",
    "FRESHNESS_UNKNOWN": "Col. 25/27/31: 'Report the most current ... data'; how current the "
        "value is can't be established.",
    "POSSIBLY_SUPERSEDED": "Col. 25/27/31: 'Report the most current ... data'; the value is "
        "older than the latest expected reporting year.",
    "YEAR_LINKED": "Col. 7/9/25/27/31: the year must be the measurement's, tied to it in the "
        "evidence.",
    "YEAR_NOT_LINKED": "Col. 7/9/25/27/31: the year isn't tied to the measurement (a page or "
        "publication date never is).",
    "PARENT_NOT_ACCEPTED": "A year belongs to its measurement, which isn't accepted.",
    "YEAR_DIFFERS_FROM_PARENT": "A year belongs to its measurement, which has another year.",
    "UTILITY_SERVES_CITY": "Col. 12 is the utility 'that operates the water supply system for "
        "the city'; the evidence must show it serves the city.",
    "UTILITY_SERVICE_NOT_STATED": "Col. 12: no evidence in the run states the utility serves "
        "the city (an official site or the UTILITY label alone isn't enough).",
    "SIBLING_UNSETTLED": "Col. 12: 'one consistent utility name across all rows'; another "
        "row's utility is unsettled.",
    "NO_OFFICIAL_SOURCE": "Not cited from a source type authoritative for the field (after "
        "re-checking stored source types under current rules).",
}
LOST = "lost_correct"

def base_code(reason: str) -> str:
    for prefix in ("WARN_", "NOT_ESTABLISHED_"):
        if reason.startswith(prefix):
            return reason[len(prefix):]
    return reason

def explain(reasons: list[str]) -> list[str]:
    seen, out = set(), []
    for r in reasons:
        code = base_code(r)
        if code in RULES and code not in seen:
            seen.add(code)
            out.append(f"{code}: {RULES[code]}")
    return out

def answered(status: str) -> bool:
    return status not in NOT_SCORED and status != "MISSING_PREDICTION"

def auto_rows(store: EvaluationStore, eval_id: str) -> tuple[dict, dict]:
    view = effective(store, eval_id)
    rows = {(r["field_key"], r["record_key"]): r for r in view["results"]
            if r["stage"] == "AUTO"}
    return rows, view["run"]

def value_of(r: dict[str, Any] | None) -> Any:
    if r is None:
        return None
    p = r.get("predicted") or {}
    return p.get("value") if isinstance(p, dict) else p

def reference_of(r: dict[str, Any] | None) -> Any:
    if r is None:
        return None
    ref = r.get("reference") or {}
    return ref.get("raw") if isinstance(ref, dict) else ref

def field_reasons(decisions: dict, field_key: str) -> list[str]:
    return sorted({x for (f, _), d in decisions.items() if f == field_key
                   for x in d.get("reasons") or []})

def reclassified(engine, run_id: str) -> list[dict[str, Any]]:
    """Stored documents whose source type changed under the current rules (revalidation)."""
    from sqlalchemy.orm import Session
    from water_source_research.db.research_models import ResearchJobRecord
    with Session(engine) as session:
        job = session.get(ResearchJobRecord, run_id)
        result = (job.result or {}) if job else {}
    return list((result.get("revalidation") or {}).get("reclassified") or [])

def compare_city(store, evidence, before: dict, after: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"city": before["city"], "parent": before["parent"],
                           "before_run": before["run"], "after_run": after["run"]}
    dec_b = automatic_decisions(evidence.resolutions(before["run"]))
    dec_a = automatic_decisions(evidence.resolutions(after["run"]))
    moves = []
    for key in sorted(set(dec_b) | set(dec_a)):
        b, a = dec_b.get(key) or {}, dec_a.get(key) or {}
        rb, ra = b.get("status") == "RESOLVED", a.get("status") == "RESOLVED"
        if rb != ra:
            moves.append({"field_key": key[0], "entity_key": key[1],
                          "before": b.get("status"), "after": a.get("status"),
                          "direction": "accepted->review" if rb else "review->accepted",
                          "reasons": a.get("reasons") or [],
                          "explanation": explain(a.get("reasons") or [])})
    out["decision_moves"] = moves
    out["reclassified"] = reclassified(store.engine, after["run"])
    if not before.get("eval") or not after.get("eval"):
        out["evaluated"] = False
        return out
    rows_b, run_b = auto_rows(store, before["eval"])
    rows_a, run_a = auto_rows(store, after["eval"])
    out["evaluated"] = True
    out["evaluations"] = {"before": before["eval"], "after": after["eval"],
                          "snapshot_before": run_b["snapshot_sha256"],
                          "snapshot_after": run_a["snapshot_sha256"],
                          "dataset": run_a["dataset_id"],
                          "compare_policy": [run_b["compare_policy_version"],
                                             run_a["compare_policy_version"]],
                          "judge_before": (run_b["config"] or {}).get("judge"),
                          "judge_after": (run_a["config"] or {}).get("judge")}
    out["scores"] = {"before": simple_scores(list(rows_b.values())),
                     "after": simple_scores(list(rows_a.values()))}
    cells = []
    for key in sorted(set(rows_b) | set(rows_a)):
        b, a = rows_b.get(key), rows_a.get(key)
        sb, sa = (b or {}).get("status", "ABSENT"), (a or {}).get("status", "ABSENT")
        kind = None
        if answered(sb) and not answered(sa):
            kind = LOST if sb == "MATCH" else ("lost_partial" if sb == "PARTIAL_MATCH"
                                               else "removed_wrong")
        elif answered(sb) and answered(sa):
            kind = "still_correct" if sa == "MATCH" else (
                "still_partial" if sa == "PARTIAL_MATCH" else "still_wrong")
            if sb != sa:
                kind = "changed_" + kind
        elif answered(sa):
            kind = "new_correct" if sa == "MATCH" else "new_answer"
        if kind is None:
            continue
        reasons = field_reasons(dec_a, key[0]) if kind in (
            LOST, "lost_partial", "removed_wrong") else []
        cells.append({"field_key": key[0], "record_key": key[1], "kind": kind,
                      "before_status": sb, "after_status": sa,
                      "value": value_of(b) if value_of(b) is not None else value_of(a),
                      "reference": reference_of(b) or reference_of(a),
                      "reasons": reasons, "explanation": explain(reasons)})
    out["cells"] = cells
    out["rows_after"] = list(rows_a.values())
    out["rows_before"] = list(rows_b.values())
    return out

def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"

def score_line(s: dict[str, Any]) -> str:
    a, c, m, p = s["answered"], s["comparable"], s["match"], s["partial"]
    return (f"{a}/{c} ({pct(s['coverage'])}) | {m}/{a} ({pct(s['accuracy_strict'])}) | "
            f"{m + 0.5 * p:g}/{a} ({pct(s['accuracy_lenient'])})")

def render(report: list[dict[str, Any]], by_field: dict[str, dict]) -> str:
    out = ["# Automatic acceptance: before / after Milestone 1", "",
           ("Before = today's rules before the change (normalize-3 / resolve-3); after = "
            "normalize-4 / resolve-4. Both are revalidations of the same stored runs (claims as "
            "extracted, the same evidence; no discovery, fetching or model calls) evaluated "
            "against the same reference dataset and comparison policy with the semantic judge "
            "in cache-only mode (no new model calls; judged cells stay provisional). AUTO = the "
            "values the system accepted on its own, before any person."), "",
           "## Per city", "",
           ("| City | Coverage before | Coverage after | Strict precision before | "
            "Strict after | Lenient before | Lenient after |"), "|---|---|---|---|---|---|---|"]
    for c in report:
        if not c.get("evaluated"):
            continue
        b, a = c["scores"]["before"], c["scores"]["after"]
        out.append(f"| {c['city']} | {b['answered']}/{b['comparable']} | "
                   f"{a['answered']}/{a['comparable']} | {b['match']}/{b['answered']} "
                   f"({pct(b['accuracy_strict'])}) | {a['match']}/{a['answered']} "
                   f"({pct(a['accuracy_strict'])}) | {pct(b['accuracy_lenient'])} | "
                   f"{pct(a['accuracy_lenient'])} |")
    pooled_b = [r for c in report if c.get("evaluated") for r in c["rows_before"]]
    pooled_a = [r for c in report if c.get("evaluated") for r in c["rows_after"]]
    sb, sa = simple_scores(pooled_b), simple_scores(pooled_a)
    out += [(f"| **All evaluated cities** | {sb['answered']}/{sb['comparable']} | "
             f"{sa['answered']}/{sa['comparable']} | {sb['match']}/{sb['answered']} "
             f"({pct(sb['accuracy_strict'])}) | {sa['match']}/{sa['answered']} "
             f"({pct(sa['accuracy_strict'])}) | {pct(sb['accuracy_lenient'])} | "
             f"{pct(sa['accuracy_lenient'])} |"), "",
            ("Coverage = AUTO answers / comparable cells; precision = correct / answered "
             "(lenient counts a partial match as half)."), "",
            "## Per field (all evaluated cities)", "",
            ("| Field | Coverage before | Strict before | Lenient before | Coverage after "
             "| Strict after | Lenient after |"), "|---|---|---|---|---|---|---|"]
    for key, s in by_field.items():
        if s["before"]["answered"] or s["after"]["answered"]:
            out.append(f"| {HEADERS.get(key, key)} | {score_line(s['before'])} | "
                       f"{score_line(s['after'])} |")
    out += ["", "Fields with no automatic answer before or after are omitted.", ""]

    def cell_table(title: str, kinds: tuple[str, ...], note: str = "") -> None:
        rows = [(c["city"], x) for c in report if c.get("evaluated") for x in c["cells"]
                if x["kind"] in kinds]
        out.extend([f"## {title} ({len(rows)})", ""] + ([note, ""] if note else []))
        if not rows:
            out.extend(["None.", ""])
            return
        out.extend(["| City | Field | Record | Value | Reference | Before → after | Why |",
                    "|---|---|---|---|---|---|---|"])
        for city, x in rows:
            why = "<br>".join(x["explanation"]) or ", ".join(x["reasons"]) or "—"
            out.append(f"| {city} | {HEADERS.get(x['field_key'], x['field_key'])} | "
                       f"{x['record_key'] or '—'} | {_short(x['value'])} | "
                       f"{_short(x['reference'])} | {x['before_status']} → {x['after_status']} "
                       f"| {why} |")
        out.append("")

    cell_table("Previously correct automatic values that were lost", (LOST, "lost_partial"),
               "Each needs a rule from the Standardization Rules; one without is a bug.")
    cell_table("Wrong automatic values no longer accepted", ("removed_wrong",))
    cell_table("Positive controls: correct values still accepted",
               ("still_correct", "changed_still_correct"))
    cell_table("Wrong or partial values still accepted",
               ("still_wrong", "still_partial", "changed_still_wrong", "changed_still_partial"),
               "These pass every rule; the reason they still pass is in the evidence shown "
               "in the evaluation's field view.")
    cell_table("Newly accepted", ("new_correct", "new_answer"))
    moves = [(c["city"], m) for c in report for m in c["decision_moves"]]
    out += [f"## Automatic decisions that moved ({len(moves)})", "",
            ("Per field and entity in the revalidation runs (also for cities without a "
             "reference)."), "", "| City | Field | Entity | Move | Reasons |",
            "|---|---|---|---|---|"]
    for city, m in moves:
        out.append(f"| {city} | {HEADERS.get(m['field_key'], m['field_key'])} | "
                   f"{m['entity_key']} | {m['direction']} | {', '.join(m['reasons']) or '—'} |")
    changed = [(c["city"], x) for c in report for x in c.get("reclassified") or []]
    out += ["", (f"## Stored documents re-checked under current source-type rules "
                 f"({len(changed)})"), "",
            ("The original runs keep their stored labels; only the revalidation copies change. "
             "A value whose only official-looking source was relabelled goes to review "
             "(NO_OFFICIAL_SOURCE)."), "", "| City | From → to | Document |", "|---|---|---|"]
    out += [f"| {city} | {x['from']} → {x['to']} | {x['url']} |" for city, x in changed]
    out += ["", "## Reproducibility", ""]
    for c in report:
        if c.get("evaluated"):
            e = c["evaluations"]
            jb, ja = e["judge_before"] or {}, e["judge_after"] or {}
            out.append(f"- {c['city']}: evaluations {e['before'][:8]} → {e['after'][:8]}, "
                       f"dataset {e['dataset'][:8]}, compare {'/'.join(e['compare_policy'])}, "
                       f"judge new calls {jb.get('new_calls')} / {ja.get('new_calls')}, cache "
                       f"misses {jb.get('cache_misses')} / {ja.get('cache_misses')}")
        else:
            out.append(f"- {c['city']}: no reference in the dataset (decision moves only)")
    return "\n".join(out) + "\n"

def _short(v: Any, n: int = 60) -> str:
    s = "—" if v in (None, "") else str(v)
    return (s[:n] + "…" if len(s) > n else s).replace("|", "/").replace("\n", " ")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path, default=Path("reports/eval/acceptance"))
    args = parser.parse_args()
    before = {c["city"]: c for c in json.loads(args.before.read_text("utf-8"))["cities"]}
    after = {c["city"]: c for c in json.loads(args.after.read_text("utf-8"))["cities"]}
    engine = get_engine()
    store, evidence = EvaluationStore(engine), EvidenceRepository(engine)
    report = [compare_city(store, evidence, before[city], after[city])
              for city in before if city in after]
    fields: dict[str, dict[str, list]] = defaultdict(lambda: {"before": [], "after": []})
    for c in report:
        for side in ("before", "after"):
            for r in c.get(f"rows_{side}", []):
                fields[r["field_key"]][side].append(r)
    by_field = {k: {s: simple_scores(v[s]) for s in ("before", "after")}
                for k, v in sorted(fields.items(), key=lambda kv: list(HEADERS).index(kv[0])
                                   if kv[0] in HEADERS else 99)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "acceptance_report.md").write_text(render(report, by_field), encoding="utf-8")
    slim = [{k: v for k, v in c.items() if not k.startswith("rows_")} for c in report]
    (args.out / "acceptance_report.json").write_text(
        json.dumps({"cities": slim, "by_field": by_field}, indent=1, default=str),
        encoding="utf-8")
    print(f"wrote {args.out / 'acceptance_report.md'}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
