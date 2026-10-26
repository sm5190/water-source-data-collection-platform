"""Worker diagnostics: persistent logs, process resource samples, heartbeat file, graph-state size.

Evidence for questions like "did the worker run out of memory?" — collected, never assumed:
  - ``configure_worker_logging`` writes an append-only, timestamped log file per worker start
    (never overwritten), enables ``faulthandler`` (native crashes dump a traceback into it),
    and logs an exit line for normal exits and uncaught exceptions (with the exception type,
    which the supervisor reads back);
  - ``process_sample`` reads the process's memory (RSS and private bytes), CPU, handles,
    threads and uptime, and the system's available memory and commit charge (psutil);
  - ``state_size`` measures what a run carries in its graph state (and so in each checkpoint);
  - ``Monitor`` samples every ``interval`` seconds and writes the sample to the log, to an
    atomically replaced heartbeat file the supervisor reads (``heartbeats/<instance>.json``),
    to a size-rotated resource file (bounded), and to the ``worker_instances`` row. It also
    watches the instance's stop file (graceful stop requested by the supervisor).
The real exit code of a killed process can't be seen from inside it: run the worker under
``scripts/worker_supervisor.py``, which records it.
"""

import asyncio
import atexit
import faulthandler
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
import psutil
from water_source_research.observability.evidence import commit_memory
from water_source_research.observability.worker_events import atomic_write_json, safe_name

log = logging.getLogger("research_worker.diagnostics")
_FAULT_FILE: IO[str] | None = None

def configure_worker_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    """Log to ``log_dir/worker-<UTC start>-<pid>.log`` (append) and stderr."""
    global _FAULT_FILE
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"worker-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)
    # Native crashes (access violations, fatal errors) write their traceback here.
    _FAULT_FILE = open(path, "a", encoding="utf-8")  # noqa: SIM115 (kept open for faulthandler)
    faulthandler.enable(_FAULT_FILE, all_threads=True)

    previous_hook = sys.excepthook

    def excepthook(kind, value, tb):  # uncaught exception in the main thread
        # "...: <Type>" is parsed by observability/evidence.py (the terminating exception).
        logging.getLogger("research_worker").critical(
            "worker exiting on an uncaught exception: %s", kind.__name__,
            exc_info=(kind, value, tb))
        previous_hook(kind, value, tb)

    sys.excepthook = excepthook
    threading.excepthook = lambda args: logging.getLogger("research_worker").error(
        "uncaught exception in thread %s", args.thread, exc_info=(
            args.exc_type, args.exc_value, args.exc_traceback))
    atexit.register(lambda: logging.getLogger("research_worker").info(
        "worker process exiting normally (atexit) %s", process_sample()))
    logging.getLogger("research_worker").info(
        "logging to %s (pid %d, python %s, %s)", path, os.getpid(), sys.version.split()[0],
        sys.platform)
    return path

def process_sample(process: psutil.Process | None = None) -> dict[str, Any]:
    """This process's memory and resource use, and the system's memory."""
    p = process or psutil.Process()
    with p.oneshot():
        mem = p.memory_info()
        sample: dict[str, Any] = {"rss_mb": round(mem.rss / 2**20, 1),
                                  "threads": p.num_threads(),
                                  "uptime_s": round(time.time() - p.create_time())}
        private = getattr(mem, "private", None)  # Windows: private (commit) bytes
        if private is None:
            try:
                private = p.memory_full_info().uss
            except (psutil.AccessDenied, AttributeError):
                private = None
        if private is not None:
            sample["private_mb"] = round(private / 2**20, 1)
        if hasattr(p, "num_handles"):
            sample["handles"] = p.num_handles()
        elif hasattr(p, "num_fds"):
            sample["fds"] = p.num_fds()
    vm = psutil.virtual_memory()
    sample["system_available_mb"] = round(vm.available / 2**20)
    sample["system_available_ratio"] = round(vm.available / vm.total, 4)
    commit = commit_memory()
    if commit:
        sample["commit_used_ratio"] = commit["commit_used_ratio"]
    return sample

def state_size(state: dict[str, Any]) -> dict[str, int]:
    """What a run carries in its graph state (each checkpoint re-saves the changed channels)."""
    chunks = state.get("evidence_chunks") or []
    return {"chunks": len(chunks),
            "chunk_chars": sum(len(c.get("text") or "") for c in chunks),
            "documents": len(state.get("acquired_documents") or []),
            "events": len(state.get("events") or []),
            "candidates": len(state.get("discovery_candidates") or [])}

