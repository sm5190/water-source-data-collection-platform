"""A real ResearchWorker process for the end-to-end crash/trace test: the production worker
loop, queue, Postgres checkpointer, event sink, Monitor heartbeat and telemetry setup, on the
offline stub graph (no discovery, fetching or model calls). ``--mode slow`` makes acquire
hang so the test can kill the process mid-run; ``--mode normal`` drains the queue and exits.
"""

import argparse
import asyncio
from pathlib import Path
from water_source_research.agents import graph as graph_module
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.observability.otel import configure_telemetry, shutdown_telemetry
from water_source_research.observability.worker_events import build_sink
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.diagnostics import Monitor, configure_worker_logging
from water_source_research.research_queue.queue import ResearchQueue
from water_source_research.research_queue.worker import ResearchWorker

async def main(a) -> None:
    settings = get_settings()
    engine = get_engine()
    if a.mode == "slow":
        original = graph_module.acquire_node

        async def slow_acquire(state):
            await asyncio.sleep(120)  # killed long before this returns
            return await original(state)
        graph_module.acquire_node = slow_acquire
    async with open_checkpointer(settings, pool_size=3) as saver:
        graph = build_graph(max_discovery_rounds=1, checkpointer=saver)
        worker = ResearchWorker(ResearchQueue(engine, settings.research_queue), graph,
                                settings.research_queue, worker_id=a.instance_id,
                                events=build_sink(a.log_dir, a.instance_id, engine))
        stop = asyncio.Event()
        monitor = Monitor(0.2, activity=worker.activity, log_dir=a.log_dir,
                          instance_id=a.instance_id, stop=stop)
        monitor.start()
        try:
            if a.mode == "slow":
                await worker.run(stop)
            else:
                await worker.drain()
        finally:
            await monitor.stop()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--log-dir", type=Path)
    p.add_argument("--instance-id")
    p.add_argument("--supervisor-id")
    p.add_argument("--mode", default="normal")
    args = p.parse_args()
    configure_worker_logging(args.log_dir)
    configure_telemetry("worker", instance_id=args.instance_id)
    try:
        run_async(main(args))
    finally:
        shutdown_telemetry()
