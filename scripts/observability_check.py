"""Live check: are a run's traces really in Jaeger, and are they complete?

    python scripts/observability_check.py <run_id> [--jaeger http://localhost:16686]
                                                   [--docker-stats]

For every trace id recorded for the run (run_traces: submit, executions, recoveries, review
resumes) it asks Jaeger's query API for the trace and reports span count, services, the span
names, links, and gaps: spans whose parent is missing (expected after a hard kill, whose
unflushed spans are lost) or recorded traces Jaeger doesn't have. It also lists the run's
worker lifecycle events from Postgres (the authoritative crash record) and, with
--docker-stats, the memory the observability containers use. Exit 0 when every sampled
trace was found in Jaeger.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from water_source_research.db.session import get_engine
from water_source_research.observability.tracing import TraceStore
from water_source_research.observability.worker_events import EventStore

def jaeger_trace(base: str, trace_id: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base}/api/traces/{trace_id}", timeout=10) as r:
            data = json.load(r).get("data") or []
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return data[0] if data else None

def summarize(t: dict) -> dict:
    spans = t.get("spans") or []
    ids = {s["spanID"] for s in spans}
    services = {p: v.get("serviceName") for p, v in (t.get("processes") or {}).items()}
    orphans, links = [], []
    for s in spans:
        for ref in s.get("references") or []:
            if ref.get("refType") == "CHILD_OF" and ref.get("traceID") == s["traceID"] \
                    and ref.get("spanID") not in ids:
                orphans.append(s["operationName"])
            if ref.get("traceID") != s["traceID"]:
                links.append(f"{s['operationName']} -> {ref.get('traceID')[:12]}…")
    names: dict[str, int] = {}
    for s in spans:
        names[s["operationName"]] = names.get(s["operationName"], 0) + 1
    return {"spans": len(spans), "services": sorted(set(services.values())),
            "span_names": dict(sorted(names.items())), "missing_parents": sorted(set(orphans)),
            "cross_trace_links": links[:10]}

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_id")
    p.add_argument("--jaeger", default="http://localhost:16686")
    p.add_argument("--docker-stats", action="store_true")
    a = p.parse_args()
    engine = get_engine()
    rows = TraceStore(engine).list(a.run_id)
    ok = True
    print(f"run {a.run_id}: {len(rows)} recorded trace(s)")
    for r in rows:
        line = f"- {r['kind']:<13} attempt={r['attempt']} trace={r['trace_id']} " \
               f"sampled={r['sampled']}"
        if not r["sampled"]:
            print(line + "  (not sampled: nothing exported, as configured)")
            continue
        t = jaeger_trace(a.jaeger, r["trace_id"])
        if t is None:
            ok = False
            print(line + "  NOT IN JAEGER")
            continue
        print(line + "  in Jaeger: " + json.dumps(summarize(t), indent=2))
    events = EventStore(engine).events(run_id=a.run_id)
    print(f"worker events for the run ({len(events)}):")
    for e in events:
        print(f"  {e['occurred_at']:%H:%M:%S} {e['kind']:<30} instance={e['instance_id']} "
              f"cause={e['inferred_cause']} trace={e['trace_id']}")
    if a.docker_stats:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format",
                              "{{.Name}} {{.MemUsage}} {{.CPUPerc}}"],
                             capture_output=True, text=True, check=False).stdout
        print("container memory:")
        for line in out.splitlines():
            if "collector" in line or "jaeger" in line:
                print("  " + line)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
