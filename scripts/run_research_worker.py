"""City-run worker: claims queued runs and executes the research graph.

Usage: python scripts/run_research_worker.py [--once] [--concurrency N]
Run several processes (or hosts) for more throughput; the database coordinates them.
Stops gracefully on Ctrl+C / SIGTERM (in-flight runs get shutdown_grace_seconds).
"""

import argparse
import asyncio
import logging
import signal
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.procedures.services import build_procedure_services
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
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
        graph = build_graph(
            procedure_memory=procedures.memory if procedures else None,
            usage_tracker=procedures.usage if procedures else None,
            run_recorder=procedures.recorder if procedures else None,
            checkpointer=checkpointer,
        )
        worker = ResearchWorker(ResearchQueue(engine, queue_settings), graph, queue_settings,
                                on_success=distill)
        log.info("worker %s started (concurrency=%d)", worker.worker_id,
                 queue_settings.worker_concurrency)
        if once:
            log.info("processed %d run(s)", await worker.drain())
            return
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        await worker.run(stop)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()
    run_async(main(args.once, args.concurrency))
