"""Worker supervisor with real processes (a scripted fake worker): exit codes from the OS,
graceful stop, forced kill, missed heartbeats, backoff, restart limit, duplicate prevention,
and crash events that survive a database outage."""

import json
import os
import sys
import threading
import time
from pathlib import Path
import psutil
import pytest
from procedure_helpers import make_engine
from sqlalchemy import create_engine
from water_source_research.db.session import init_db
from water_source_research.observability.config import ObservabilitySettings
from water_source_research.observability.supervisor import (
    EXIT_DUPLICATE, EXIT_GAVE_UP, EXIT_OK, Supervisor, SupervisorLock,
)
from water_source_research.observability.worker_events import (
    EventLog, EventStore, EventSync, WorkerEvent, atomic_write_json,
)
from water_source_research.research_queue.diagnostics import heartbeat_path

FAKE = Path(__file__).parent / "fixtures" / "fake_worker.py"

def settings(**overrides) -> ObservabilitySettings:
    base = {"sample_seconds": 0.2, "heartbeat_stale_factor": 3.0, "restart_base_seconds": 0.05,
            "restart_max_seconds": 0.4, "max_failures": 3, "failure_window_seconds": 60,
            "stop_grace_seconds": 1.0, "event_sync_seconds": 0.2}
    return ObservabilitySettings(**{**base, **overrides})

def supervisor(tmp_path, modes, engine=None, **overrides) -> Supervisor:
    starts = []

    def command(instance_id, supervisor_id):
        mode = modes[min(len(starts), len(modes) - 1)]
        starts.append(mode)
        return [sys.executable, str(FAKE), "--log-dir", str(tmp_path / "logs"),
                "--instance-id", instance_id, "--supervisor-id", supervisor_id,
                "--mode", mode]

    sup = Supervisor(command=command, log_dir=tmp_path / "logs", settings=settings(**overrides),
                     engine=engine, poll_seconds=0.05, collect_windows_events=False)
    sup.starts = starts
    return sup

def events(tmp_path) -> list[dict]:
    out = []
    for p in sorted((tmp_path / "logs" / "events").glob("*.jsonl")):
        out += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    return sorted(out, key=lambda e: e["occurred_at"])

def kinds(tmp_path) -> list[str]:
    return [e["kind"] for e in events(tmp_path)]

def in_background(sup: Supervisor) -> tuple[threading.Thread, list[int]]:
    result: list[int] = []
    t = threading.Thread(target=lambda: result.append(sup.run()), daemon=True)
    t.start()
    return t, result

def wait_for(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path, "sup.db")
    yield eng
    eng.dispose()

# --- startup, graceful stop, exit codes -------------------------------------------------

def test_graceful_stop_is_a_clean_shutdown(tmp_path, engine):
    sup = supervisor(tmp_path, ["clean"], engine)
    thread, result = in_background(sup)
    wait_for(lambda: list((tmp_path / "logs" / "heartbeats").glob("*.json")))
    sup.request_stop()
    thread.join(20)
    assert result == [EXIT_OK]
    exit_event = next(e for e in events(tmp_path) if e["kind"] == "WORKER_STOPPED")
    assert exit_event["inferred_cause"] == "CLEAN_SHUTDOWN"
    assert exit_event["cause_confidence"] == "CONFIRMED"
    assert exit_event["facts"]["exit_code"] == 0 and exit_event["facts"]["stop_requested"]
    assert "WORKER_EXITED_UNEXPECTEDLY" not in kinds(tmp_path)
    assert kinds(tmp_path)[:2] == ["SUPERVISOR_STARTED", "WORKER_STARTED"]
    # the same events reached the database
    assert {e["kind"] for e in EventStore(engine).events()} >= {"WORKER_STARTED",
                                                                "WORKER_STOPPED"}

def test_stop_file_from_cli_stops_both(tmp_path):
    sup = supervisor(tmp_path, ["clean"])
    thread, result = in_background(sup)
    wait_for(lambda: list((tmp_path / "logs" / "heartbeats").glob("*.json")))
    (tmp_path / "logs" / "stop").mkdir(exist_ok=True)
    (tmp_path / "logs" / "stop" / "supervisor.stop").write_text("now")
    thread.join(20)
    assert result == [EXIT_OK]
    assert sup.exits[0]["class"] == "CLEAN_SHUTDOWN"

