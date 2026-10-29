from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from water_source_research.acquisition.service import AcquisitionService
from water_source_research.core.capabilities import Capability, available_capabilities
from water_source_research.core.config import get_settings
from water_source_research.discovery.service import DiscoveryService
from water_source_research.llm.gateway import LLMGateway, UnconfiguredGateway
from water_source_research.procedures.retrieval import ProcedureMemory
from water_source_research.procedures.usage import UsageTracker
from water_source_research.context_manager import ContextService
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.field_state import load_field_definitions
from water_source_research.services.record_store import RecordRepository
from water_source_research.services.review import ReviewRepository, open_review_items
from water_source_research.services.run_recorder import RunRecorder
from water_source_research.schemas.research import CityResearchState, FieldDefinition
from .state import GraphState
from .nodes import (
    acquire_node, evidence_index, make_acquire_node, make_discover_node, make_extract_node,
    make_assemble_node, make_finalize_node, make_human_review_node, make_plan_node,
    make_resolve_node, make_validate_node,
)

def make_route_after_resolve(fields: dict[str, FieldDefinition], reviews: bool):
    """To human_review while a person has something to decide, else on to assemble.

    With a review store that is any open review item (the node then pauses the run); without
    one, only inline ``review_decisions`` to apply."""
    def route_after_resolve(state: GraphState) -> str:
        if not reviews:
            return "human_review" if state.get("review_decisions") else "assemble"
        research = CityResearchState.model_validate(state["research_state"])
        open_items = open_review_items(state.get("run_id", ""), 0, research, fields,
                                       state.get("unresolved_fields"),
                                       state.get("fields_needing_evidence"),
                                       evidence_index(state))
        return "human_review" if open_items else "assemble"
    return route_after_resolve

def route_start(state: GraphState) -> str:
    """Reprocessing a stored run starts at validate; everything else at plan."""
    return "validate" if state.get("reprocess_of") else "plan"

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
    acquisition: AcquisitionService | None = None,
    evidence_store: EvidenceRepository | None = None,
    review_store: ReviewRepository | None = None,
    record_store: RecordRepository | None = None,
):
    """Procedure memory, usage tracking and run recording are optional soft dependencies:
    without them the graph runs its default strategy and skips post-processing.
    Without ``discovery`` (see ``build_discovery_service``) the discover step finds nothing;
    without ``acquisition`` (``build_acquisition_service``) acquire fetches nothing and only
    caller-supplied ``evidence_chunks`` are used. ``evidence_store`` persists documents,
    chunks, claims and field states; once given it is a hard dependency (a failed write
    fails the run, which the research queue retries). ``review_store`` turns on persistent
    human review: the run pauses (interrupt) on open review items, so it needs a
    ``checkpointer``; without it, only inline ``review_decisions`` are applied."""
    if review_store is not None and checkpointer is None:
        raise ValueError("review_store needs a checkpointer (review pauses the run)")
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
    g.add_node("discover", make_discover_node(usage_tracker, discovery, fields,
                                              evidence_store))
    g.add_node("acquire", make_acquire_node(acquisition, evidence_store)
               if acquisition is not None else acquire_node)
    g.add_node("extract", make_extract_node(
        context_service or ContextService(), gateway or UnconfiguredGateway(),
        skip_no_new_entity=settings.extraction.skip_no_new_entity,
    ))
    g.add_node("validate", make_validate_node(fields))
    g.add_node("resolve", make_resolve_node(fields))
    g.add_node("human_review", make_human_review_node(fields, review_store, evidence_store))
    g.add_node("assemble", make_assemble_node(fields))
    g.add_node("finalize", make_finalize_node(fields, run_recorder, usage_tracker,
                                              evidence_store, record_store))
    g.add_conditional_edges(START, route_start, {"plan": "plan", "validate": "validate"})
    g.add_edge("plan", "discover")
    g.add_edge("discover", "acquire")
    g.add_edge("acquire", "extract")
    g.add_conditional_edges("extract", make_route_after_extract(max_discovery_rounds), {
        "discover": "discover",
        "validate": "validate",
    })
    g.add_edge("validate", "resolve")
    g.add_conditional_edges("resolve", make_route_after_resolve(
        fields, review_store is not None), {
        "human_review": "human_review",
        "assemble": "assemble",
    })
    g.add_edge("human_review", "resolve")
    g.add_edge("assemble", "finalize")  # rules re-resolve what reviewers sent back
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
