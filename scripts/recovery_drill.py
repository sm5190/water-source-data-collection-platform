"""Live crash/restart drill for one REAL city run (not run by pytest).

Usage: uv run python scripts/recovery_drill.py [--api http://localhost:8000] [--city Toronto]
       [--country Canada] [--fields a,b,c] [--kill-after acquire] [--lease 30]

Needs the API running (uvicorn) plus everything a real run needs (VT VPN, Postgres at head,
MinIO, SearXNG). Steps: refuse if any other job is QUEUED/RUNNING (never disrupts unrelated
work) -> submit through the API -> start the real research worker as a subprocess (env
overrides for that process only: short lease, kept checkpoints, concurrency 1) -> once the
``--kill-after`` step is recorded, hard-kill the worker process tree (simulated crash) ->
check the job is still RUNNING with checkpoints in Postgres -> wait for the lease to expire ->
start a fresh worker -> verify the same run resumed from its checkpoint without repeating
completed work or duplicating rows. Exit 0 only if every check passes.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
import httpx
from sqlalchemy import text
from water_source_research.core.config import get_settings
from water_source_research.core.paths import REPO_ROOT
from water_source_research.db.session import get_engine
from water_source_research.research_queue.queue import JobStatus, ResearchQueue
from water_source_research.services.evidence_store import EvidenceRepository

FIELDS = "utility_name,water_source,total_city_water_withdrawals_m3_day"

def say(msg: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}Z] {msg}", flush=True)

def start_worker(env: dict[str, str], log: Path) -> subprocess.Popen:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_research_worker.py"), "--once"],
        cwd=REPO_ROOT, env=env, stdout=log.open("ab"), stderr=subprocess.STDOUT,
        creationflags=flags, start_new_session=sys.platform != "win32")

def hard_kill(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)

def checkpoints(engine, run_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id = :r"),
                            {"r": run_id}).scalar()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--city", default="Toronto")
    parser.add_argument("--country", default="Canada")
    parser.add_argument("--fields", default=FIELDS)
    parser.add_argument("--kill-after", default="acquire",
                        help="graph node whose first completed step triggers the kill")
    parser.add_argument("--lease", type=float, default=30.0)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    settings = get_settings()
    engine = get_engine()
    queue = ResearchQueue(engine, settings.research_queue)
    repo = EvidenceRepository(engine)
    checks: list[tuple[str, bool, str]] = []

    busy = {k: v for k, v in queue.stats()["by_status"].items()
            if k in (JobStatus.QUEUED.value, JobStatus.RUNNING.value) and v}
    if busy:
        say(f"ABORT: other jobs are active {busy}; the drill would claim or disturb them")
        return 2

    r = httpx.post(f"{args.api.rstrip('/')}/research/city", timeout=30, json={
        "city_name": args.city, "country": args.country,
        "field_keys": [f.strip() for f in args.fields.split(",")]})
    r.raise_for_status()
    run_id = r.json()["run_id"]
    say(f"submitted through the API: run_id={run_id} (HTTP {r.status_code})")

    env = {**os.environ, "RESEARCH_QUEUE__LEASE_SECONDS": str(args.lease),
           "RESEARCH_QUEUE__HEARTBEAT_SECONDS": str(max(1.0, args.lease / 6)),
           "RESEARCH_QUEUE__KEEP_CHECKPOINTS": "true",
           "RESEARCH_QUEUE__WORKER_CONCURRENCY": "1", "PYTHONUNBUFFERED": "1"}
    log = Path(os.environ.get("TEMP", "/tmp")) / f"recovery-drill-{run_id[:8]}.log"
    worker = start_worker(env, log)
    say(f"worker #1 started (pid {worker.pid}); log: {log}")

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        nodes = [s["node"] for s in queue.steps(run_id)]
        if args.kill_after in nodes:
            break
        if worker.poll() is not None:
            say(f"worker #1 exited (code {worker.returncode}) before '{args.kill_after}': "
                "drill not possible")
            return 3
        time.sleep(0.5)
    else:
        say("timed out waiting for the kill point")
        hard_kill(worker)
        return 3

    hard_kill(worker)
    killed_at = datetime.now(UTC)
    say(f"worker #1 HARD-KILLED after step '{args.kill_after}' (taskkill /F /T)")
    job = queue.get(run_id)
    steps_before = queue.steps(run_id)
    docs_before = {d["canonical_url"]: d["retrieved_at"] for d in repo.documents(run_id)}
    cp_before = checkpoints(engine, run_id)
    claims_before = repo.counts(run_id)["claims"]
    say(f"after crash: status={job.status.value} attempts={job.attempts} "
        f"lease_until={job.lease_until} checkpoints={cp_before} "
        f"steps={[s['node'] for s in steps_before]} documents={len(docs_before)} "
        f"claims={claims_before}")
    checks.append(("job left RUNNING by the crash", job.status is JobStatus.RUNNING,
                   job.status.value))
    checks.append(("checkpoint rows persisted before resume", cp_before > 0,
                   f"{cp_before} rows"))
    checks.append(("acquisition rows durable before resume", len(docs_before) > 0,
                   f"{len(docs_before)} source_documents"))

    wait = max(0.0, (job.lease_until - killed_at).total_seconds()) + 2 if job.lease_until else 2
    say(f"waiting {wait:.0f}s for the lease to expire")
    time.sleep(wait)

    worker2 = start_worker(env, log)
    say(f"worker #2 started (pid {worker2.pid})")
    try:
        code = worker2.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        hard_kill(worker2)
        say("worker #2 timed out")
        return 4
    job = queue.get(run_id)
    steps = queue.steps(run_id)
    attempt2 = [s["node"] for s in steps if s["attempt"] == job.attempts]
    docs_after = repo.documents(run_id)
    refetched = [d["canonical_url"] for d in docs_after
                 if d["canonical_url"] in docs_before
                 and d["retrieved_at"] != docs_before[d["canonical_url"]]]
    dup_docs = len(docs_after) - len({d["canonical_url"] for d in docs_after})
    result = queue.result(run_id) or {}
    state_claims = sum(len(f.get("candidate_claims") or []) for f in
                       (result.get("research_state") or {}).get("fields", {}).values())
    counts = repo.counts(run_id)
    say(f"worker #2 exited {code}: status={job.status.value} attempts={job.attempts} "
        f"resumed={job.resumed} last_error={job.last_error!r}")
    say(f"attempt {job.attempts} steps: {attempt2}")
    checks += [
        ("same run_id succeeded", job.status is JobStatus.SUCCEEDED, job.status.value),
        ("resumed from checkpoint (attempt 2)", job.resumed and job.attempts == 2,
         f"attempts={job.attempts} resumed={job.resumed}"),
        ("no restart from plan", "plan" not in attempt2, f"{attempt2[:3]}"),
        (f"completed '{args.kill_after}' work not repeated", not refetched,
         f"re-fetched round-1 documents: {refetched or 'none'}"),
        ("no duplicate documents", dup_docs == 0, f"{dup_docs} duplicates"),
        ("claims stored once", counts["claims"] == state_claims,
         f"DB {counts['claims']} vs final state {state_claims}"),
        ("checkpoints kept for audit", checkpoints(engine, run_id) > 0,
         f"{checkpoints(engine, run_id)} rows"),
    ]
    print()
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<40} {detail}")
    print(f"\nNext: uv run python scripts/verify_run.py {run_id} --api {args.api}")
    return 0 if all(ok for _, ok, _ in checks) else 1

if __name__ == "__main__":
    sys.exit(main())
