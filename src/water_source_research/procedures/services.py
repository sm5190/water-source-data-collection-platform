"""Wiring for procedural memory (used by the API and the worker script)."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from sqlalchemy import Engine
from water_source_research.core.config import Settings
from water_source_research.llm.embeddings import EmbeddingClient, OpenAIEmbeddingClient
from water_source_research.llm.openai_reasoner import OpenAIStructuredClient, StructuredLLM
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.field_state import load_field_definitions
from water_source_research.services.run_recorder import RunRecorder
from .distiller import DeterministicDistiller, Distiller
from .lifecycle import LifecycleManager, LifecyclePolicy
from .llm_distiller import LLMDistiller
from .repository import ProcedureRepository
from .retrieval import ProcedureMemory
from .usage import UsageTracker
from .worker import DistillationWorker

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class ProcedureServices:
    repo: ProcedureRepository
    memory: ProcedureMemory
    usage: UsageTracker
    recorder: RunRecorder
    worker: DistillationWorker
    lifecycle: LifecycleManager

def build_distiller(settings: Settings, fields: Mapping[str, FieldDefinition],
                    llm: StructuredLLM | None = None) -> Distiller:
    pm = settings.procedure_memory
    if pm.distiller == "llm":
        if llm is None and settings.openai_api_key:
            llm = OpenAIStructuredClient(settings.openai_api_key,
                                         pm.distiller_model or settings.openai_model)
        if llm is not None:
            return LLMDistiller(llm, fields, pm)
        logger.warning("procedure_memory.distiller=llm but no OpenAI key is configured; "
                       "using the deterministic distiller")
    return DeterministicDistiller(fields, pm)

def build_procedure_services(settings: Settings, engine: Engine,
                             embedder: EmbeddingClient | None = None,
                             llm: StructuredLLM | None = None) -> ProcedureServices:
    pm = settings.procedure_memory
    if embedder is None and settings.openai_api_key:
        embedder = OpenAIEmbeddingClient(settings.openai_api_key, pm.embedding_model)
    # Without an embedder, retrieval ranks on metadata + reliability (vector_degraded).
    fields = load_field_definitions()
    repo = ProcedureRepository(engine)
    distiller = build_distiller(settings, fields, llm)
    return ProcedureServices(
        repo=repo,
        memory=ProcedureMemory(repo, embedder, pm),
        usage=UsageTracker(repo, pm),
        recorder=RunRecorder(engine, fields, distiller.version),
        worker=DistillationWorker(repo, distiller, embedder, pm, fields),
        lifecycle=LifecycleManager(repo, LifecyclePolicy(pm)),
    )
