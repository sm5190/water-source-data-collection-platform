from typing import TypedDict

class GraphState(TypedDict, total=False):
    run_id: str
    city_name: str
    country: str | None
    research_state: dict  # serialized CityResearchState; the run's field-level state
    unresolved_fields: list[str]
    candidate_urls: list[str]  # ranked URLs of discovery_candidates
    # Discovery: serialized discovery.base.SearchResult, deduped and ranked across rounds.
    discovery_candidates: list[dict]
    discovery_provider_status: dict[str, str]  # provider -> ok | no_results | <error kind>
    # Acquisition: one record per attempted URL (any status); de-duplication across rounds
    # and provenance (source type, acquisition method, source date, raw object key).
    acquired_documents: list[dict]
    evidence_chunks: list[dict]  # serialized EvidenceChunk (fetched document text only)
    seen_evidence: list[list[str]]  # [chunk_id, field_key] pairs already sent to the LLM
    fields_needing_evidence: list[str]  # set by extract; non-empty routes back to discover
    discovery_rounds: int
    context_bundle_ids: dict[str, str]  # field_key -> persisted ContextBundle id
    claims: list[dict]
    needs_human_review: bool
    review_reason: str | None
    review_decisions: list[dict]  # [{field_key, decision: APPROVED|CORRECTED|REJECTED, ...}]
    finalized: bool
    # Trusted workflow events (schemas.events.WorkflowEvent); the only distillation input.
    events: list[dict]
    # Procedural memory: advisory planning guidance, never evidence.
    procedure_guidance: dict[str, list[dict]]  # field_type -> compact matches
    procedure_selections: list[dict]
    procedure_memory_degraded: bool
    planned_queries: dict[str, list[str]]  # field_type -> rendered query templates
    # Soft post-processing (never affects the scientific result).
    distillation_jobs: list[str]
    post_processing_errors: list[str]