def test_exit_code_without_python_cleanup_is_recorded(tmp_path):
    sup = supervisor(tmp_path, ["exit:127", "quick"])
    assert sup.run() == EXIT_OK
    crash = sup.exits[0]
    assert crash["exit_code"] == 127 and crash["facts"]["exit_hex"] == "0x0000007F"
    assert crash["class"] == "UNKNOWN_TERMINATION" and crash["confidence"] == "UNKNOWN"
    assert "not an out-of-memory code" in next(
        e for e in events(tmp_path) if e["kind"] == "WORKER_EXITED_UNEXPECTEDLY")["rationale"]
    assert kinds(tmp_path).count("WORKER_RESTARTED") == 1

def test_forced_kill_is_captured_from_the_os(tmp_path):
    sup = supervisor(tmp_path, ["ignore-stop", "quick"])
    thread, result = in_background(sup)
    wait_for(lambda: list((tmp_path / "logs" / "heartbeats").glob("*.json")))
    pid = json.loads(next((tmp_path / "logs" / "heartbeats").glob("*.json")).read_text())["pid"]
    psutil.Process(pid).kill()  # no handler, no atexit, no flush in the worker
    thread.join(20)
    assert result == [EXIT_OK]
    killed = sup.exits[0]
    assert killed["exit_code"] not in (None, 0)
    assert killed["class"] not in ("CONFIRMED_MEMORY_EXHAUSTION", "CLEAN_SHUTDOWN")
    assert killed["facts"]["killed_by_supervisor"] is None
    assert killed["facts"]["last_heartbeat_at"] is not None

def test_worker_ignoring_the_stop_is_killed_after_the_grace(tmp_path):
    sup = supervisor(tmp_path, ["ignore-stop"], stop_grace_seconds=0.5)
    thread, result = in_background(sup)
    wait_for(lambda: list((tmp_path / "logs" / "heartbeats").glob("*.json")))
    sup.request_stop()
    thread.join(20)
    assert result == [EXIT_OK]
    assert sup.exits[0]["class"] == "EXTERNAL_TERMINATION"
    assert sup.exits[0]["facts"]["killed_by_supervisor"] == "stop_timeout"

def test_uncaught_connection_error_is_read_from_the_worker_log(tmp_path):
    sup = supervisor(tmp_path, ["exception:ConnectionRefusedError", "quick"])
    assert sup.run() == EXIT_OK
    crash = sup.exits[0]
    assert crash["facts"]["terminating_exception"] == "ConnectionRefusedError"
    assert crash["class"] == "CONNECTION_FAILURE"
    # the log of a crashed worker is protected from retention
    assert crash["facts"]["worker_log"]
    protected = json.loads((tmp_path / "logs" / "crash-evidence.json").read_text())
    assert Path(crash["facts"]["worker_log"]).name in protected

def test_memory_error_alone_is_not_confirmed_oom(tmp_path):
    sup = supervisor(tmp_path, ["exception:MemoryError", "quick"])
    sup.run()
    assert sup.exits[0]["class"] == "POSSIBLE_MEMORY_EXHAUSTION"
    assert sup.exits[0]["confidence"] == "PROBABLE"

def test_interrupted_runs_are_recorded_with_their_last_trace(tmp_path):
    sup = supervisor(tmp_path, ["active", "quick"])
    sup.run()
    interrupted = [e for e in events(tmp_path) if e["kind"] == "JOB_INTERRUPTED"]
    assert len(interrupted) == 1
    e = interrupted[0]
    assert (e["run_id"], e["trace_id"], e["span_id"]) == ("run-abc", "ab" * 16, "cd" * 8)
    assert e["facts"]["stage"] == "acquire"
    exit_event = next(x for x in events(tmp_path)
                      if x["kind"] == "WORKER_EXITED_UNEXPECTEDLY")
    assert e["facts"]["exit_event_id"] == exit_event["event_id"]
    assert exit_event["facts"]["affected_runs"] == ["run-abc"]

# --- heartbeats ---------------------------------------------------------------------------

