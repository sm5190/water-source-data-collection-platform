"""Procedural-memory metrics.

Dimensions are deliberately low-cardinality (field_type, task_type, status, result,
strategy). Run/procedure IDs go on spans, never on metrics.
"""

from water_source_research.observability.otel import get_meter

_meter = get_meter()

retrieval_duration_ms = _meter.create_histogram(
    "procedure.retrieval.duration", unit="ms", description="Procedure retrieval latency")
retrieval_requests = _meter.create_counter(
    "procedure.retrieval.requests",
    description="Retrievals by outcome (matched | no_match | degraded)")
procedures_selected = _meter.create_counter(
    "procedure.selected", description="Procedure matches chosen by the planner")
strategy_decisions = _meter.create_counter(
    "research.field.strategy", description="Per-field planning strategy (procedure | default)")
field_outcomes = _meter.create_counter(
    "research.field.outcome",
    description="Field outcomes by strategy, resolution and human review/correction")
field_tool_calls = _meter.create_histogram(
    "research.field.tool_calls", description="Tool calls per field, by strategy")
field_search_rounds = _meter.create_histogram(
    "research.field.search_rounds", description="Search rounds per field, by strategy")
distillation_results = _meter.create_counter(
    "procedure.distillation.results",
    description="Distillation jobs by result (succeeded/merged/ineligible/rejected/failed)")
lifecycle_transitions = _meter.create_counter(
    "procedure.lifecycle.transitions", description="Procedure version status transitions")
post_processing_errors = _meter.create_counter(
    "research.post_processing.errors",
    description="Soft post-processing failures (never change the scientific result)")
