"""Before/after comparison: original runs (baseline) vs their saved-document replays.

    uv run python scripts/replay_report.py <baseline_run_id> [...] [--out reports/eval/replay]

For each baseline run, the latest REPLAY run of it is compared. Both are evaluated now with the
same (current, corrected) evaluation policy and the semantic judge, so differences come from
the research pipeline, not the metrics. Per city:
  - source precision / recall (RAW = any extracted candidate, PROPOSED, AUTO), matched,
    missing and extra sources, kinds named instead of natural sources, out-of-area evidence;
  - evidence coverage: workbook sources named in the acquired text, and in the passages shown
    to the model for water_source (same documents in both: the second one measures retrieval);
  - LLM usage of extraction (requests, prompt / completion tokens; cached calls are free) and
    re-extractions per round;
  - attribution: retrieval (named-source evidence shown to the model), later-round
    re-extraction (sources first proposed in a round > 1), entity handling (kinds/relations
    moving reservoirs, regions or categories out of the accepted sources).
The workbook is only read here, by the evaluation; nothing is written to research tables.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from water_source_research.core.config import get_settings
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.db.session import get_engine
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.judge import build_judge
from water_source_research.evaluation.store import EvaluationStore
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.services.evidence_store import EvidenceRepository

GENERIC = {"river", "rivers", "lake", "lakes", "spring", "springs", "karst", "aquifer",
           "aquifers", "wells", "well", "groundwater", "reservoir", "the", "of", "and", "&",
           "water", "system"}

def significant(name: str) -> list[str]:
    return [w for w in re.findall(r"[\w'’]+", name.casefold()) if w not in GENERIC]

def named(source: str, text: str) -> bool:
    words = significant(source)
    t = text.casefold()
    return bool(words) and all(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t) for w in words)

async def seen_from_checkpoint(run_id: str) -> list[list[str]] | None:
    async with open_checkpointer(get_settings()) as saver:
        tup = await saver.aget_tuple({"configurable": {"thread_id": run_id}})
    if tup is None:
        return None
    return tup.checkpoint.get("channel_values", {}).get("seen_evidence")

def usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    ext = [e for e in events if e.get("kind") == "NODE" and e.get("node") == "extract"]
    attrs = [e.get("attrs") or {} for e in ext]
    re_rounds = Counter((e.get("attrs") or {}).get("discovery_round")
                        for e in ext if str(e.get("strategy", "")).startswith("reextract:"))
    return {"extractions": len(ext),
            "llm_requests": sum(a.get("llm_requests") or 0 for a in attrs),
            "cache_hits": sum(a.get("cache_hits") or 0 for a in attrs),
            "prompt_tokens": sum(a.get("prompt_tokens") or 0 for a in attrs),
            "completion_tokens": sum(a.get("completion_tokens") or 0 for a in attrs),
            "reextractions_by_round": dict(sorted(re_rounds.items()))}

def source_view(store: EvaluationStore, eval_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for m in store.metrics(eval_id):
        if m["scope"] != "source":
            continue
        e = out.setdefault(m["scope_key"], {**(m["detail"] or {})})
        e[m["metric"]] = (m["numerator"], m["denominator"])
    return out

def city_report(engine, store, judge, baseline: str, replay: str) -> dict[str, Any]:
    with Session(engine) as s:
        b_job, r_job = s.get(ResearchJobRecord, baseline), s.get(ResearchJobRecord, replay)
        b_result, r_result = dict(b_job.result or {}), dict(r_job.result or {})
        city, country = b_job.city_name, b_job.country
    evals = {"baseline": run_evaluation(engine, baseline, judge=judge),
             "replay": run_evaluation(engine, replay, judge=judge)}
    ds = store.load_dataset(store.run(evals["baseline"])["dataset_id"])
    workbook = [r.water_source for r in ds.city(city, country).records if r.water_source]
    evidence = EvidenceRepository(engine)
    seen = {"baseline": run_async(seen_from_checkpoint(baseline)) or b_result.get(
        "seen_evidence") or [], "replay": r_result.get("seen_evidence") or []}
    out: dict[str, Any] = {"city": city, "baseline_run": baseline, "replay_run": replay,
                           "evaluations": evals, "workbook_sources": workbook}
    for label, run_id, result in (("baseline", baseline, b_result),
                                  ("replay", replay, r_result)):
        chunks = {c["chunk_id"]: c["text"] for c in evidence.chunks(run_id, limit=1_000_000)}
        shown = {c for c, f in seen[label] if f == "water_source"}
        acquired = [w for w in workbook if any(named(w, t) for t in chunks.values())]
        to_model = [w for w in workbook if any(named(w, chunks.get(c, "")) for c in shown)]
        claims = [c for c in evidence.claims(run_id) if c["field_key"] == "water_source"]
        later = sorted({str(c.get("normalized_value") or c["value"]) for c in claims
                        if ((c.get("qualifiers") or {}).get("extraction_round") or 1) > 1})
        out[label] = {"sources": source_view(store, evals[label]),
                      "evidence": {"acquired": acquired, "shown_to_model": to_model,
                                   "chunks_shown_for_water_source": len(shown)},
                      "usage": usage(result.get("events") or []),
                      "later_round_sources": later,
                      "water_source_candidates": sorted({str(c.get("normalized_value")
                                                             or c["value"]) for c in claims})}
    return out

def frac(t: tuple | None) -> str:
    if not t or not t[1]:
        return "n/a"
    return f"{t[0]:g}/{t[1]:g} ({t[0] / t[1]:.0%})"

def row(label: str, before: Any, after: Any) -> str:
    return f"| {label} | {before} | {after} |"

def names(items: list[str] | None) -> str:
    return ", ".join(items or []) or "—"

def markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Saved-document replay: before / after", "",
        ("Baseline = the original run, re-scored now with the current (corrected) "
         "evaluation. Replay = the same saved documents, introduced in their original "
         "acquisition rounds, through the current retrieval, re-extraction and source-entity "
         "code. **No new discovery or fetching**: gains here are from the pipeline on "
         "identical evidence; gains from better discovery can only come from fresh runs."), "",
        ("Judge-made source pairs are provisional: the semantic judge is not validated yet."),
        ""]
    for r in rows:
        b, p = r["baseline"], r["replay"]
        w = len(r["workbook_sources"])
        bs, ps = b["sources"].get("RAW", {}), p["sources"].get("RAW", {})
        lines += [f"## {r['city']}", "",
                  f"Workbook sources ({w}): {names(r['workbook_sources'])}", "",
                  "| | Baseline | Replay |", "|---|---|---|"]
        for stage in ("RAW", "PROPOSED", "AUTO"):
            sb, sp = b["sources"].get(stage, {}), p["sources"].get(stage, {})
            lines.append(row(f"{stage} source recall", frac(sb.get("source_recall")),
                             frac(sp.get("source_recall"))))
            lines.append(row(f"{stage} source precision", frac(sb.get("source_precision")),
                             frac(sp.get("source_precision"))))
        bu, pu = b["usage"], p["usage"]
        lines += [
            row("Missing (RAW)", names(bs.get("missing")), names(ps.get("missing"))),
            row("Extra (RAW)", names(bs.get("extra")), names(ps.get("extra"))),
            row("Reservoir/region/category named as a source (RAW)",
                bs.get("non_source_kind", "n/a"), ps.get("non_source_kind", "n/a")),
            row("Out-of-area sources (RAW)", bs.get("out_of_area", "n/a"),
                ps.get("out_of_area", "n/a")),
            row("Judge-made (provisional) matches (RAW)", bs.get("provisional", 0),
                ps.get("provisional", 0)),
            row("Workbook sources in acquired text", f"{len(b['evidence']['acquired'])}/{w}",
                f"{len(p['evidence']['acquired'])}/{w}"),
            row("Workbook sources in passages shown for water_source",
                f"{len(b['evidence']['shown_to_model'])}/{w}",
                f"{len(p['evidence']['shown_to_model'])}/{w}"),
            row("Extraction LLM requests (cache hits)",
                f"{bu['llm_requests']} ({bu['cache_hits']})",
                f"{pu['llm_requests']} ({pu['cache_hits']})"),
            row("Prompt / completion tokens",
                f"{bu['prompt_tokens']:,} / {bu['completion_tokens']:,}",
                f"{pu['prompt_tokens']:,} / {pu['completion_tokens']:,}"),
            row("Re-extractions by round", "—", pu["reextractions_by_round"] or "none"),
            row("water_source candidates", names(b["water_source_candidates"]),
                names(p["water_source_candidates"])),
            "", "**Where the change came from**", ""]
        shown_b = set(b["evidence"]["shown_to_model"])
        shown_p = set(p["evidence"]["shown_to_model"])
        matched_b = {m["reference"] for m in bs.get("matched") or []}
        gained = sorted({m["reference"] for m in ps.get("matched") or []} - matched_b)
        later = set(p["later_round_sources"])
        from_later = sum(1 for m in ps.get("matched") or []
                         if m["reference"] in gained and m["predicted"] in later)
        pb = frac(b["sources"].get("PROPOSED", {}).get("source_precision"))
        pp = frac(p["sources"].get("PROPOSED", {}).get("source_precision"))
        lines += [
            (f"- Retrieval: workbook sources newly shown to the model: "
             f"{names(sorted(shown_p - shown_b))} (no longer shown: "
             f"{names(sorted(shown_b - shown_p))})."),
            (f"- Later-round re-extraction: sources first proposed in a round > 1: "
             f"{names(sorted(later))}; {from_later} of the newly matched workbook sources "
             f"({names(gained)}) came from a later round."),
            (f"- Entity handling: reservoirs/regions/categories proposed as sources "
             f"{bs.get('non_source_kind', 'n/a')} -> {ps.get('non_source_kind', 'n/a')}; "
             f"PROPOSED source precision {pb} -> {pp}."), ""]
    return "\n".join(lines)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baselines", nargs="+")
    parser.add_argument("--out", type=Path, default=Path("reports/eval/replay"))
    args = parser.parse_args()
    engine = get_engine()
    store = EvaluationStore(engine)
    judge = build_judge(engine)
    rows = []
    with Session(engine) as s:
        for baseline in args.baselines:
            replay = s.scalars(select(ResearchJobRecord.run_id).where(
                ResearchJobRecord.mode == "REPLAY",
                ResearchJobRecord.parent_run_id == baseline)
                .order_by(ResearchJobRecord.created_at.desc())).first()
            if replay is None:
                print(f"no replay of {baseline}: run scripts/replay_run.py first")
                return 2
            rows.append(city_report(engine, store, judge, baseline, replay))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "replay_report.json").write_text(json.dumps(rows, indent=1, default=str),
                                                 encoding="utf-8")
    text = markdown(rows)
    (args.out / "replay_report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0

if __name__ == "__main__":
    sys.exit(main())
