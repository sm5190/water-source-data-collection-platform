"""City-run worker: claims queued runs and executes the research graph.

Usage: python scripts/run_research_worker.py [--once] [--concurrency N] [--log-dir logs]
           [--instance-id ID] [--supervisor-id ID]
Run several processes (or hosts) for more throughput; the database coordinates them.
Stops gracefully on Ctrl+C / SIGTERM / SIGBREAK, or when its stop file
(<log-dir>/stop/<instance>.stop) appears; in-flight runs get shutdown_grace_seconds.

Diagnostics (research_queue/diagnostics.py): an append-only log file per start in --log-dir,
faulthandler tracebacks for native crashes, an exit-reason line, and a resource sample every
OBSERVABILITY__SAMPLE_SECONDS (log line, heartbeat file, rotating resource file and the
worker_instances row). Recovery events (lease reclaimed, checkpoint resume, outcome) go to
worker_events. Run it under scripts/worker_supervisor.py to record how the process ends.
"""

import argparse
import asyncio
import logging
import os
import signal
import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from water_source_research.acquisition.service import fetches_in_flight
from water_source_research.agents.runtime import build_production_graph
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.llm.router import llm_requests_in_flight
from water_source_research.observability.worker_events import (
    EventKind, atomic_write_json, build_sink,
)
from water_source_research.procedures.services import build_procedure_services
from water_source_research.research_queue.checkpointing import (
    measure_checkpoint_writes, open_checkpointer, run_async,
)
from water_source_research.research_queue.diagnostics import (
    Monitor, configure_worker_logging, heartbeat_path,
)
from water_source_research.research_queue.queue import ResearchQueue
from water_source_research.research_queue.worker import ResearchWorker

log = logging.getLogger("research_worker")

async def main(once: bool, concurrency: int | None, log_dir: Path, instance_id: str,
               supervisor_id: str | None) -> None:
    settings = get_settings()
    obs = settings.observability
    queue_settings = settings.research_queue
    if concurrency:
        queue_settings = queue_settings.model_copy(update={"worker_concurrency": concurrency})
    engine = get_engine()
    sink = build_sink(log_dir, instance_id, engine, obs.events_max_mb)
    procedures = (build_procedure_services(settings, engine)
                  if settings.procedure_memory.enabled else None)
    supervised = supervisor_id is not None
    started_at = datetime.now(UTC)
    # First heartbeat before the slow startup (graph build, checkpointer setup): tells the
    # supervisor this interpreter's pid (a venv launcher may sit in between) and its log.
    atomic_write_json(heartbeat_path(log_dir, instance_id),
                      {"instance_id": instance_id, "pid": os.getpid(),
                       "at": started_at.isoformat(), "stage": "starting", "active_runs": {}})
    sink.instance(instance_id, supervisor_id=supervisor_id, host=socket.gethostname(),
                  pid=os.getpid(), started_at=started_at)
    if not supervised:  # supervised: the supervisor records start and end (it outlives us)
        sink.emit(EventKind.WORKER_STARTED, "worker", instance_id=instance_id, pid=os.getpid(),
                  facts={"concurrency": queue_settings.worker_concurrency, "supervised": False})

    def distill(run_id: str) -> None:  # soft post-run hook
        if procedures:
            procedures.worker.run_once(run_id=run_id)
            procedures.worker.embed_pending()

    async with open_checkpointer(settings) as checkpointer:
        checkpoint_stats = measure_checkpoint_writes(checkpointer)
        graph = build_production_graph(settings, engine, checkpointer, procedures)
        worker = ResearchWorker(ResearchQueue(engine, queue_settings), graph, queue_settings,
                                worker_id=instance_id, on_success=distill, events=sink)
        log.info("worker %s started (concurrency=%d, supervisor=%s)", worker.worker_id,
                 queue_settings.worker_concurrency, supervisor_id)
        if once:
            log.info("processed %d run(s)", await worker.drain())
            return
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def on_signal(sig: int) -> None:
            log.warning("signal %s received: stopping gracefully", signal.Signals(sig).name)
            loop.call_soon_threadsafe(stop.set)

        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):  # Windows console close / Ctrl+Break
            signals.append(signal.SIGBREAK)
        for sig in signals:
            try:
                loop.add_signal_handler(sig, on_signal, sig)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda s, _: on_signal(s))

        def activity() -> dict:
            return {**worker.activity(), "llm_requests_in_flight": llm_requests_in_flight(),
                    "fetches_in_flight": fetches_in_flight(), **checkpoint_stats.snapshot()}

        def on_sample(sample: dict) -> None:
            sink.instance(instance_id, last_heartbeat_at=datetime.now(UTC), last_sample=sample,
                          active_run_ids=sorted(worker.active))

        monitor = Monitor(obs.sample_seconds, in_flight=lambda: len(worker.in_flight),
                          activity=activity, log_dir=log_dir, instance_id=instance_id,
                          on_sample=on_sample, stop=stop,
                          resources_max_mb=obs.resources_max_mb,
                          resources_backups=obs.resources_backups)
        monitor.start()
        try:
            await worker.run(stop)
        finally:
            await monitor.stop()
            log.info("worker %s stopped (peak rss %.1f MB)", worker.worker_id,
                     monitor.peak_rss_mb)
            sink.instance(instance_id, stopped_at=datetime.now(UTC))
            if not supervised:
                sink.emit(EventKind.WORKER_STOPPED, "worker", instance_id=instance_id,
                          pid=os.getpid(), facts={"peak_rss_mb": monitor.peak_rss_mb})

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--instance-id", default=None,
                        help="worker identity (also the lease owner); default: generated")
    parser.add_argument("--supervisor-id", default=None)
    args = parser.parse_args()
    log_dir = args.log_dir or Path(get_settings().observability.log_dir)
    instance = args.instance_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:6]}"
    configure_worker_logging(log_dir)
    run_async(main(args.once, args.concurrency, log_dir, instance, args.supervisor_id))
