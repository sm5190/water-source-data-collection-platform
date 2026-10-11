"""Queue metrics. Low-cardinality dimensions only (status/created); run IDs go on spans."""

from water_source_research.observability.otel import get_meter

_meter = get_meter()

jobs_submitted = _meter.create_counter(
    "research_queue.jobs.submitted", description="Submissions (created=false: deduplicated)")
jobs_claimed = _meter.create_counter("research_queue.jobs.claimed",
                                     description="Jobs claimed by workers")
jobs_finished = _meter.create_counter(
    "research_queue.jobs.finished",
    description="Job state changes out of RUNNING (SUCCEEDED/QUEUED retry/FAILED/CANCELLED)")
queue_wait_seconds = _meter.create_histogram(
    "research_queue.wait", unit="s", description="Time from submission to claim")
run_duration_seconds = _meter.create_histogram(
    "research_queue.run.duration", unit="s", description="Graph execution time per attempt")
runs_resumed = _meter.create_counter(
    "research_queue.runs.resumed", description="Attempts that resumed from a graph checkpoint")
