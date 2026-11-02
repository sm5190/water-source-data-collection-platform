"""Controlled fresh-discovery comparison (Milestone 3, step B; live search, no model calls).

    uv run python scripts/discovery_experiment.py run --city Santiago --country Chile \
        --arm improved --repeat 1 [--rounds 3] [--out reports/eval/discovery/fresh]
    PYTHONPATH=<baseline worktree>/src uv run python scripts/discovery_experiment.py run ... \
        --arm baseline ...          # the same script on the pre-Milestone-3 code

One "discovery-only" run = the real discover and acquire nodes of whichever code is imported,
for ``--rounds`` rounds, with every searchable field kept open each round (no extraction, so
both arms are driven identically and no model is called). Every provider response is recorded
to a JSON-lines snapshot with its timestamp (discovery/recorded.py, when the code has it; the
baseline arm's searches are captured from its trusted events and discovery hits). Output: one
JSON per run with the settings snapshot (code commit, version constants, discovery and
acquisition budgets), the searches and documents per round (with their first passages, for
labelling) — nothing is written to the research database.

    uv run python scripts/discovery_experiment.py settings   # the snapshot only (compare arms)
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

def _commit(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return "?"

def settings_snapshot() -> dict:
    import water_source_research as pkg
    from water_source_research.core import versions
    from water_source_research.core.config import get_settings
    s = get_settings()
    pkg_dir = Path(pkg.__file__).resolve().parents[2]
    return {
        "code_path": str(pkg_dir),
        "commit": os.environ.get("EXPERIMENT_CODE_COMMIT") or _commit(pkg_dir),
        "versions": {k: getattr(versions, k) for k in dir(versions) if k.isupper()},
        "retrieval_policy": __import__("water_source_research.context_manager.manager",
                                       fromlist=["x"]).RETRIEVAL_POLICY_VERSION,
        "discovery": s.discovery.model_dump(mode="json", exclude={"record_path",
                                                                   "record_label",
                                                                   "replay_paths"}),
        "acquisition": s.acquisition.model_dump(mode="json"),
        "llm": {"provider": s.llm_provider, "model": getattr(s, "vt_llm_model", None)},
        "identity_draft": os.environ.get("CITY_IDENTITY_ALLOW_DRAFT", ""),
    }

async def run_once(city: str, country: str, rounds: int) -> dict:
    from water_source_research.acquisition.service import build_acquisition_service
    from water_source_research.agents.nodes import make_acquire_node, make_discover_node
    from water_source_research.core.config import get_settings
    from water_source_research.discovery.service import build_discovery_service
    from water_source_research.services.field_state import (
        initialize_city_state, load_field_definitions,
    )
    settings = get_settings()
    fields = load_field_definitions()
    discover = make_discover_node(None, build_discovery_service(settings), fields, None)
    acquire = make_acquire_node(build_acquisition_service(settings, None), None)
    research = initialize_city_state(city, country)
    targets = [k for k, d in fields.items() if not d.derived and not d.generated]
    state: dict = {"run_id": f"exp-{city}", "city_name": city, "country": country,
                   "research_state": research.model_dump(mode="json"), "events": [],
                   "fields_needing_evidence": targets, "evidence_chunks": []}
    per_round = []
    for r in range(1, rounds + 1):
        before_events = len(state.get("events", []))
        before_docs = len(state.get("acquired_documents", []))
        state = await discover(state)
        state = await acquire(state)
        state["fields_needing_evidence"] = targets  # no extraction: every field stays open
        events = state.get("events", [])[before_events:]
        per_round.append({
            "round": r,
            "searches": [{"provider": e.get("provider"), "query": (e.get("attrs") or {})
                          .get("query"), "found": (e.get("attrs") or {}).get("sources_found"),
                          "retained": (e.get("attrs") or {}).get("sources_retained")}
                         for e in events if e.get("kind") == "TOOL_CALL"
                         and e.get("node") == "discover"],
            "documents": state.get("acquired_documents", [])[before_docs:],
            "utility_registry": state.get("utility_registry"),
            "suppressed_hosts": state.get("suppressed_hosts"),
            "other_events": [e for e in events if e.get("kind") in ("UTILITY_STATUS",
                                                                     "HOST_SUPPRESSED")],
        })
    chunks_by_url: dict[str, list[str]] = {}
    for c in state.get("evidence_chunks", []):
        chunks_by_url.setdefault(c["url"], []).append(c["text"])
    return {"rounds": per_round,
            "passages": {u: [t[:1500] for t in ts[:4]] for u, ts in chunks_by_url.items()},
            "chunks": state.get("evidence_chunks", [])}

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--city", required=True)
    r.add_argument("--country", required=True)
    r.add_argument("--arm", choices=["baseline", "improved"], required=True)
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--rounds", type=int, default=3)
    r.add_argument("--out", type=Path, default=Path("reports/eval/discovery/fresh"))
    sub.add_parser("settings")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "settings":
        print(json.dumps(settings_snapshot(), indent=2, default=str))
        return 0
    label = f"{args.city}-{args.arm}-{args.repeat}"
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ["DISCOVERY__RECORD_PATH"] = str((args.out / f"{label}.snapshots.jsonl").resolve())
    os.environ["DISCOVERY__RECORD_LABEL"] = label
    from water_source_research.core.config import get_settings
    get_settings.cache_clear()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    from water_source_research.research_queue.checkpointing import run_async
    result = run_async(run_once(args.city, args.country, args.rounds))
    out = {"label": label, "city": args.city, "country": args.country, "arm": args.arm,
           "repeat": args.repeat, "started_at": started,
           "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "settings": settings_snapshot(), **result}
    (args.out / f"{label}.json").write_text(json.dumps(out, indent=1, default=str),
                                            encoding="utf-8")
    docs = [d for rr in result["rounds"] for d in rr["documents"]]
    print(f"{label}: {sum(len(rr['searches']) for rr in result['rounds'])} searches, "
          f"{len(docs)} documents ({sum(d.get('status') == 'OK' for d in docs)} OK)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