def resource_logger(log_dir: Path, instance_id: str, max_mb: float = 5.0,
                    backups: int = 5) -> logging.Logger:
    """A size-rotated JSONL file of resource samples (routine data: bounded, may roll over)."""
    logger = logging.getLogger(f"research_worker.resources.{instance_id}")
    logger.propagate = False
    if not logger.handlers:
        (log_dir / "resources").mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "resources" / f"{safe_name(instance_id)}.jsonl", maxBytes=int(max_mb * 2**20),
            backupCount=backups, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

def heartbeat_path(log_dir: Path, instance_id: str) -> Path:
    return log_dir / "heartbeats" / f"{safe_name(instance_id)}.json"

def stop_path(log_dir: Path, instance_id: str) -> Path:
    return log_dir / "stop" / f"{safe_name(instance_id)}.stop"

class Monitor:
    """Samples resources every ``interval`` seconds while the worker runs.

    ``activity`` returns the worker's live state (active runs with stage, attempt and last
    trace/span; in-flight LLM calls and fetches; checkpoint sizes). With ``log_dir`` and
    ``instance_id`` the sample also goes to the heartbeat and resource files; ``on_sample``
    receives it too (e.g. the worker_instances row). ``stop`` is set when the stop file
    appears (checked every ``stop_poll`` seconds)."""

    def __init__(self, interval: float = 30.0,
                 in_flight: Callable[[], int] = lambda: 0, *,
                 activity: Callable[[], dict[str, Any]] | None = None,
                 log_dir: Path | None = None, instance_id: str | None = None,
                 on_sample: Callable[[dict[str, Any]], None] | None = None,
                 stop: asyncio.Event | None = None, stop_poll: float = 1.0,
                 resources_max_mb: float = 5.0, resources_backups: int = 5) -> None:
        self.interval = interval
        self.in_flight = in_flight
        self.activity = activity
        self.log_dir = log_dir
        self.instance_id = instance_id
        self.on_sample = on_sample
        self.stop_event = stop
        self.stop_poll = stop_poll
        self.peak_rss_mb = 0.0
        self.samples = 0
        self._task: asyncio.Task | None = None
        self._process = psutil.Process()
        self._process.cpu_percent(None)  # prime: the next call reports the interval's CPU
        self._resources = (resource_logger(log_dir, instance_id, resources_max_mb,
                                           resources_backups)
                           if log_dir is not None and instance_id else None)

    def sample(self) -> dict[str, Any]:
        s = process_sample(self._process)
        s["cpu_percent"] = self._process.cpu_percent(None)
        self.peak_rss_mb = max(self.peak_rss_mb, s["rss_mb"])
        s.update(peak_rss_mb=self.peak_rss_mb, in_flight=self.in_flight())
        if self.activity:
            try:
                s.update(self.activity())
            except Exception as exc:  # noqa: BLE001 - diagnostics never fail the worker
                s["activity_error"] = type(exc).__name__
        s["at"] = datetime.now(UTC).isoformat()
        return s

    def beat(self) -> dict[str, Any]:
        """Take one sample and publish it everywhere (log, files, callback)."""
        s = self.sample()
        self.samples += 1
        log.info("heartbeat %s", s)
        if self.log_dir is not None and self.instance_id:
            try:
                atomic_write_json(heartbeat_path(self.log_dir, self.instance_id),
                                  {"instance_id": self.instance_id, "pid": os.getpid(), **s})
            except OSError:
                log.warning("could not write the heartbeat file", exc_info=True)
        if self._resources is not None:
            self._resources.info(json.dumps(s, default=str))
        if self.on_sample:
            try:
                self.on_sample(s)
            except Exception:
                log.warning("heartbeat callback failed", exc_info=True)
        return s

    def stop_requested(self) -> bool:
        return (self.log_dir is not None and self.instance_id is not None
                and stop_path(self.log_dir, self.instance_id).exists())

    async def _loop(self) -> None:
        next_beat = 0.0
        loop = asyncio.get_running_loop()
        while True:
            if loop.time() >= next_beat:
                await asyncio.to_thread(self.beat)
                next_beat = loop.time() + self.interval
            if self.stop_event is not None and not self.stop_event.is_set() \
                    and self.stop_requested():
                log.warning("stop file found: stopping gracefully")
                self.stop_event.set()
            await asyncio.sleep(min(self.stop_poll, max(0.01, next_beat - loop.time())))

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("monitor stopped %s", self.sample())
