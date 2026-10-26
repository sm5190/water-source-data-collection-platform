"""Run the research worker under the supervisor, or ask a running supervisor to stop.

    python scripts/worker_supervisor.py [run] [--concurrency 2] [--log-dir logs]
                                        [--no-windows-events] [--no-db]
    python scripts/worker_supervisor.py stop [--log-dir logs]
    python scripts/worker_supervisor.py sync [--log-dir logs]   # push local events to Postgres

The supervisor starts ``<venv python> scripts/run_research_worker.py`` directly (no shell or
uv layer in between), records how it ends in worker_events (local files under
<log-dir>/events first, then Postgres) and restarts it with backoff. ``stop`` asks for a
graceful stop: the worker drains its runs, exits 0 and the supervisor exits.
See observability/supervisor.py.
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from water_source_research.core.config import get_settings
from water_source_research.observability.supervisor import Supervisor, install_signal_handlers
from water_source_research.observability.worker_events import EventStore, EventSync

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", default="run", choices=["run", "stop", "sync"])
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--no-windows-events", action="store_true",
                        help="skip reading the Windows System event log at worker exit")
    parser.add_argument("--no-db", action="store_true",
                        help="record events in local files only (sync later)")
    args = parser.parse_args()
    settings = get_settings()
    log_dir = args.log_dir or Path(settings.observability.log_dir)
    if not log_dir.is_absolute():
        log_dir = ROOT / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.action == "stop":
        path = log_dir / "stop" / "supervisor.stop"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        print(f"stop requested ({path}); the worker drains its runs, then both exit")
        return 0

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "worker-supervisor.log", encoding="utf-8"),
                  logging.StreamHandler()])
    engine = None
    if not args.no_db:
        from water_source_research.db.session import get_engine
        engine = get_engine()

    if args.action == "sync":
        if engine is None:
            print("--no-db: nothing to sync to")
            return 2
        n = EventSync(log_dir / "events", EventStore(engine)).sync_pending()
        print(f"{n} event(s) inserted")
        return 0

    def command(instance_id: str, supervisor_id: str) -> list[str]:
        cmd = [sys.executable, str(ROOT / "scripts" / "run_research_worker.py"),
               "--log-dir", str(log_dir), "--instance-id", instance_id,
               "--supervisor-id", supervisor_id]
        if args.concurrency:
            cmd += ["--concurrency", str(args.concurrency)]
        return cmd

    supervisor = Supervisor(command=command, log_dir=log_dir, settings=settings.observability,
                            engine=engine, cwd=ROOT,
                            collect_windows_events=not args.no_windows_events)
    install_signal_handlers(supervisor)
    return supervisor.run()

if __name__ == "__main__":
    sys.exit(main())
