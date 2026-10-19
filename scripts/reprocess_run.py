"""Reprocess a finished run: re-validate, resolve, review and assemble its stored claims and
evidence as a new run, without new discovery, fetching or model calls.

    uv run python scripts/reprocess_run.py <run_id>            # submit only (a worker runs it)
    uv run python scripts/reprocess_run.py <run_id> --process  # submit and run it here

With --process the run goes as far as it can: normally it stops at AWAITING_REVIEW, and the
review happens in the dashboard (Review queue tab); then resume it there and run a worker
(scripts/run_research_worker.py) to assemble and finalize. The parent run is not modified.
"""

import argparse
import sys
from water_source_research.agents.runtime import build_production_graph
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.research_queue.checkpointing import open_checkpointer, run_async
from water_source_research.research_queue.queue import JobStatus, ResearchQueue
from water_source_research.research_queue.worker import ResearchWorker
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.review import ReviewRepository

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", help="a SUCCEEDED run to reprocess")
    parser.add_argument("--process", action="store_true",
                        help="run the reprocessing here (otherwise a worker picks it up)")
    args = parser.parse_args()
    settings = get_settings()
    engine = get_engine()
    queue = ResearchQueue(engine, settings.research_queue)
    try:
        submitted = queue.submit_reprocess(args.run_id)
    except (LookupError, ValueError) as exc:
        print(f"cannot reprocess: {exc}")
        return 2
    job = submitted.job
    if not submitted.created:
        print(f"{job.city_name} already has an active run {job.run_id} ({job.status.value}); "
              "not submitting another")
        return 1
    print(f"submitted reprocess run {job.run_id} of {args.run_id} ({job.city_name})")
    if not args.process:
        return 0

    async def work() -> int:
        async with open_checkpointer(settings) as checkpointer:
            graph = build_production_graph(settings, engine, checkpointer)
            return await ResearchWorker(queue, graph, queue.s).drain()

    run_async(work())
    job = queue.get(job.run_id)
    counts = EvidenceRepository(engine).counts(job.run_id)
    print(f"run {job.run_id}: {job.status.value} (review round {job.review_round}, "
          f"completion {job.completion}) | {counts}")
    if job.status is JobStatus.AWAITING_REVIEW:
        items = ReviewRepository(engine).items(job.run_id, job.review_round, status="OPEN")
        print(f"\n{len(items)} item(s) to review (dashboard -> Review queue):")
        for i in items:
            print(f"  {i['field_key']:<45} {i['entity_key']:<20} {', '.join(i['reason_codes'])}")
    if job.status is JobStatus.FAILED:
        print(f"failed: {job.last_error}")
        return 6
    return 0

if __name__ == "__main__":
    sys.exit(main())
