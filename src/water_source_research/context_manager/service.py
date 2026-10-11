"""Graph-facing entry point to the Context Manager: (run_id, field_key, operation) -> bundle.

Run state is carried in LangGraph state (``research_state``, ``evidence_chunks``,
``seen_evidence``); "load state" means validating and deserializing it from there.
"""

import asyncio
from collections.abc import Mapping
from typing import Any
from water_source_research.observability.otel import get_tracer
from water_source_research.schemas.research import CityResearchState, EvidenceChunk
from .manager import ContextBundle, ContextManager, ContextPurpose, ContextTask
from .store import ContextBundleStore

class ContextService:
    def __init__(
        self,
        manager: ContextManager | None = None,
        store: ContextBundleStore | None = None,
    ) -> None:
        self.manager = manager or ContextManager()
        self.store = store  # None: bundles are built per call and not persisted

    async def get_context(
        self,
        *,
        run_id: str,
        field_key: str,
        operation: ContextPurpose | str,
        graph_state: Mapping[str, Any],
    ) -> ContextBundle:
        operation = ContextPurpose(operation)
        tracer = get_tracer()
        with tracer.start_as_current_span("context.get_context") as span:
            span.set_attribute("context.run_id", run_id)
            span.set_attribute("context.field_key", field_key)
            span.set_attribute("context.operation", operation.value)

            with tracer.start_as_current_span("context.load_state") as load_span:
                state, chunks, seen = load_run_state(graph_state, run_id)
                load_span.set_attribute("context.run_id", run_id)
                load_span.set_attribute("context.chunks_available", len(chunks))
                load_span.set_attribute("context.seen_pairs", len(seen))

            task = ContextTask(operation, [field_key])
            if self.store is not None:
                bundle = await self.store.aget_or_build(self.manager, task, state, chunks, seen)
            else:
                with tracer.start_as_current_span("context.bundle_create") as create_span:
                    bundle = await asyncio.to_thread(self.manager.build, task, state, chunks,
                                                     seen)
                    create_span.set_attribute("context.cache_hit", False)
                    create_span.set_attribute("context.status", bundle.status.value)

            span.set_attribute("context.status", bundle.status.value)
            span.set_attribute("context.bundle_id", bundle.bundle_id or "")
            return bundle

def load_run_state(
    graph_state: Mapping[str, Any], run_id: str
) -> tuple[CityResearchState, list[EvidenceChunk], set[tuple[str, str]]]:
    if graph_state.get("run_id") != run_id:
        raise ValueError(f"Graph state belongs to run {graph_state.get('run_id')!r}, not {run_id!r}")
    if "research_state" not in graph_state:
        raise ValueError(f"Run {run_id!r} has no research_state in graph state")
    state = CityResearchState.model_validate(graph_state["research_state"])
    chunks = [EvidenceChunk.model_validate(c) for c in graph_state.get("evidence_chunks", [])]
    seen = {(c, k) for c, k in graph_state.get("seen_evidence", [])}
    return state, chunks, seen