def test_missing_heartbeat_is_recorded_without_declaring_death(tmp_path):
    sup = supervisor(tmp_path, ["hang"])
    thread, result = in_background(sup)
    wait_for(lambda: "HEARTBEAT_MISSING" in kinds(tmp_path))
    missing = next(e for e in events(tmp_path) if e["kind"] == "HEARTBEAT_MISSING")
    assert missing["facts"]["process_alive"] is True
    assert "WORKER_EXITED_UNEXPECTEDLY" not in kinds(tmp_path)
    sup.s = settings(stop_grace_seconds=0.3)
    sup.request_stop()  # the hung worker never reads its stop file: killed after the grace
    thread.join(20)
    assert result == [EXIT_OK]

def test_kill_on_stall_is_opt_in(tmp_path):
    sup = supervisor(tmp_path, ["hang", "quick"], kill_on_stall=True, stall_kill_seconds=1.0)
    assert sup.run() == EXIT_OK
    assert sup.exits[0]["class"] == "HEARTBEAT_TIMEOUT"

# --- restarts -----------------------------------------------------------------------------

def test_restart_backoff_and_limit(tmp_path):
    sup = supervisor(tmp_path, ["exit:3"], max_failures=3)
    assert sup.run() == EXIT_GAVE_UP
    assert sup.restart_delays == [0.05, 0.1, 0.2]  # exponential, capped at 0.4
    assert len(sup.starts) == 4
    assert kinds(tmp_path).count("RESTART_SCHEDULED") == 3
    gave_up = next(e for e in events(tmp_path) if e["kind"] == "RESTART_FAILED")
    assert gave_up["facts"]["reason"] == "restart limit reached"

def test_unstartable_worker_counts_as_a_failure(tmp_path):
    sup = Supervisor(command=lambda i, s: [str(tmp_path / "missing.exe")],
                     log_dir=tmp_path / "logs", settings=settings(max_failures=1),
                     poll_seconds=0.05, collect_windows_events=False)
    assert sup.run() == EXIT_GAVE_UP
    reasons = [e["facts"]["reason"] for e in events(tmp_path) if e["kind"] == "RESTART_FAILED"]
    assert reasons.count("could not start the worker") == 2
    assert reasons[-1] == "restart limit reached"

# --- duplicates ---------------------------------------------------------------------------

def test_second_supervisor_is_refused(tmp_path):
    held = SupervisorLock(tmp_path / "logs" / "worker-supervisor.lock")
    held.acquire()
    try:
        assert supervisor(tmp_path, ["quick"]).run() == EXIT_DUPLICATE
    finally:
        held.release()
    assert supervisor(tmp_path, ["quick"]).run() == EXIT_OK  # free again

def test_live_worker_from_a_dead_supervisor_blocks_a_duplicate(tmp_path):
    me = psutil.Process()
    atomic_write_json(tmp_path / "logs" / "worker-supervisor.json",
                      {"worker_pid": os.getpid(), "worker_create_time": me.create_time()})
    assert supervisor(tmp_path, ["quick"]).run() == EXIT_DUPLICATE
    # a recorded pid that no longer runs (or was reused) does not block
    atomic_write_json(tmp_path / "logs" / "worker-supervisor.json",
                      {"worker_pid": os.getpid(), "worker_create_time": 1.0})
    assert supervisor(tmp_path, ["quick"]).run() == EXIT_OK

# --- database outage ----------------------------------------------------------------------

def test_events_survive_a_database_outage_and_sync_idempotently(tmp_path, engine):
    down = create_engine(f"sqlite:///{tmp_path / 'no' / 'such' / 'dir.db'}")
    sup = supervisor(tmp_path, ["exit:2", "quick"], engine=down)
    assert sup.run() == EXIT_OK
    local = events(tmp_path)
    assert "WORKER_EXITED_UNEXPECTEDLY" in [e["kind"] for e in local]
    sync = EventSync(tmp_path / "logs" / "events", EventStore(engine))
    assert sync.sync_pending() == len(local)
    assert sync.sync_pending() == 0  # nothing new
    (tmp_path / "logs" / "events" / "sync-state.json").unlink()  # forget progress
    assert sync.sync_pending() == 0  # re-reading everything inserts nothing twice
    stored = EventStore(engine).events()
    assert sorted(e["event_id"] for e in stored) == sorted(e["event_id"] for e in local)
    crash = next(e for e in stored if e["kind"] == "WORKER_EXITED_UNEXPECTEDLY")
    assert crash["facts"]["exit_code"] == 2 and crash["inferred_cause"]

