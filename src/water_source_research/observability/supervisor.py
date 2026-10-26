"""Worker supervisor: runs one research worker process, records how it ends, restarts it.

It is a separate process from the worker, so a worker that is killed (or dies without running
any Python cleanup) is still observed: the exit code comes from the operating system via
``Popen.wait``. The supervisor records, as worker events (local file first, then Postgres):
start/restart, missed and restored heartbeats, how the process ended (observed facts plus an
evidence-based classification, ``termination.classify``), the runs it held (from its last
heartbeat file and the lease table), restart scheduling and giving up.

- One supervisor per log directory (an OS file lock); a new one also refuses to start while a
  worker recorded by a previous supervisor is still alive.
- Windows: the worker is put in a Job Object that kills it when the supervisor's handle
  closes, so a dead supervisor leaves no orphaned worker.
- Graceful stop: the supervisor writes the worker's stop file (the worker drains its runs and
  exits 0) and kills it only after ``stop_grace_seconds``.
- Restarts back off exponentially; after ``max_failures`` unexpected exits within
  ``failure_window_seconds`` it gives up (RESTART_FAILED) instead of looping.
- A missed heartbeat is recorded, never treated as death (``kill_on_stall`` is opt-in).
"""

import contextlib
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
import psutil
from sqlalchemy import Engine
from water_source_research.research_queue.diagnostics import heartbeat_path, stop_path
from .config import ObservabilitySettings
from .evidence import log_facts, memory_facts, windows_events, worker_log
from .termination import CLEAN_SHUTDOWN, classify, exit_hex
from .worker_events import (
    EventKind, EventLog, EventSink, EventStore, EventSync, atomic_write_json, read_json,
)

log = logging.getLogger("worker_supervisor")

EXIT_OK, EXIT_GAVE_UP, EXIT_DUPLICATE = 0, 1, 3
SAMPLE_KEYS = ("rss_mb", "private_mb", "peak_rss_mb", "cpu_percent", "threads", "uptime_s",
               "commit_used_ratio", "system_available_ratio", "system_available_mb",
               "active_jobs", "llm_requests_in_flight", "fetches_in_flight",
               "checkpoint_last_bytes", "checkpoint_peak_bytes", "at")

class DuplicateSupervisor(RuntimeError):
    pass

