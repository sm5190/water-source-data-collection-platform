"""Procedure retrieval: metadata filter -> similarity over survivors -> ranking -> top-N.

A soft dependency. ``retrieve`` never raises: any failure returns ``degraded=True`` with no
matches, and the caller proceeds with its default strategy. Results are advisory guidance;
nothing here executes a procedure or produces factual data.
"""

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from opentelemetry.trace import Status, StatusCode
from water_source_research.core.capabilities import Capability, capabilities_for_workflow
from water_source_research.db.models import utcnow
from water_source_research.llm.embeddings import EmbeddingClient
from water_source_research.observability.otel import get_tracer
from water_source_research.schemas.procedures import (
    ProcedureMatch, Recipe, RecipeStep, RetrievalRequest, RetrievalResult,
)
from . import telemetry
from .config import ProcedureMemorySettings
from .ranking import RankingPolicy
from .repository import ProcedureRepository

class ProcedureMemory:
    def __init__(
        self,
        repo: ProcedureRepository,
        embedder: EmbeddingClient | None,
        settings: ProcedureMemorySettings,
        ranking: RankingPolicy | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.repo = repo
        self.embedder = embedder
        self.settings = settings
        self.ranking = ranking or RankingPolicy(settings)
        self.clock = clock

    async def aretrieve(self, req: RetrievalRequest) -> RetrievalResult:
        return await asyncio.to_thread(self.retrieve, req)

    def retrieve(self, req: RetrievalRequest) -> RetrievalResult:
        tracer = get_tracer()
        dims = {"field_type": req.field_type, "task_type": req.task_type}
        started = time.perf_counter()
        outcome = "degraded"
        with tracer.start_as_current_span("procedure_memory.retrieve") as span:
            span.set_attribute("research.run_id", req.run_id or "")
            span.set_attribute("research.field_type", req.field_type)
            span.set_attribute("research.task_type", req.task_type)
            span.set_attribute("research.workflow_version", req.workflow_version)
            try:
                result = self._retrieve(req, tracer)
                outcome = "matched" if result.matches else "no_match"
                return result
            except Exception as exc:  # noqa: BLE001 - soft dependency boundary
                # soft dependency: never break the research run
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, "procedure memory degraded"))
                return RetrievalResult(degraded=True, degraded_reason=type(exc).__name__)
            finally:
                latency = (time.perf_counter() - started) * 1000
                span.set_attribute("procedure.retrieval_latency_ms", round(latency, 3))
                span.set_attribute("procedure.retrieval_outcome", outcome)
                telemetry.retrieval_duration_ms.record(latency, {**dims, "outcome": outcome})
                telemetry.retrieval_requests.add(1, {**dims, "outcome": outcome})

    def _retrieve(self, req: RetrievalRequest, tracer) -> RetrievalResult:
        s = self.settings
        workflow_caps = frozenset(c.value for c in capabilities_for_workflow(req.workflow_version))

        with tracer.start_as_current_span("procedure_memory.filter") as span:
            candidates = self.repo.filter_candidates(req, s.statuses, workflow_caps,
                                                     s.max_candidates)
            span.set_attribute("procedure.candidate_count", len(candidates))
        if not candidates:
            return RetrievalResult()

        vector_degraded = False
        with tracer.start_as_current_span("procedure_memory.vector_search") as span:
            similarities: dict[str, float] = {}
            if self.embedder is None:
                vector_degraded = True
            else:
                try:
                    query_vec = self.embedder.embed([req.query_text()])[0]
                    similarities = self.repo.similarities(
                        [c.rank_input.version_id for c in candidates], query_vec)
                except Exception as exc:  # noqa: BLE001 - soft dependency boundary
                    # Semantic search is optional; metadata + reliability still rank safely.
                    span.record_exception(exc)
                    vector_degraded = True
            span.set_attribute("procedure.vector_degraded", vector_degraded)
            span.set_attribute("procedure.similarity_count", len(similarities))
            if similarities:
                span.set_attribute("procedure.top_similarity", round(max(similarities.values()), 4))

        by_version = {c.rank_input.version_id: c for c in candidates}
        with tracer.start_as_current_span("procedure_memory.rank") as span:
            ranked = self.ranking.rank([c.rank_input for c in candidates], similarities,
                                       self.clock())
            if not vector_degraded:
                # Below-threshold similarity is excluded; a missing embedding is kept but
                # gets no similarity credit.
                ranked = [r for r in ranked
                          if r.similarity is None or r.similarity >= s.min_similarity]
            span.set_attribute("procedure.ranked_count", len(ranked))

        with tracer.start_as_current_span("procedure_memory.select") as span:
            matches = []
            for rank, r in enumerate(ranked[: s.max_results], 1):
                row = by_version[r.candidate.version_id]
                recipe = compact_recipe(Recipe.model_validate(row.recipe_json),
                                        s.procedure_token_budget)
                matches.append(ProcedureMatch(
                    procedure_id=r.candidate.procedure_id,
                    procedure_version_id=r.candidate.version_id,
                    version=r.candidate.version, task_type=row.task_type,
                    field_type=row.field_type, rank=rank, score=r.score,
                    similarity_score=r.similarity, reliability_score=r.reliability,
                    usage_count=r.candidate.usage_count,
                    success_count=r.candidate.success_count,
                    recent_success_rate=r.candidate.recent_success_rate,
                    recipe=recipe, reasons=list(r.reasons),
                ))
            span.set_attribute("procedure.selected_count", len(matches))
            span.set_attribute("procedure.ids",
                               [f"{m.procedure_id}@v{m.version}" for m in matches])
            if req.run_id and req.field_keys and matches:
                self._record_retrievals(req, matches, span)

        return RetrievalResult(matches=matches, candidate_count=len(candidates),
                               vector_degraded=vector_degraded)

    def _record_retrievals(self, req: RetrievalRequest, matches: list[ProcedureMatch],
                           span) -> None:
        try:
            self.repo.record_retrievals(req.run_id or "", req.field_keys, [
                (m.procedure_version_id, m.rank, m.score, m.similarity_score) for m in matches
            ])
        except Exception as exc:  # noqa: BLE001 - soft dependency boundary
            # usage bookkeeping must not drop valid guidance
            span.record_exception(exc)

@dataclass(frozen=True, slots=True)
class StrategySelection:
    match: ProcedureMatch
    usable_steps: list[RecipeStep]
    dropped_capabilities: list[str]

    @property
    def adapted(self) -> bool:
        return bool(self.dropped_capabilities)

def choose_strategy(
    matches: list[ProcedureMatch], available: frozenset[Capability]
) -> StrategySelection | None:
    """Planner policy: current reality beats memory.

    Takes the best-ranked match with at least one step whose capability is available now,
    dropping unavailable steps (provider health/policy always wins). None means: use the
    default strategy.
    """
    for match in matches:
        usable = [s for s in match.recipe.recommended_steps if s.capability in available]
        if not usable:
            continue
        dropped = [s.capability.value for s in match.recipe.recommended_steps
                   if s.capability not in available]
        return StrategySelection(match, usable, dropped)
    return None

def compact_recipe(recipe: Recipe, token_budget: int) -> Recipe:
    """Trim optional detail until the planner-facing text fits the token budget."""
    def tokens(r: Recipe) -> int:
        return math.ceil(len(r.recipe_text()) / 4)

    current = recipe
    for attr in ("query_patterns", "constraints", "fallback_capabilities"):
        while tokens(current) > token_budget and getattr(current, attr):
            current = current.model_copy(update={attr: getattr(current, attr)[:-1]})
    return current
