"""Wiring for procedural memory (used by the API and the worker script)."""

from dataclasses import dataclass
from sqlalchemy import Engine
from water_source_research.core.config import Settings
from water_source_research.llm.embeddings import EmbeddingClient, OpenAIEmbeddingClient
from water_source_research.services.field_state import load_field_definitions
from water_source_research.services.run_recorder import RunRecorder
from .distiller import DeterministicDistiller
from .lifecycle import LifecycleManager, LifecyclePolicy
from .repository import ProcedureRepository
from .retrieval import ProcedureMemory
from .usage import UsageTracker
from .worker import DistillationWorker

@dataclass(slots=True)
class ProcedureServices:
    repo: ProcedureRepository
    memory: ProcedureMemory
    usage: UsageTracker
    recorder: RunRecorder
    worker: DistillationWorker
    lifecycle: LifecycleManager

def build_procedure_services(settings: Settings, engine: Engine,
                             embedder: EmbeddingClient | None = None) -> ProcedureServices:
    pm = settings.procedure_memory
    if embedder is None and settings.openai_api_key:
        embedder = OpenAIEmbeddingClient(settings.openai_api_key, pm.embedding_model)
    # Without an embedder, retrieval ranks on metadata + reliability (vector_degraded).
    fields = load_field_definitions()
    repo = ProcedureRepository(engine)
    return ProcedureServices(
        repo=repo,
        memory=ProcedureMemory(repo, embedder, pm),
        usage=UsageTracker(repo, pm),
        recorder=RunRecorder(engine, fields),
        worker=DistillationWorker(repo, DeterministicDistiller(fields, pm), embedder, pm, fields),
        lifecycle=LifecycleManager(repo, LifecyclePolicy(pm)),
    )
