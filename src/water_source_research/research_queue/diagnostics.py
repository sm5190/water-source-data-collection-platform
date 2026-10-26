"""Worker diagnostics: persistent logs, process resource samples and graph-state size.

Evidence for questions like "did the worker run out of memory?" — collected, never assumed:
  - ``configure_worker_logging`` writes an append-only, timestamped log file per worker start
    (never overwritten), enables ``faulthandler`` (native crashes dump a traceback into it),
    and logs an exit line for normal exits, uncaught exceptions and console signals;
  - ``process_sample`` reads the process's memory (RSS and private bytes), handles/fds and
    threads (psutil);
  - ``state_size`` measures what a run carries in its graph state (and so in each checkpoint);
  - ``Monitor`` logs a heartbeat with both every ``interval`` seconds.
The real exit code of a killed process can't be seen from inside it: run the worker under
``scripts/worker_supervised.ps1``, which records it.
"""

import asyncio
import atexit
import faulthandler
import logging
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
import psutil

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
        logging.getLogger("research_worker").critical(
            "worker exiting on an uncaught exception", exc_info=(kind, value, tb))
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
    """This process's memory and resource use."""
    p = process or psutil.Process()
    with p.oneshot():
        mem = p.memory_info()
        sample: dict[str, Any] = {"rss_mb": round(mem.rss / 2**20, 1),
                                  "threads": p.num_threads()}
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
    return sample

def state_size(state: dict[str, Any]) -> dict[str, int]:
    """What a run carries in its graph state (each checkpoint re-saves the changed channels)."""
    chunks = state.get("evidence_chunks") or []
    return {"chunks": len(chunks),
            "chunk_chars": sum(len(c.get("text") or "") for c in chunks),
            "documents": len(state.get("acquired_documents") or []),
            "events": len(state.get("events") or []),
            "candidates": len(state.get("discovery_candidates") or [])}

class Monitor:
    """Logs a resource heartbeat every ``interval`` seconds while the worker runs."""

    def __init__(self, interval: float = 30.0,
                 in_flight: Callable[[], int] = lambda: 0) -> None:
        self.interval = interval
        self.in_flight = in_flight
        self.peak_rss_mb = 0.0
        self._task: asyncio.Task | None = None

    def sample(self) -> dict[str, Any]:
        s = process_sample()
        self.peak_rss_mb = max(self.peak_rss_mb, s["rss_mb"])
        return {**s, "peak_rss_mb": self.peak_rss_mb, "in_flight": self.in_flight()}

    async def _loop(self) -> None:
        while True:
            log.info("heartbeat %s", self.sample())
            await asyncio.sleep(self.interval)

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