def test_sync_waits_out_an_outage_without_losing_offsets(tmp_path, engine):
    events_dir = tmp_path / "events"
    log = EventLog(events_dir, "w1")
    for i in range(3):
        log.append(WorkerEvent(kind="JOB_RECLAIMED", source="worker", run_id=f"r{i}"))
    broken = EventSync(events_dir, EventStore(create_engine(
        f"sqlite:///{tmp_path / 'missing' / 'x.db'}")))
    assert broken.sync_pending() == 0 and broken.outage
    assert broken.unsynced_files() == ["w1.jsonl"]
    healthy = EventSync(events_dir, EventStore(engine))
    assert healthy.sync_pending() == 3 and not healthy.outage
    assert healthy.unsynced_files() == []

def test_torn_last_line_is_left_for_the_next_sync(tmp_path, engine):
    events_dir = tmp_path / "events"
    EventLog(events_dir, "w1").append(WorkerEvent(kind="JOB_RESUMED", source="worker"))
    with open(events_dir / "w1.jsonl", "a", encoding="utf-8") as f:
        f.write('{"kind": "JOB_RES')  # a write cut off by a crash
    sync = EventSync(events_dir, EventStore(engine))
    assert sync.sync_pending() == 1
    assert sync.unsynced_files() == ["w1.jsonl"]  # the partial line is not skipped over

# --- rotation and retention ---------------------------------------------------------------

def test_rotation_keeps_unsynced_files_and_prunes_only_synced_ones(tmp_path, engine):
    events_dir = tmp_path / "events"
    log = EventLog(events_dir, "w1", max_bytes=600)
    for i in range(12):
        log.append(WorkerEvent(kind="JOB_RECLAIMED", source="worker", run_id=f"r{i}",
                               facts={"pad": "x" * 100}))
    rotated = sorted(events_dir.glob("w1.rot-*.jsonl"))
    assert rotated  # rotated files are kept
    sync = EventSync(events_dir, EventStore(engine), retain_bytes=1)
    # Nothing synced yet: an outage must never cost evidence.
    broken = EventSync(events_dir, EventStore(create_engine(
        f"sqlite:///{tmp_path / 'missing' / 'x.db'}")), retain_bytes=1)
    broken.sync_pending()
    assert sorted(events_dir.glob("w1.rot-*.jsonl")) == rotated
    sink_events = []

    class Sink:
        def emit(self, kind, source, **values):
            sink_events.append((kind, values))

    sync.sink = Sink()
    assert sync.sync_pending() == 12
    assert not list(events_dir.glob("w1.rot-*.jsonl"))  # synced and over retention: pruned
    assert (events_dir / "w1.jsonl").exists()  # the live file is never pruned
    assert sink_events and sink_events[0][0] == "EVENT_FILES_PRUNED"
    assert len(EventStore(engine).events()) == 12

def test_worker_log_retention_never_deletes_crash_evidence(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(5):
        p = logs / f"worker-2026010{i}T000000Z-{100 + i}.log"
        p.write_text("x")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    atomic_write_json(logs / "crash-evidence.json", ["worker-20260100T000000Z-100.log"])
    sup = Supervisor(command=lambda i, s: [], log_dir=logs, settings=settings(),
                     worker_logs_keep=2, collect_windows_events=False)
    sup._prune_worker_logs()
    left = sorted(p.name for p in logs.glob("worker-*.log"))
    assert left == ["worker-20260100T000000Z-100.log", "worker-20260103T000000Z-103.log",
                    "worker-20260104T000000Z-104.log"]

def test_heartbeat_file_is_replaced_atomically(tmp_path):
    path = heartbeat_path(tmp_path, "host-1:abc")  # unsafe characters are made safe
    assert ":" not in path.name
    atomic_write_json(path, {"n": 1})
    atomic_write_json(path, {"n": 2})
    assert json.loads(path.read_text()) == {"n": 2}
    assert not list(path.parent.glob("*.tmp"))

def test_store_tables_exist_after_init(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    init_db(eng)
    EventStore(eng).insert([WorkerEvent(kind="WORKER_STARTED", source="supervisor")])
    assert len(EventStore(eng).events()) == 1
