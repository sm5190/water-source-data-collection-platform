"""City-run worker: claims queued runs and executes the research graph.

Usage: python scripts/run_research_worker.py [--once] [--concurrency N] [--log-dir logs]
Run several processes (or hosts) for more throughput; the database coordinates them.
Stops gracefully on Ctrl+C / SIGTERM / SIGBREAK (in-flight runs get shutdown_grace_seconds).

Diagnostics (research_queue/diagnostics.py): an append-only log file per start in --log-dir,
faulthandler tracebacks for native crashes, an exit-reason line, and a resource heartbeat
(memory, handles, in-flight runs) every 30 s. To record the real exit code of a killed worker,
run it under scripts/worker_supervised.ps1.
"""

import argparse
import asyncio
import logging
import signal
from pathlib import Path
from water_source_research.agents.runtime import build_production_graph
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.procedures.services import build_procedure_services
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.diagnostics import Monitor, configure_worker_logging
from water_source_research.research_queue.queue import ResearchQueue
from water_source_research.research_queue.worker import ResearchWorker

log = logging.getLogger("research_worker")

async def main(once: bool, concurrency: int | None) -> None:
    settings = get_settings()
    queue_settings = settings.research_queue
    if concurrency:
        queue_settings = queue_settings.model_copy(update={"worker_concurrency": concurrency})
    engine = get_engine()
    procedures = (build_procedure_services(settings, engine)
                  if settings.procedure_memory.enabled else None)

    def distill(run_id: str) -> None:  # soft post-run hook
        if procedures:
            procedures.worker.run_once(run_id=run_id)
            procedures.worker.embed_pending()

    async with open_checkpointer(settings) as checkpointer:
        graph = build_production_graph(settings, engine, checkpointer, procedures)
        worker = ResearchWorker(ResearchQueue(engine, queue_settings), graph, queue_settings,
                                on_success=distill)
        log.info("worker %s started (concurrency=%d)", worker.worker_id,
                 queue_settings.worker_concurrency)
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
        monitor = Monitor(30.0, in_flight=lambda: len(worker.in_flight))
        monitor.start()
        try:
            await worker.run(stop)
        finally:
            await monitor.stop()
            log.info("worker %s stopped (peak rss %.1f MB)", worker.worker_id,
                     monitor.peak_rss_mb)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    args = parser.parse_args()
    configure_worker_logging(args.log_dir)
    run_async(main(args.once, args.concurrency))
