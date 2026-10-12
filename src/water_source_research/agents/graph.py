from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from water_source_research.core.capabilities import Capability, available_capabilities
from water_source_research.core.config import get_settings
from water_source_research.discovery.service import DiscoveryService
from water_source_research.llm.gateway import LLMGateway, UnconfiguredGateway
from water_source_research.procedures.retrieval import ProcedureMemory
from water_source_research.procedures.usage import UsageTracker
from water_source_research.context_manager import ContextService
from water_source_research.services.field_state import load_field_definitions
from water_source_research.services.run_recorder import RunRecorder
from .state import GraphState
from .nodes import (
    acquire_node, human_review_node, make_discover_node, make_extract_node,
    make_finalize_node, make_plan_node, validate_node,
)

def route_after_validation(state: GraphState) -> str:
    if state.get("needs_human_review") or state.get("review_decisions"):
        return "human_review"
    return "finalize"

def make_route_after_extract(max_discovery_rounds: int):
    def route_after_extract(state: GraphState) -> str:
        # NO_SUFFICIENT_EVIDENCE fields go back to discovery until the round budget is spent;
        # validate then flags whatever is still missing for human review.
        if state.get("fields_needing_evidence") and (
            state.get("discovery_rounds", 0) < max_discovery_rounds
        ):
            return "discover"
        return "validate"
    return route_after_extract

def build_graph(
    gateway: LLMGateway | None = None,
    context_service: ContextService | None = None,
    max_discovery_rounds: int | None = None,
    procedure_memory: ProcedureMemory | None = None,
    usage_tracker: UsageTracker | None = None,
    run_recorder: RunRecorder | None = None,
    capability_availability: frozenset[Capability] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    discovery: DiscoveryService | None = None,
):
    """Procedure memory, usage tracking and run recording are optional soft dependencies:
    without them the graph runs its default strategy and skips post-processing.
    Without ``discovery`` (see ``build_discovery_service``) the discover step finds nothing."""
    settings = get_settings()
    if max_discovery_rounds is None:
        max_discovery_rounds = settings.max_discovery_rounds_per_city
    if procedure_memory is not None and usage_tracker is None:
        usage_tracker = UsageTracker(procedure_memory.repo, procedure_memory.settings)
    if capability_availability is None:
        capability_availability = available_capabilities(settings)
    fields = load_field_definitions()

    g = StateGraph(GraphState)
    g.add_node("plan", make_plan_node(fields, procedure_memory, usage_tracker,
                                      capability_availability))
    g.add_node("discover", make_discover_node(usage_tracker, discovery, fields))
    g.add_node("acquire", acquire_node)
    g.add_node("extract", make_extract_node(
        context_service or ContextService(), gateway or UnconfiguredGateway(),
    ))
    g.add_node("validate", validate_node)
    g.add_node("human_review", human_review_node)
    g.add_node("finalize", make_finalize_node(fields, run_recorder, usage_tracker))
    g.add_edge(START, "plan")
    g.add_edge("plan", "discover")
    g.add_edge("discover", "acquire")
    g.add_edge("acquire", "extract")
    g.add_conditional_edges("extract", make_route_after_extract(max_discovery_rounds), {
        "discover": "discover",
        "validate": "validate",
    })
    g.add_conditional_edges("validate", route_after_validation, {
        "human_review": "human_review",
        "finalize": "finalize",
    })
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
