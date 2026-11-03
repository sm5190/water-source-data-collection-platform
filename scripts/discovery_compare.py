"""Compare fresh-discovery runs (scripts/discovery_experiment.py output; evaluation only).

    uv run python scripts/discovery_compare.py pool [--fresh reports/eval/discovery/fresh]
        # adds every newly found document to the blind label sheet (fresh_pool.csv + key)
    uv run python scripts/discovery_compare.py report [--fresh ...]
        # per run: searches, site: targets on non-serving hosts, documents, other-place
        # documents, steering hosts vs expectations, registry on the run's evidence,
        # acquisition precision and pooled recall (validated vs provisional)

Pooled recall's universe is explicit: the relevant documents (by label) among everything any
arm or repeat of the city acquired, plus the stored runs' labelled documents for the city.
It is relative to that pool, never absolute recall. Labels and expectations are evaluation
data (tests/evaluation/discovery/), never read by research.
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

os.environ.setdefault("CITY_IDENTITY_ALLOW_DRAFT", "1")  # judging geography (evaluation only)

LABELS = Path("tests/evaluation/discovery")

def host(url):
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")

def load_runs(fresh: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(fresh.glob("*.json"))
            if not p.name.endswith("snapshots.jsonl")]

def ok_docs(run):
    return [d for r in run["rounds"] for d in r["documents"] if d.get("status") == "OK"]

def doc_url(d):
    return d.get("final_url") or d.get("requested_url")

def cmd_pool(fresh: Path) -> None:
    from water_source_research.evaluation.discovery_labels import build_sample, write_sheet
    from water_source_research.services.city_identity import load_city_identities
    stored = json.loads((LABELS / "labels_key.json").read_text(encoding="utf-8"))
    known = {(v["city"], v["url"]) for v in stored.values()}
    docs, seen = [], set()
    for run in load_runs(fresh):
        texts = {}
        for c in run["chunks"]:
            texts.setdefault(c["url"], []).append((c["chunk_id"], c["text"]))
        for d in ok_docs(run):
            u = doc_url(d)
            if (run["city"], u) in known or (run["city"], u) in seen or u not in texts:
                continue
            seen.add((run["city"], u))
            docs.append({"run_id": "fresh", "document_id": 0, "city": run["city"],
                         "country": run["country"], "url": u, "title": d.get("title") or "",
                         "round": d.get("round"), "texts": texts[u]})
    sample = build_sample(docs, per_city=10_000)
    for n, d in enumerate(sample, 1):
        d["sample_id"] = f"F{n:03d}"
    ids = {(i.city, i.country): i for i in load_city_identities()}
    write_sheet(sample, ids, LABELS / "fresh_pool.csv", LABELS / "fresh_pool_key.json")
    print(f"{len(sample)} new documents -> {LABELS / 'fresh_pool.csv'}",
          dict(Counter(d["city"] for d in sample)))

def all_labels() -> dict[tuple[str, str], dict]:
    from water_source_research.evaluation.discovery_labels import labels_by_document, read_labels
    out = {}
    for sheet, key in (("labels_sample.csv", "labels_key.json"),
                       ("fresh_pool.csv", "fresh_pool_key.json")):
        if (LABELS / sheet).exists():
            k = json.loads((LABELS / key).read_text(encoding="utf-8"))
            out.update(labels_by_document(read_labels(LABELS / sheet), k))
    return out

def cmd_report(fresh: Path) -> str:
    from water_source_research.evaluation.discovery_metrics import (
        load_expectations, utility_accuracy,
    )
    from water_source_research.schemas.research import EvidenceChunk
    from water_source_research.services.field_state import initialize_city_state
    from water_source_research.services.geo_match import matcher_for
    from water_source_research.services.utility_registry import build_registry
    labels = all_labels()
    expectations = load_expectations(LABELS / "utility_expectations.yaml")
    runs = load_runs(fresh)
    pools: dict[str, set] = {}
    for (city, url), lab in labels.items():
        if lab["relevant"] == "YES":
            pools.setdefault(city, set()).add(url)
    lines = ["# Fresh discovery: baseline (e0c8320) vs improved (Milestone 3)", "",
             ("Discovery-only runs (real discover + acquire nodes, every field open each round, "
             "no extraction or model calls), A-B-B-A per city in one session, same providers "
             "and budgets. The improved arm used the drafted city identity (as if approved). "
             "Labels and utility expectations are UNCONFIRMED drafts unless stated, so every "
             "figure below is provisional. Pooled recall = relevant documents found / relevant "
             "documents in the city's pool (everything any run acquired + the stored runs' "
             "labelled documents), not absolute recall."), "",
             ("| run | searches | site: (non-serving host) | docs OK | about elsewhere | "
             "relevant / irrelevant / uncertain / unlabelled | precision | pooled recall | "
             "steering hosts | registry verifies (new rules, this evidence) | replay |"),
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        city, country = run["city"], run["country"]
        exp = expectations.get((city, country)) or {}
        serving_hosts = {h for s in exp.get("serving", []) for h in s.get("hosts", [])}
        rep = run.get("replay")
        replay = ("live" if not rep else
                  ("recorded" if rep["like_for_like"] else
                   f"NOT like-for-like: {rep['counts']['NOT_RECORDED']} not recorded"))
        if rep and rep["counts"].get("LIVE_ON_MISS"):
            replay += f", {rep['counts']['LIVE_ON_MISS']} live on miss"
        searches = [s for r in run["rounds"] for s in r["searches"]
                    if s.get("replay") != "NOT_RECORDED"]  # unknown outcome, not a search
        site = [s["query"].split()[0][5:] for s in searches
                if (s.get("query") or "").startswith("site:")]
        site_bad = [h for h in site if h.removeprefix("www.") not in serving_hosts]
        docs = ok_docs(run)
        m = matcher_for(city, country)
        texts: dict[str, list[str]] = {}
        for c in run["chunks"]:
            texts.setdefault(c["url"], []).append(c["text"])
        elsewhere = 0
        rel = Counter()
        found_relevant = set()
        for d in docs:
            u = doc_url(d)
            if m is not None and m.document_scope(u, d.get("title"), texts.get(u, [])).elsewhere:
                elsewhere += 1
            lab = labels.get((city, u))
            rel[(lab or {}).get("relevant") or "UNLABELLED"] += 1
            if lab and lab["relevant"] == "YES":
                found_relevant.add(u)
        judged = rel["YES"] + rel["NO"]
        if run["arm"] == "baseline":
            steer = Counter(host(doc_url(d)) for d in docs
                            if (d.get("source_type") or "").upper() == "UTILITY")
            steering = sorted(steer, key=lambda h: -steer[h])
        else:
            last = run["rounds"][-1].get("utility_registry") or {}
            steering = [h for c in last.get("candidates", []) if c.get("status") == "VERIFIED"
                        for h in c.get("bound_hosts", [])]
        chunks = [EvidenceChunk(chunk_id=c["chunk_id"], url=c["url"], text=c["text"],
                                title=c.get("title"), source_type=c.get("source_type"))
                  for c in run["chunks"]]
        reg = build_registry(initialize_city_state(city, country), chunks).model_dump(
            mode="json")
        acc = utility_accuracy(reg, exp) if exp else {}
        pool = pools.get(city, set())
        lines.append(
            f"| {run['label']} | {len(searches)} | {len(site)} ({len(site_bad)}) | {len(docs)} "
            f"| {elsewhere} | {rel['YES']} / {rel['NO']} / {rel['UNCERTAIN']} / "
            f"{rel['UNLABELLED']} | {rel['YES']}/{judged if judged else '—'} | "
            f"{len(found_relevant)}/{len(pool)} | {', '.join(steering[:3]) or '—'} | "
            f"correct {acc.get('correct', [])}, wrong {acc.get('wrong', [])} | {replay} |")
    return "\n".join(lines) + "\n"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pool", "report"])
    ap.add_argument("--fresh", type=Path, default=Path("reports/eval/discovery/fresh"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "pool":
        cmd_pool(args.fresh)
    else:
        md = cmd_report(args.fresh)
        (args.fresh / "comparison.md").write_text(md, encoding="utf-8")
        print(md)
    return 0

if __name__ == "__main__":
    sys.exit(main())
