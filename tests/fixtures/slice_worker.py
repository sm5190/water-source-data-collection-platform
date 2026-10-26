"""A real worker process for the monitoring end-to-end test: the production worker loop,
queue, Postgres checkpointer, event sink, Monitor heartbeat (with span-export and usage-ledger
counters), telemetry setup and usage ledger, running the mocked Toronto slice (mocked web, a
scripted model behind the real LLM router, so model calls reach the ledger).

``--mode slow``: the acquire step hangs, so the test can kill the process mid-run.
``--mode serve``: works normally and keeps running until its stop file appears.
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ helpers

from acquisition_helpers import make_acquisition
from slice_helpers import ModelScript, make_discovery, make_gateway, make_site
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.observability.otel import (
    configure_telemetry, exporter_stats, shutdown_telemetry,
)
from water_source_research.observability.usage import configure_usage
from water_source_research.observability.usage import recorder
from water_source_research.observability.worker_events import build_sink
from water_source_research.research_queue.checkpointing import (
    open_checkpointer, run_async,
)
from water_source_research.research_queue.diagnostics import (
    Monitor, configure_worker_logging,
)
from water_source_research.research_queue.queue import ResearchQueue
from water_source_research.research_queue.worker import ResearchWorker

async def main(a) -> None:
    settings = get_settings()
    engine = get_engine()
    configure_usage(engine, settings)
    sink = build_sink(a.log_dir, a.instance_id, engine)
    sink.instance(a.instance_id, supervisor_id=a.supervisor_id, pid=os.getpid(),
                  started_at=datetime.now(UTC))
    acquisition = make_acquisition(make_site())
    if a.mode == "slow":
        original = acquisition.acquire_round

        async def hang(*args, **kwargs):
            await asyncio.sleep(120)  # killed long before this returns
            return await original(*args, **kwargs)
        acquisition.acquire_round = hang
    gateway, _ = make_gateway(ModelScript(), structured_max_attempts=1)
    async with open_checkpointer(settings, pool_size=3) as saver:
        graph = build_graph(gateway=gateway, discovery=make_discovery(),
                            acquisition=acquisition, max_discovery_rounds=2,
                            checkpointer=saver)
        worker = ResearchWorker(ResearchQueue(engine, settings.research_queue), graph,
                                settings.research_queue, worker_id=a.instance_id,
                                events=sink)
        stop = asyncio.Event()

        def activity() -> dict:
            ledger = recorder()
            return {**worker.activity(), "telemetry": exporter_stats(),
                    **(ledger.stats() if ledger else {})}

        def on_sample(sample: dict) -> None:
            sink.instance(a.instance_id, last_heartbeat_at=datetime.now(UTC),
                          last_sample=sample, active_run_ids=sorted(worker.active))

        monitor = Monitor(0.2, activity=activity, log_dir=a.log_dir,
                          instance_id=a.instance_id, on_sample=on_sample, stop=stop)
        monitor.start()
        try:
            await worker.run(stop)
        finally:
            await monitor.stop()
            sink.instance(a.instance_id, stopped_at=datetime.now(UTC))
            ledger = recorder()
            if ledger:
                ledger.flush()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--log-dir", type=Path)
    p.add_argument("--instance-id")
    p.add_argument("--supervisor-id")
    p.add_argument("--mode", default="serve")
    args = p.parse_args()
    configure_worker_logging(args.log_dir)
    configure_telemetry("worker", instance_id=args.instance_id)
    try:
        run_async(main(args))
    finally:
        shutdown_telemetry()
