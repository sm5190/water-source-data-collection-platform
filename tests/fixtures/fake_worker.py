"""A stand-in research worker for supervisor tests: same command-line contract, same log /
heartbeat / stop-file conventions, scripted ending (``--mode``).

Modes: clean (beat until the stop file, exit 0), exit:<code> (beat, then os._exit(code): no
Python cleanup at all), hang (one beat, then silent but alive), ignore-stop (beat, never
exits on its own), exception:<Type> (an uncaught exception through the real excepthook),
active (a heartbeat listing a run, then os._exit(1)), quick (exit 0 at once).
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from water_source_research.observability.worker_events import atomic_write_json
from water_source_research.research_queue.diagnostics import (
    configure_worker_logging, heartbeat_path, stop_path,
)

def beat(log_dir: Path, instance: str, active: dict | None = None) -> None:
    atomic_write_json(heartbeat_path(log_dir, instance),
                      {"instance_id": instance, "pid": os.getpid(), "rss_mb": 42.0,
                       "at": datetime.now(UTC).isoformat(), "active_runs": active or {}})

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--log-dir", type=Path)
    p.add_argument("--instance-id")
    p.add_argument("--supervisor-id")
    p.add_argument("--mode", default="clean")
    p.add_argument("--interval", type=float, default=0.1)
    a = p.parse_args()
    configure_worker_logging(a.log_dir)
    mode, _, arg = a.mode.partition(":")
    if mode == "quick":
        return
    if mode == "active":
        beat(a.log_dir, a.instance_id, {"run-abc": {"stage": "acquire", "attempt": 1,
                                                     "trace_id": "ab" * 16,
                                                     "span_id": "cd" * 8}})
        time.sleep(0.3)
        os._exit(1)
    beat(a.log_dir, a.instance_id)
    if mode == "exit":
        time.sleep(0.3)
        os._exit(int(arg))
    if mode == "exception":
        time.sleep(0.2)
        raise {"ConnectionRefusedError": ConnectionRefusedError,
               "MemoryError": MemoryError}.get(arg, RuntimeError)("scripted failure")
    deadline = time.time() + 120
    while time.time() < deadline:
        if mode != "hang":
            beat(a.log_dir, a.instance_id)
        if mode == "clean" and stop_path(a.log_dir, a.instance_id).exists():
            import logging
            logging.getLogger("research_worker").info("worker %s stopped", a.instance_id)
            return
        time.sleep(a.interval)
    sys.exit(9)

if __name__ == "__main__":
    main()
