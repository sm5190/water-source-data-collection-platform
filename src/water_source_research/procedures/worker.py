"""Asynchronous trajectory distillation. Never on the critical path of a city result.

claim job -> build trajectory -> eligibility -> distill -> schema -> grounding + safety
-> embed (optional) -> dedupe/merge -> persist CANDIDATE -> lifecycle evaluation

Idempotent: jobs are keyed by hash(trajectory, distiller version) and claimed with a
conditional UPDATE; a version and its origin commit together, and an origin's distillation
key is unique, so a retry after a crash reuses the earlier result.
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.exc import IntegrityError
from water_source_research.core.versions import EMBEDDING_VERSION
from water_source_research.db.models import utcnow
from water_source_research.llm.embeddings import EmbeddingClient
from water_source_research.observability.otel import get_tracer
from water_source_research.schemas.procedures import ProcedureStatus, Recipe
from water_source_research.schemas.research import FieldDefinition
from . import telemetry
from .config import ProcedureMemorySettings
from .dedupe import MergeDecision, MergeEvaluator, procedure_key
from .distiller import Distiller
from .eligibility import EligibilityPolicy
from .grounding import check_grounding
from .lifecycle import LifecycleManager, LifecyclePolicy
from .repository import OriginSpec, ProcedureRepository
from .safety import check_safety
from .trajectory import build_trajectory

class JobOutcome(StrEnum):
    CREATED = "created"
    MERGED = "merged"
    REUSED = "reused"
    INELIGIBLE = "ineligible"
    REJECTED = "rejected"
    FAILED = "failed"

@dataclass(slots=True)
class JobResult:
    distillation_key: str
    outcome: JobOutcome
    procedure_version_id: str | None = None
    reasons: list[str] = field(default_factory=list)

class DistillationWorker:
    def __init__(
        self,
        repo: ProcedureRepository,
        distiller: Distiller,
        embedder: EmbeddingClient | None,
        settings: ProcedureMemorySettings,
        field_definitions: Mapping[str, FieldDefinition],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.repo = repo
        self.distiller = distiller
        self.embedder = embedder
        self.s = settings
        self.fields = field_definitions
        self.clock = clock
        self.eligibility = EligibilityPolicy(settings, field_definitions)
        self.merge = MergeEvaluator(settings)
        self.lifecycle = LifecycleManager(repo, LifecyclePolicy(settings), clock)

    def run_once(self, limit: int = 10, run_id: str | None = None) -> list[JobResult]:
        now = self.clock()
        results = []
        for key in self.repo.claimable_jobs(now, self.s.distillation_max_attempts, limit, run_id):
            if self.repo.claim_job(key, now, self.s.distillation_lease_seconds,
                                   self.s.distillation_max_attempts):
                results.append(self.process(key))
        return results

    def process(self, key: str) -> JobResult:
        tracer = get_tracer()
        with tracer.start_as_current_span("procedure.distillation_job") as span:
            span.set_attribute("distillation.key", key)
            result = self._process(key, tracer, span)
            span.set_attribute("distillation.result", result.outcome.value)
            if result.procedure_version_id:
                span.set_attribute("procedure.version_id", result.procedure_version_id)
            telemetry.distillation_results.add(1, {"result": result.outcome.value})
            return result

    def _process(self, key: str, tracer, span) -> JobResult:
        job = self.repo.get_job(key)
        if job is None:
            return JobResult(key, JobOutcome.FAILED, reasons=["job not found"])
        span.set_attribute("research.run_id", job["run_id"])
        span.set_attribute("research.field_type", job["field_type"])
        try:
            reused = self.repo.version_for_distillation(key)
            if reused:
                self.repo.complete_job(key, "SUCCEEDED", version_id=reused)
                return JobResult(key, JobOutcome.REUSED, reused)

            with tracer.start_as_current_span("trajectory.build") as s:
                run, events = self.repo.load_run(job["run_id"])
                trajectory = build_trajectory(run, events, self.fields, job["field_type"])
                if trajectory.trajectory_id != job["trajectory_id"]:
                    raise RuntimeError("trajectory identity mismatch")
                self.repo.save_trajectory(trajectory)
                s.set_attribute("trajectory.id", trajectory.trajectory_id)
                s.set_attribute("trajectory.step_count", len(trajectory.steps))
                s.set_attribute("trajectory.payload_hash", trajectory.payload_hash())

            with tracer.start_as_current_span("trajectory.eligibility") as s:
                decision = self.eligibility.evaluate(trajectory)
                s.set_attribute("distillation.eligible", decision.eligible)
                s.set_attribute("distillation.auto_promotable", decision.auto_promotable)
            if not decision.eligible:
                self.repo.complete_job(key, "INELIGIBLE", "; ".join(decision.reasons))
                return JobResult(key, JobOutcome.INELIGIBLE, reasons=decision.reasons)

            with tracer.start_as_current_span("procedure.distill") as s:
                recipe = self.distiller.distill(trajectory)
                s.set_attribute("procedure.step_count", len(recipe.recommended_steps))

            with tracer.start_as_current_span("procedure.validate") as s:
                # Re-validate through the schema: a distiller may hand back a constructed or
                # mutated object.
                recipe = Recipe.model_validate(recipe.model_dump(mode="json"))
                violations = (check_grounding(recipe, trajectory, self.fields)
                              + check_safety(recipe, self.s.max_recipe_chars))
                s.set_attribute("procedure.violation_count", len(violations))
                s.set_attribute("procedure.violation_codes",
                                sorted({v.code for v in violations}))
            if violations:
                reasons = [f"{v.code}: {v.detail}" for v in violations]
                self.repo.complete_job(key, "REJECTED", "; ".join(reasons))
                return JobResult(key, JobOutcome.REJECTED, reasons=reasons)

            embedding, input_hash = self._embed(recipe, tracer)
            origin_base = {"run_id": trajectory.run_id,
                           "trajectory_id": trajectory.trajectory_id,
                           "distillation_key": key, "city_name": trajectory.city_name,
                           "human_corrected": trajectory.quality.human_correction_count > 0}
            version_id, outcome = self._dedupe_and_persist(
                recipe, embedding, input_hash, origin_base, decision.auto_promotable, key, tracer)

            snapshot = self.repo.get_version(version_id)
            if snapshot:
                self.lifecycle.apply(snapshot)
            self.repo.complete_job(key, "SUCCEEDED", version_id=version_id)
            return JobResult(key, outcome, version_id, decision.reasons)
        except Exception as exc:  # noqa: BLE001 - soft dependency boundary
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, "distillation failed"))
            self.repo.complete_job(key, "FAILED", f"{type(exc).__name__}: {exc}")
            return JobResult(key, JobOutcome.FAILED, reasons=[type(exc).__name__])

    def _dedupe_and_persist(self, recipe: Recipe, embedding: list[float] | None,
                            input_hash: str | None, origin_base: dict, auto_promotable: bool,
                            key: str, tracer) -> tuple[str, JobOutcome]:
        pkey = procedure_key(recipe, None, None)
        for _ in range(5):  # re-evaluate if a concurrent worker changed the picture
            with tracer.start_as_current_span("procedure.dedupe") as s:
                procedure_id = self.repo.get_or_create_procedure(pkey, recipe, None, None)
                existing = self.repo.versions_for_procedure(procedure_id)
                evaluation = self.merge.evaluate(
                    recipe, embedding, existing,
                    self.embedder.model if self.embedder else None)
                s.set_attribute("procedure.id", procedure_id)
                s.set_attribute("procedure.merge_decision", evaluation.decision.value)
                if evaluation.jaccard is not None:
                    s.set_attribute("procedure.merge_jaccard", round(evaluation.jaccard, 4))
            with tracer.start_as_current_span("procedure.persist") as s:
                try:
                    if evaluation.decision is MergeDecision.MERGE:
                        assert evaluation.target_version_id
                        added = self.repo.add_origin(evaluation.target_version_id, OriginSpec(
                            contribution_type="MERGED_SUPPORT", **origin_base))
                        version_id = (evaluation.target_version_id if added
                                      else self.repo.version_for_distillation(key))
                        if version_id is None:
                            continue
                        s.set_attribute("procedure.version_id", version_id)
                        return version_id, (JobOutcome.MERGED if added else JobOutcome.REUSED)
                    next_version = max((v.version for v in existing), default=0) + 1
                    version_id = self.repo.create_version_with_origin(
                        procedure_id, next_version, recipe, ProcedureStatus.CANDIDATE,
                        auto_promotable, self.distiller.version,
                        OriginSpec(contribution_type="CREATED", **origin_base),
                        embedding, self.embedder.model if self.embedder else None,
                        EMBEDDING_VERSION, input_hash)
                    s.set_attribute("procedure.version_id", version_id)
                    s.set_attribute("procedure.version", next_version)
                    return version_id, JobOutcome.CREATED
                except IntegrityError:
                    existing_version = self.repo.version_for_distillation(key)
                    if existing_version:
                        return existing_version, JobOutcome.REUSED
                    # Lost a version-number race; loop and re-evaluate (likely a merge now).
        raise RuntimeError("could not persist procedure after repeated contention")

    def _embed(self, recipe: Recipe, tracer) -> tuple[list[float] | None, str | None]:
        if self.embedder is None:
            return None, None
        text = recipe.embedding_text()
        input_hash = embedding_input_hash(text, self.embedder.model)
        with tracer.start_as_current_span("procedure.embed") as s:
            try:
                vector = self.embedder.embed([text])[0]
                s.set_attribute("procedure.embedding_status", "READY")
                return vector, input_hash
            except Exception as exc:  # noqa: BLE001 - soft dependency boundary
                # Candidate is stored with a PENDING embedding; embed_pending() retries later.
                s.record_exception(exc)
                s.set_attribute("procedure.embedding_status", "PENDING")
                return None, None

    def embed_pending(self, limit: int = 50) -> int:
        if self.embedder is None:
            return 0
        done = 0
        for version_id, recipe_json in self.repo.pending_embeddings(limit, self.embedder.model):
            text = Recipe.model_validate(recipe_json).embedding_text()
            try:
                vector = self.embedder.embed([text])[0]
            except Exception:  # noqa: BLE001 - soft dependency boundary
                self.repo.mark_embedding_failed(version_id)
                continue
            if self.repo.set_embedding(version_id, vector, self.embedder.model,
                                       EMBEDDING_VERSION,
                                       embedding_input_hash(text, self.embedder.model)):
                done += 1
        return done

    def sweep_lifecycle(self):
        return self.lifecycle.sweep()

def embedding_input_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}|{EMBEDDING_VERSION}|{text}".encode()).hexdigest()