class SupervisorLock:
    """Exclusive, OS-released lock (dies with the process: never stale)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+b")  # noqa: SIM115 - held for the supervisor's lifetime
        try:
            if sys.platform == "win32":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            f.close()
            raise DuplicateSupervisor(f"another supervisor holds {self.path}") from exc
        self._file = f

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._file.close()
        self._file = None

def process_alive(pid: int | None, create_time: float | None) -> bool:
    """Is this exact process (pid + creation time, so a reused pid doesn't count) alive?"""
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        return create_time is None or abs(p.create_time() - create_time) < 1.0
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

def kill_on_close_job(proc: subprocess.Popen) -> Any:
    """Windows: put ``proc`` in a Job Object that is killed when this process exits.
    Returns the job handle (keep a reference) or None (not Windows / not possible)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9,  # ExtendedLimitInformation
                                          ctypes.byref(info), ctypes.sizeof(info))
    handle = getattr(proc, "_handle", None)
    if not ok or handle is None or not kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(job), wintypes.HANDLE(int(handle))):
        kernel32.CloseHandle(wintypes.HANDLE(job))
        return None
    return job

@dataclass
class WorkerProcess:
    instance_id: str
    proc: subprocess.Popen
    started_wall: datetime
    started: float  # supervisor clock
    job_handle: Any = None
    stop_sent_at: float | None = None
    killed_by_supervisor: str | None = None
    heartbeat_missing: bool = False
    real_pid: int | None = None  # the interpreter under a venv launcher, once seen
    facts: dict[str, Any] = field(default_factory=dict)

class Supervisor:
    def __init__(self, *, command: Callable[[str, str], list[str]], log_dir: Path,
                 settings: ObservabilitySettings | None = None, engine: Engine | None = None,
                 supervisor_id: str | None = None, cwd: Path | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, poll_seconds: float = 1.0,
                 collect_windows_events: bool = True, worker_logs_keep: int = 200) -> None:
        self.command = command
        self.log_dir = log_dir
        self.s = settings or ObservabilitySettings()
        self.supervisor_id = supervisor_id or f"sup-{uuid.uuid4().hex[:10]}"
        self.cwd = cwd
        self.clock = clock
        self.sleep = sleep
        self.poll = poll_seconds
        self.collect_windows_events = collect_windows_events
        self.worker_logs_keep = worker_logs_keep
        self.store = EventStore(engine) if engine is not None else None
        self.sink = EventSink(EventLog(log_dir / "events", self.supervisor_id,
                                       int(self.s.events_max_mb * 2**20)), self.store)
        self.sync = (EventSync(log_dir / "events", self.store,
                               retain_bytes=int(self.s.events_retain_mb * 2**20),
                               sink=self.sink) if self.store is not None else None)
        self.lock = SupervisorLock(log_dir / "worker-supervisor.lock")
        self.info_path = log_dir / "worker-supervisor.json"
        self.stop_file = log_dir / "stop" / "supervisor.stop"
        self.stop_requested = False
        self.restart_delays: list[float] = []  # for tests and the report
        self.exits: list[dict[str, Any]] = []
        self._last_sync = float("-inf")

    # --- public ---------------------------------------------------------------------------

    def request_stop(self, *_: Any) -> None:
        self.stop_requested = True

    def run(self) -> int:
        try:
            self.lock.acquire()
        except DuplicateSupervisor as exc:
            log.error("%s; not starting a second supervisor", exc)
            return EXIT_DUPLICATE
        try:
            previous = read_json(self.info_path) or {}
            if process_alive(previous.get("worker_pid"), previous.get("worker_create_time")):
                log.error("worker pid %s from a previous supervisor is still running; refusing "
                          "to start a duplicate", previous.get("worker_pid"))
                return EXIT_DUPLICATE
            with contextlib.suppress(FileNotFoundError):
                self.stop_file.unlink()
            self._sync(force=True)
            self.sink.emit(EventKind.SUPERVISOR_STARTED, "supervisor",
                           supervisor_id=self.supervisor_id, pid=os.getpid(),
                           facts={"host": socket.gethostname(), "log_dir": str(self.log_dir)})
            return self._loop()
        finally:
            self.sink.emit(EventKind.SUPERVISOR_STOPPED, "supervisor",
                           supervisor_id=self.supervisor_id, pid=os.getpid(),
                           facts={"restarts": len(self.restart_delays),
                                  "stop_requested": self.stop_requested})
            self._sync(force=True)
            self._write_info(None)
            self.lock.release()

    # --- loop -----------------------------------------------------------------------------

    def _loop(self) -> int:
        failures: list[float] = []
        previous_instance: str | None = None
        while not self._stop_wanted():
            worker = self._spawn(previous_instance)
            if worker is None:  # could not start the process at all
                failures.append(self.clock())
                outcome = self._after_failure(failures)
                if outcome is not None:
                    return outcome
                continue
            self._watch(worker)
            clean = self._record_exit(worker)
            previous_instance = worker.instance_id
            if clean or self.stop_requested:
                return EXIT_OK
            failures.append(self.clock())
            outcome = self._after_failure(failures)
            if outcome is not None:
                return outcome
        return EXIT_OK

    def _after_failure(self, failures: list[float]) -> int | None:
        """Back off before the next start, or give up. None = restart."""
        now = self.clock()
        failures[:] = [t for t in failures if now - t <= self.s.failure_window_seconds]
        if len(failures) > self.s.max_failures:
            self.sink.emit(EventKind.RESTART_FAILED, "supervisor",
                           supervisor_id=self.supervisor_id, pid=os.getpid(),
                           facts={"reason": "restart limit reached",
                                  "failures_in_window": len(failures),
                                  "window_seconds": self.s.failure_window_seconds,
                                  "max_failures": self.s.max_failures})
            log.error("giving up: %d unexpected exits within %.0f s", len(failures),
                      self.s.failure_window_seconds)
            return EXIT_GAVE_UP
        delay = min(self.s.restart_max_seconds,
                    self.s.restart_base_seconds * 2 ** (len(failures) - 1))
        self.restart_delays.append(delay)
        self.sink.emit(EventKind.RESTART_SCHEDULED, "supervisor",
                       supervisor_id=self.supervisor_id, pid=os.getpid(),
                       facts={"delay_seconds": delay, "failures_in_window": len(failures)})
        log.warning("restarting the worker in %.1f s (%d failure(s) in window)", delay,
                    len(failures))
        deadline = self.clock() + delay
        while self.clock() < deadline and not self._stop_wanted():
            self.sleep(min(self.poll, max(0.0, deadline - self.clock())))
            self._sync()
        return EXIT_OK if self._stop_wanted() else None

    def _spawn(self, previous_instance: str | None) -> WorkerProcess | None:
        instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:10]}"
        cmd = self.command(instance_id, self.supervisor_id)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        try:
            proc = subprocess.Popen(cmd, cwd=self.cwd, creationflags=flags)
        except OSError as exc:
            self.sink.emit(EventKind.RESTART_FAILED, "supervisor",
                           supervisor_id=self.supervisor_id, instance_id=instance_id,
                           pid=os.getpid(), facts={"reason": "could not start the worker",
                                                   "error": f"{type(exc).__name__}: {exc}"[:500],
                                                   "command": cmd[:3]})
            return None
        worker = WorkerProcess(instance_id, proc, datetime.now(UTC), self.clock(),
                               job_handle=kill_on_close_job(proc))
        try:
            create_time = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            create_time = None
        self._write_info({"worker_pid": proc.pid, "worker_create_time": create_time,
                          "instance_id": instance_id})
        kind = EventKind.WORKER_RESTARTED if previous_instance else EventKind.WORKER_STARTED
        self.sink.emit(kind, "supervisor", supervisor_id=self.supervisor_id,
                       instance_id=instance_id, pid=proc.pid,
                       facts={"previous_instance": previous_instance,
                              "job_object": worker.job_handle is not None,
                              "command": [Path(cmd[0]).name, *cmd[1:2]]})
        self.sink.instance(instance_id, supervisor_id=self.supervisor_id,
                           host=socket.gethostname(), pid=proc.pid,
                           started_at=worker.started_wall)
        log.info("started worker %s (pid %d)", instance_id, proc.pid)
        return worker

    def _watch(self, w: WorkerProcess) -> None:
        stale_after = self.s.heartbeat_stale_factor * self.s.sample_seconds
        while w.proc.poll() is None:
            self.sleep(self.poll)
            now = self.clock()
            if self._stop_wanted() and w.stop_sent_at is None:
                self._request_worker_stop(w)
            if w.stop_sent_at is not None and w.killed_by_supervisor is None \
                    and now - w.stop_sent_at > self.s.stop_grace_seconds:
                self._kill(w, "stop_timeout")
            self._check_heartbeat(w, stale_after)
            self._sync()

    def _check_heartbeat(self, w: WorkerProcess, stale_after: float) -> None:
        hb = read_json(heartbeat_path(self.log_dir, w.instance_id)) or {}
        if w.real_pid is None:
            w.real_pid = self._worker_pid(w, hb) if hb else None
        age = _age_seconds(hb.get("at"))
        if age is None:  # no heartbeat yet: count from the start
            age = self.clock() - w.started
        if age > stale_after and not w.heartbeat_missing:
            w.heartbeat_missing = True
            alive = w.proc.poll() is None
            cpu = None
            with contextlib.suppress(psutil.Error):
                cpu = psutil.Process(w.real_pid or w.proc.pid).cpu_percent(interval=0.1)
            self.sink.emit(EventKind.HEARTBEAT_MISSING, "supervisor",
                           supervisor_id=self.supervisor_id, instance_id=w.instance_id,
                           pid=w.proc.pid,
                           facts={"heartbeat_age_seconds": round(age, 1),
                                  "stale_after_seconds": stale_after, "process_alive": alive,
                                  "process_cpu_percent": cpu,
                                  "last_stage": _stages(hb)},
                           rationale="a missed heartbeat alone does not mean the process died")
        elif age <= stale_after and w.heartbeat_missing:
            w.heartbeat_missing = False
            self.sink.emit(EventKind.HEARTBEAT_RESTORED, "supervisor",
                           supervisor_id=self.supervisor_id, instance_id=w.instance_id,
                           pid=w.proc.pid, facts={"heartbeat_age_seconds": round(age, 1)})
        if self.s.kill_on_stall and age > self.s.stall_kill_seconds \
                and w.killed_by_supervisor is None:
            self._kill(w, "stall")

    def _request_worker_stop(self, w: WorkerProcess) -> None:
        w.stop_sent_at = self.clock()
        path = stop_path(self.log_dir, w.instance_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        log.info("asked worker %s to stop (grace %.0f s)", w.instance_id,
                 self.s.stop_grace_seconds)

    def _kill(self, w: WorkerProcess, reason: str) -> None:
        """Kill the whole process tree (a venv launcher runs the real interpreter as a
        child on Windows; killing only the launcher would leave the worker running)."""
        w.killed_by_supervisor = reason
        log.warning("killing worker %s (%s)", w.instance_id, reason)
        with contextlib.suppress(psutil.Error):
            for child in psutil.Process(w.proc.pid).children(recursive=True):
                with contextlib.suppress(psutil.Error):
                    child.kill()
        with contextlib.suppress(OSError):
            w.proc.kill()

    def _worker_pid(self, w: WorkerProcess, hb: dict[str, Any]) -> int:
        """The interpreter running the worker: the pid it reported, else the launched
        process's python child (venv launcher), else the launched process."""
        if hb.get("pid"):
            return int(hb["pid"])
        with contextlib.suppress(psutil.Error):
            for child in psutil.Process(w.proc.pid).children(recursive=True):
                if "python" in child.name().lower():
                    return child.pid
        return w.proc.pid

    # --- exit -----------------------------------------------------------------------------

    def _record_exit(self, w: WorkerProcess) -> bool:
        """Record how the worker ended. Returns True for a clean shutdown."""
        code = w.proc.wait()
        ended = datetime.now(UTC)
        hb = read_json(heartbeat_path(self.log_dir, w.instance_id)) or {}
        active = hb.get("active_runs") or {}
        facts: dict[str, Any] = {
            "exit_code": code, "exit_hex": exit_hex(code),
            "uptime_seconds": round((ended - w.started_wall).total_seconds(), 1),
            "started_at": w.started_wall.isoformat(), "ended_at": ended.isoformat(),
            "stop_requested": w.stop_sent_at is not None,
            "killed_by_supervisor": w.killed_by_supervisor,
            "last_heartbeat_at": hb.get("at"),
            "heartbeat_age_seconds": _age_seconds(hb.get("at")),
            "last_sample": {k: hb[k] for k in SAMPLE_KEYS if k in hb},
            "active_runs": active,
            "system_at_exit": _soft(memory_facts),
        }
        worker_pid = self._worker_pid(w, hb) if hb else w.real_pid or w.proc.pid
        facts["worker_pid"] = worker_pid
        facts["launcher_pid"] = w.proc.pid
        facts.update(_soft(lambda: log_facts(worker_log(self.log_dir, worker_pid))))
        if self.collect_windows_events:
            facts.update(_soft(lambda: windows_events(w.started_wall, ended, worker_pid)))
        cls, confidence, rationale = classify(facts)
        leased: list[str] = []
        if self.store is not None:
            with contextlib.suppress(Exception):
                leased = self.store.leased_runs(w.instance_id)
        affected = sorted(set(active) | set(leased))
        facts["affected_runs"] = affected
        clean = cls == CLEAN_SHUTDOWN
        kind = EventKind.WORKER_STOPPED if clean else EventKind.WORKER_EXITED_UNEXPECTEDLY
        exit_event = self.sink.emit(kind, "supervisor", supervisor_id=self.supervisor_id,
                                    instance_id=w.instance_id, pid=w.proc.pid, facts=facts,
                                    inferred_cause=cls, cause_confidence=confidence,
                                    rationale=rationale)
        if not clean:
            for run_id in affected:
                live = active.get(run_id) or {}
                self.sink.emit(EventKind.JOB_INTERRUPTED, "supervisor",
                               supervisor_id=self.supervisor_id, instance_id=w.instance_id,
                               pid=w.proc.pid, run_id=run_id, attempt=live.get("attempt"),
                               trace_id=live.get("trace_id"), span_id=live.get("span_id"),
                               facts={"stage": live.get("stage"),
                                      "exit_event_id": exit_event.event_id,
                                      "lease_still_held": run_id in leased},
                               inferred_cause=cls, cause_confidence=confidence)
            self._protect_log(facts.get("worker_log"))
        self.sink.instance(w.instance_id, stopped_at=ended, exit_code=code,
                           termination_class=cls)
        self.exits.append({"instance_id": w.instance_id, "class": cls,
                           "confidence": confidence, "exit_code": code, "facts": facts})
        log.log(logging.INFO if clean else logging.ERROR,
                "worker %s exited: code %s (%s) -> %s [%s] %s", w.instance_id, code,
                exit_hex(code), cls, confidence, rationale)
        for path in (heartbeat_path(self.log_dir, w.instance_id),
                     stop_path(self.log_dir, w.instance_id)):
            with contextlib.suppress(OSError):
                path.unlink()
        self._prune_worker_logs()
        self._sync(force=True)
        return clean

    # --- helpers --------------------------------------------------------------------------

    def _stop_wanted(self) -> bool:
        if not self.stop_requested and self.stop_file.exists():
            self.stop_requested = True
        return self.stop_requested

    def _sync(self, force: bool = False) -> None:
        if self.sync is None:
            return
        now = self.clock()
        if not force and now - self._last_sync < self.s.event_sync_seconds:
            return
        self._last_sync = now
        self.sync.sync_pending()

    def _write_info(self, worker: dict[str, Any] | None) -> None:
        with contextlib.suppress(OSError):
            atomic_write_json(self.info_path, {"supervisor_id": self.supervisor_id,
                                               "supervisor_pid": os.getpid(),
                                               **(worker or {})})

    def _protect_log(self, path: str | None) -> None:
        if not path:
            return
        index = self.log_dir / "crash-evidence.json"
        protected = set(read_json(index) or [])
        protected.add(Path(path).name)
        with contextlib.suppress(OSError):
            atomic_write_json(index, sorted(protected))

    def _prune_worker_logs(self) -> None:
        """Keep the newest ``worker_logs_keep`` worker logs; never one tied to a crash."""
        protected = set(read_json(self.log_dir / "crash-evidence.json") or [])
        logs = sorted(self.log_dir.glob("worker-*.log"), key=lambda p: p.stat().st_mtime)
        for p in logs[:max(0, len(logs) - self.worker_logs_keep)]:
            if p.name not in protected:
                with contextlib.suppress(OSError):
                    p.unlink()

def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return max(0.0, (datetime.now(UTC) - at).total_seconds())

def _stages(hb: dict[str, Any]) -> dict[str, Any]:
    return {k: (v or {}).get("stage") for k, v in (hb.get("active_runs") or {}).items()}

def _soft(collect: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return collect() or {}
    except Exception as exc:  # noqa: BLE001 - evidence collection never stops recording
        return {"collection_error": f"{type(exc).__name__}: {exc}"[:200]}

def install_signal_handlers(supervisor: Supervisor) -> None:
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for sig in signals:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, supervisor.request_stop)
