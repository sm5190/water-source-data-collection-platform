from typing import TypedDict

class GraphState(TypedDict, total=False):
    run_id: str
    # REPROCESS runs: the parent run whose stored claims/evidence this run re-validates (the
    # graph then starts at validate: no discovery, acquisition or model calls).
    reprocess_of: str
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
    # Re-extractions per entity-defining field (bounded; see agents/nodes.py::REEXTRACT_FIELDS)
    reextractions: dict[str, int]
    fields_needing_evidence: list[str]  # set by extract; non-empty routes back to discover
    discovery_rounds: int
    # Utilities related to the city, by role, with evidence (schemas/utility.py
    # UtilityRegistry), as of the start of the latest discovery round / before validation.
    utility_registry: dict
    # Hosts not fetched or site-searched again this run (commercial/directory hosts shown
    # irrelevant; never government, regulatory, academic, technical or database hosts).
    suppressed_hosts: list[str]
    # services/geo_match.py::geo_basis: APPROVED | DRAFT_EVALUATION | NAME_ONLY + signature
    geo_basis: dict[str, str]
    context_bundle_ids: dict[str, str]  # field_key -> persisted ContextBundle id
    claims: list[dict]
    # Normalization/resolution (services/normalization.py, services/resolution.py).
    validated_claim_uids: list[str]  # claims a VALIDATION event was emitted for
    resolution_log: list[dict]  # append-only (field, entity) decisions, with seq
    fields_requiring_review: list[str]  # CONFLICT / HITL_REQUIRED after resolution
    needs_human_review: bool
    review_reason: str | None
    # Inline field-level decisions (no review store): [{field_key, decision:
    # APPROVED|CORRECTED|REJECTED, value?}]. Production reviews go through the API instead.
    review_decisions: list[dict]
    review_round: int  # human review rounds completed (each one an interrupt + resume)
    # SupplyRecords, derived values and quality checks (schemas/records.py RecordSet).
    record_set: dict
    completion: str  # FINALIZED | FINALIZED_WITH_GAPS | INCOMPLETE
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
