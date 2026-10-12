"""The production graph wiring, shared by the research worker and the live smoke test.

Every dependency is real: discovery providers, safe HTTP/PDF acquisition with raw files in
the artifact store, the context manager with persisted bundles, the extraction gateway on
the configured LLM provider, per-run evidence/claims persistence, and run/event recording
(always; distillation jobs only when procedure memory is enabled).
"""

from typing import Any
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import Engine
from water_source_research.acquisition.artifacts import ArtifactStore, build_artifact_store
from water_source_research.acquisition.service import build_acquisition_service
from water_source_research.context_manager import ContextBundleStore, ContextService
from water_source_research.core.config import Settings
from water_source_research.discovery.service import build_discovery_service
from water_source_research.llm.extraction import build_extraction_gateway
from water_source_research.procedures.services import ProcedureServices
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.run_recorder import RunRecorder
from .graph import build_graph

def build_production_graph(
    settings: Settings,
    engine: Engine,
    checkpointer: BaseCheckpointSaver | None = None,
    procedures: ProcedureServices | None = None,
    artifact_store: ArtifactStore | None = None,
    gateway: Any = None,
):
    if artifact_store is None:
        artifact_store = build_artifact_store(settings)
    recorder = procedures.recorder if procedures else RunRecorder(
        engine, enqueue_distillation_jobs=False)
    return build_graph(
        gateway=gateway if gateway is not None else build_extraction_gateway(settings),
        context_service=ContextService(store=ContextBundleStore(engine)),
        procedure_memory=procedures.memory if procedures else None,
        usage_tracker=procedures.usage if procedures else None,
        run_recorder=recorder,
        checkpointer=checkpointer,
        discovery=build_discovery_service(settings),
        acquisition=build_acquisition_service(settings, artifact_store),
        evidence_store=EvidenceRepository(engine),
    )
