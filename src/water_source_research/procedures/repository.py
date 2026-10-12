"""All procedural-memory persistence.

Concurrency is enforced at the database boundary, never by in-process locks alone:
unique constraints (procedure_key, (procedure_id, version), distillation_key, usage identity),
a partial unique index (one ACTIVE version per procedure), conditional UPDATEs for job
claims and lifecycle transitions, and SQL-side increments for statistics.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Engine, Float, and_, bindparam, distinct, func, or_, select, true, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer
from water_source_research.core.versions import (
    EMBEDDING_DIMENSIONS, RECIPE_SCHEMA_VERSION, WORKFLOW_VERSION,
)
from water_source_research.db.models import utcnow
from water_source_research.db.procedure_models import (
    ProcedureOriginRecord, ProcedureRecord, ProcedureUsageRecord, ProcedureVersionRecord,
)
from water_source_research.db.research_models import (
    DistillationJobRecord, ResearchRunRecord, RunEventRecord, TrajectoryRecord,
)
from water_source_research.schemas.events import WorkflowEvent
from water_source_research.schemas.procedures import (
    ProcedureStatus, Recipe, RetrievalRequest, Trajectory,
)
from .ranking import RankInput

P, V = ProcedureRecord, ProcedureVersionRecord
U, O, J = ProcedureUsageRecord, ProcedureOriginRecord, DistillationJobRecord

@dataclass(frozen=True, slots=True)
class CandidateRow:
    rank_input: RankInput
    task_type: str
    field_type: str
    recipe_json: dict[str, Any]

@dataclass(frozen=True, slots=True)
class VersionSnapshot:
    id: str
    procedure_id: str
    version: int
    status: str
    status_changed_at: datetime
    auto_promotable: bool
    required_capabilities: list[str]
    min_workflow_version: int
    recipe_json: dict[str, Any]
    recipe_hash: str
    embedding: list[float] | None
    support_runs: int
    support_cities: int
    support_corrected_runs: int
    usage_count: int
    success_count: int
    failure_count: int
    recent_success_rate: float | None
    human_correction_count: int
    last_success_at: datetime | None
    approved_at: datetime | None
    created_at: datetime
    embedding_model: str | None = None  # vectors are only comparable within one model

@dataclass(frozen=True, slots=True)
class OriginSpec:
    run_id: str
    trajectory_id: str
    distillation_key: str
    city_name: str
    contribution_type: str  # CREATED | MERGED_SUPPORT
    human_corrected: bool

@dataclass(frozen=True, slots=True)
class UsageOutcome:
    success: bool
    field_resolved: bool
    validation_result: str | None
    tool_calls: int
    search_rounds: int
    cost: float | None
    latency_ms: float | None
    human_review_required: bool
    human_corrected: bool

class ProcedureRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- read path ----------------------------------------------------------------------

    def filter_candidates(
        self,
        req: RetrievalRequest,
        statuses: list[ProcedureStatus],
        workflow_capabilities: frozenset[str],
        limit: int,
    ) -> list[CandidateRow]:
        """Metadata filter. Runs before (and bounds) any semantic comparison."""
        conditions = [
            P.task_type == req.task_type,
            P.field_type == req.field_type,
            V.status.in_([s.value for s in statuses]),
            V.min_workflow_version <= req.workflow_version,
        ]
        # NULL on the procedure means "applies to any"; an unknown request value can't exclude.
        for column, value in ((P.water_type, req.water_type),
                              (P.jurisdiction_type, req.jurisdiction_type),
                              (P.language, req.language)):
            conditions.append(or_(column.is_(None), column == value) if value else true())
        stmt = (
            select(V, P.task_type, P.field_type)
            .join(P, P.procedure_id == V.procedure_id)
            .where(*conditions)
            .options(defer(V.embedding))  # vectors are compared separately, only for survivors
            .order_by(V.procedure_id, V.version)
            .limit(limit)
        )
        with Session(self.engine) as session:
            rows = session.execute(stmt).all()
            out = []
            for v, task_type, field_type in rows:
                if not set(v.required_capabilities) <= workflow_capabilities:
                    continue
                out.append(CandidateRow(
                    RankInput(v.procedure_id, v.id, v.version, v.success_count,
                              v.failure_count, v.usage_count, v.recent_success_rate,
                              v.human_correction_count, v.last_success_at, v.created_at),
                    task_type, field_type, v.recipe_json,
                ))
            return out

    def similarities(self, version_ids: list[str], query_vec: list[float],
                     model: str | None = None) -> dict[str, float]:
        """Cosine similarity for already-filtered versions only (bounded set).

        With ``model``, only vectors from that embedding model are compared (vectors from
        different models live in different spaces even when their widths match).
        """
        if not version_ids:
            return {}
        ready = and_(V.id.in_(version_ids), V.embedding.is_not(None),
                     V.embedding_status == "READY")
        if model is not None:
            ready = and_(ready, V.embedding_model == model)
        with Session(self.engine) as session:
            if self.engine.dialect.name == "postgresql":
                q = bindparam("q", query_vec, type_=Vector(EMBEDDING_DIMENSIONS))
                distance = V.embedding.op("<=>", return_type=Float)(q)
                rows = session.execute(select(V.id, 1 - distance).where(ready)).all()
                return {vid: float(sim) for vid, sim in rows}
            rows = session.execute(select(V.id, V.embedding).where(ready)).all()
            return {vid: _cosine(vec, query_vec) for vid, vec in rows}

    # --- usage --------------------------------------------------------------------------

    def record_retrievals(
        self, run_id: str, field_keys: list[str],
        matches: list[tuple[str, int, float, float | None]],
    ) -> int:
        """One usage row per (version, run, field). Re-recording is a no-op."""
        inserted = 0
        for version_id, rank, score, similarity in matches:
            new_for_version = 0
            for field_key in field_keys:
                try:
                    with Session(self.engine) as session, session.begin():
                        session.add(U(procedure_version_id=version_id, run_id=run_id,
                                      field_key=field_key, retrieved_rank=rank,
                                      retrieved_score=score, similarity=similarity))
                except IntegrityError:
                    continue
                new_for_version += 1
            if new_for_version:
                self._increment(version_id, retrieved_count=V.retrieved_count + new_for_version)
                inserted += new_for_version
        return inserted

    def mark_selected(self, version_id: str, run_id: str, field_keys: list[str],
                      adapted: bool) -> int:
        with Session(self.engine) as session, session.begin():
            n = session.execute(
                update(U).where(U.procedure_version_id == version_id, U.run_id == run_id,
                                U.field_key.in_(field_keys), U.selected.is_(False))
                .values(selected=True, adapted=adapted)
            ).rowcount or 0
            if n:
                session.execute(update(V).where(V.id == version_id)
                                .values(selected_count=V.selected_count + n))
            return n

    def mark_applied(self, version_id: str, run_id: str, field_keys: list[str]) -> int:
        now = utcnow()
        with Session(self.engine) as session, session.begin():
            n = session.execute(
                update(U).where(U.procedure_version_id == version_id, U.run_id == run_id,
                                U.field_key.in_(field_keys), U.selected.is_(True),
                                U.applied.is_(False))
                .values(applied=True)
            ).rowcount or 0
            if n:
                session.execute(update(V).where(V.id == version_id)
                                .values(usage_count=V.usage_count + n, last_used_at=now))
            return n

    def record_outcome(self, version_id: str, run_id: str, field_key: str,
                       outcome: UsageOutcome, ewma_alpha: float) -> bool:
        """Apply an outcome exactly once; only applied usages affect procedure statistics."""
        now = utcnow()
        s = 1.0 if outcome.success else 0.0
        with Session(self.engine) as session, session.begin():
            n = session.execute(
                update(U).where(U.procedure_version_id == version_id, U.run_id == run_id,
                                U.field_key == field_key, U.applied.is_(True),
                                U.outcome_recorded_at.is_(None))
                .values(result="SUCCESS" if outcome.success else "FAILURE",
                        field_resolved=outcome.field_resolved,
                        validation_result=outcome.validation_result,
                        tool_calls=outcome.tool_calls, search_rounds=outcome.search_rounds,
                        cost=outcome.cost, latency_ms=outcome.latency_ms,
                        human_review_required=outcome.human_review_required,
                        human_corrected=outcome.human_corrected,
                        outcome_recorded_at=now)
            ).rowcount or 0
            if not n:
                return False
            stats: dict[str, Any] = {"last_success_at": now} if outcome.success else {}
            session.execute(update(V).where(V.id == version_id).values(
                **stats,
                success_count=V.success_count + int(outcome.success),
                failure_count=V.failure_count + int(not outcome.success),
                recent_success_rate=func.coalesce(V.recent_success_rate, s) * (1 - ewma_alpha)
                + ewma_alpha * s,
                human_review_count=V.human_review_count + int(outcome.human_review_required),
                human_correction_count=V.human_correction_count + int(outcome.human_corrected),
                sum_tool_calls=V.sum_tool_calls + outcome.tool_calls,
                sum_search_rounds=V.sum_search_rounds + outcome.search_rounds,
                sum_cost=V.sum_cost + (outcome.cost or 0.0),
                sum_latency_ms=V.sum_latency_ms + (outcome.latency_ms or 0.0),
            ))
            return True

    def usages_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(U).where(U.run_id == run_id)
                                   .order_by(U.procedure_version_id, U.field_key)).all()
            return [_row_dict(r) for r in rows]

    # --- runs, trajectories, jobs ---------------------------------------------------------

    def load_run(self, run_id: str) -> tuple[dict[str, Any], list[WorkflowEvent]]:
        with Session(self.engine) as session:
            run = session.get(ResearchRunRecord, run_id)
            if run is None:
                raise LookupError(f"Run {run_id!r} not persisted")
            events = session.scalars(select(RunEventRecord).where(RunEventRecord.run_id == run_id)
                                     .order_by(RunEventRecord.seq)).all()
            return _row_dict(run), [
                WorkflowEvent(seq=e.seq, kind=e.kind, node=e.node, field_key=e.field_key,
                              capability=e.capability, provider=e.provider,
                              strategy=e.strategy, result=e.result, attrs=e.attrs or {},
                              created_at=_iso(e.created_at))
                for e in events
            ]

    def save_trajectory(self, t: Trajectory) -> None:
        try:
            with Session(self.engine) as session, session.begin():
                session.add(TrajectoryRecord(
                    trajectory_id=t.trajectory_id, run_id=t.run_id, task_type=t.task_type,
                    field_type=t.field_type, workflow_version=t.workflow_version,
                    outcome=t.outcome, payload=t.model_dump(mode="json"),
                    payload_hash=t.payload_hash(),
                ))
        except IntegrityError:
            pass  # same trajectory identity already stored

    def claimable_jobs(self, now: datetime, max_attempts: int, limit: int,
                       run_id: str | None = None) -> list[str]:
        stmt = select(J.distillation_key).where(_claimable(now, max_attempts))
        if run_id:
            stmt = stmt.where(J.run_id == run_id)
        with Session(self.engine) as session:
            return list(session.scalars(stmt.order_by(J.created_at).limit(limit)))

    def claim_job(self, key: str, now: datetime, lease_seconds: int, max_attempts: int) -> bool:
        with Session(self.engine) as session, session.begin():
            n = session.execute(
                update(J).where(J.distillation_key == key, _claimable(now, max_attempts))
                .values(status="RUNNING", attempts=J.attempts + 1,
                        lease_until=now + timedelta(seconds=lease_seconds), updated_at=now)
            ).rowcount
            return bool(n)

    def complete_job(self, key: str, status: str, error: str | None = None,
                     version_id: str | None = None) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(update(J).where(J.distillation_key == key).values(
                status=status, last_error=(error or None) and error[:1000],
                result_procedure_version_id=version_id, lease_until=None,
                updated_at=utcnow()))

    def get_job(self, key: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            job = session.get(J, key)
            return _row_dict(job) if job else None

    # --- procedures and versions -------------------------------------------------------

    def version_for_distillation(self, distillation_key: str) -> str | None:
        with Session(self.engine) as session:
            return session.scalar(select(O.procedure_version_id)
                                  .where(O.distillation_key == distillation_key))

    def get_or_create_procedure(self, procedure_key: str, recipe: Recipe,
                                jurisdiction_type: str | None, language: str | None) -> str:
        existing = self._procedure_id(procedure_key)
        if existing:
            return existing
        procedure_id = str(uuid4())
        try:
            with Session(self.engine) as session, session.begin():
                session.add(P(procedure_id=procedure_id, procedure_key=procedure_key,
                              task_type=recipe.task_type, field_type=recipe.field_type,
                              water_type=recipe.water_type,
                              jurisdiction_type=jurisdiction_type, language=language))
            return procedure_id
        except IntegrityError:
            winner = self._procedure_id(procedure_key)
            if winner is None:
                raise
            return winner

    def versions_for_procedure(self, procedure_id: str) -> list[VersionSnapshot]:
        with Session(self.engine) as session:
            rows = session.scalars(select(V).where(V.procedure_id == procedure_id)
                                   .order_by(V.version)).all()
            return [_snapshot(v) for v in rows]

    def create_version_with_origin(
        self, procedure_id: str, version: int, recipe: Recipe, status: ProcedureStatus,
        auto_promotable: bool, distiller_version: str, origin: OriginSpec,
        embedding: list[float] | None, embedding_model: str | None,
        embedding_version: str | None, embedding_input_hash: str | None,
    ) -> str:
        """Version and its CREATED origin commit together, so a crash leaves both or neither.

        Raises IntegrityError when another worker took this version number or already
        recorded this distillation key; the caller re-evaluates.
        """
        version_id = str(uuid4())
        with Session(self.engine) as session, session.begin():
            session.add(V(
                id=version_id, procedure_id=procedure_id, version=version, status=status.value,
                auto_promotable=auto_promotable, title=recipe.title, summary=recipe.summary,
                recipe_json=recipe.model_dump(mode="json"), recipe_text=recipe.recipe_text(),
                recipe_hash=recipe.content_hash(),
                required_capabilities=sorted(set(recipe.capability_plan())),
                workflow_version=WORKFLOW_VERSION, min_workflow_version=WORKFLOW_VERSION,
                schema_version=RECIPE_SCHEMA_VERSION, distiller_version=distiller_version,
                embedding=embedding, embedding_model=embedding_model if embedding else None,
                embedding_version=embedding_version if embedding else None,
                embedding_input_hash=embedding_input_hash if embedding else None,
                embedding_status="READY" if embedding else "PENDING",
            ))
            session.flush()
            session.add(_origin(version_id, origin))
            session.flush()
            _recompute_support(session, version_id)
        return version_id

    def add_origin(self, version_id: str, origin: OriginSpec) -> bool:
        """Record supporting provenance for an existing version (merge). Idempotent."""
        try:
            with Session(self.engine) as session, session.begin():
                session.add(_origin(version_id, origin))
        except IntegrityError:
            return False
        with Session(self.engine) as session, session.begin():
            _recompute_support(session, version_id)
        return True

    def origins_for_version(self, version_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(select(O).where(O.procedure_version_id == version_id)
                                   .order_by(O.id)).all()
            return [_row_dict(r) for r in rows]

    def get_version(self, version_id: str) -> VersionSnapshot | None:
        with Session(self.engine) as session:
            v = session.get(V, version_id)
            return _snapshot(v) if v else None

    def lifecycle_candidates(self) -> list[VersionSnapshot]:
        live = [ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE, ProcedureStatus.DEPRECATED]
        with Session(self.engine) as session:
            rows = session.scalars(select(V).options(defer(V.embedding))
                                   .where(V.status.in_([s.value for s in live]))
                                   .order_by(V.id)).all()
            return [_snapshot(v, with_embedding=False) for v in rows]

    def transition(self, version_id: str, expected: set[ProcedureStatus],
                   new: ProcedureStatus) -> bool:
        """Optimistic status change: succeeds only if the current status is expected."""
        if new is ProcedureStatus.ACTIVE:
            raise ValueError("Use promote() to activate a version")
        with Session(self.engine) as session, session.begin():
            n = session.execute(
                update(V).where(V.id == version_id, V.status.in_([s.value for s in expected]))
                .values(status=new.value, status_changed_at=utcnow())
            ).rowcount
            return bool(n)

    def promote(self, version_id: str, expected: set[ProcedureStatus],
                approved_by: str | None = None) -> bool:
        """Atomically make this version ACTIVE and deprecate the previously active one."""
        now = utcnow()
        try:
            with Session(self.engine) as session, session.begin():
                procedure_id = session.scalar(select(V.procedure_id).where(V.id == version_id))
                if procedure_id is None:
                    return False
                session.execute(
                    update(V).where(V.procedure_id == procedure_id, V.id != version_id,
                                    V.status == ProcedureStatus.ACTIVE.value)
                    .values(status=ProcedureStatus.DEPRECATED.value, status_changed_at=now))
                values: dict[str, Any] = {"status": ProcedureStatus.ACTIVE.value,
                                          "status_changed_at": now}
                if approved_by:
                    values.update(approved_by=approved_by, approved_at=now)
                n = session.execute(
                    update(V).where(V.id == version_id,
                                    V.status.in_([s.value for s in expected]))
                    .values(**values)).rowcount
                if not n:
                    raise _NotPromoted  # roll back the deprecation too
            return True
        except _NotPromoted:
            return False
        except IntegrityError:
            return False  # a concurrent promotion won; the partial unique index held

    # --- embeddings ---------------------------------------------------------------------

    def pending_embeddings(self, limit: int,
                           model: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        """Versions needing an embedding; with ``model``, also those embedded by another
        model (re-embedded after switching embedding providers)."""
        needed = V.embedding_status != "READY"
        if model is not None:
            needed = or_(needed, V.embedding_model.is_(None), V.embedding_model != model)
        with Session(self.engine) as session:
            rows = session.execute(
                select(V.id, V.recipe_json).where(needed,
                                                  V.status != ProcedureStatus.RETIRED.value)
                .order_by(V.created_at).limit(limit)).all()
            return [(vid, recipe) for vid, recipe in rows]

    def set_embedding(self, version_id: str, vector: list[float], model: str,
                      embedding_version: str, input_hash: str) -> bool:
        """Write an embedding only if it would change something; never touches the recipe."""
        with Session(self.engine) as session, session.begin():
            n = session.execute(
                update(V).where(
                    V.id == version_id,
                    or_(V.embedding_status != "READY", V.embedding_input_hash.is_(None),
                        V.embedding_input_hash != input_hash,
                        V.embedding_version != embedding_version),
                ).values(embedding=vector, embedding_model=model,
                         embedding_version=embedding_version,
                         embedding_input_hash=input_hash, embedding_status="READY")
            ).rowcount
            return bool(n)

    def mark_embedding_failed(self, version_id: str) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(update(V).where(V.id == version_id, V.embedding_status != "READY")
                            .values(embedding_status="FAILED"))

    # --- reporting ----------------------------------------------------------------------

    def stats_summary(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            by_status = dict(session.execute(
                select(V.status, func.count()).group_by(V.status)).all())
            jobs = dict(session.execute(select(J.status, func.count()).group_by(J.status)).all())
            merges = session.scalar(select(func.count()).select_from(O)
                                    .where(O.contribution_type == "MERGED_SUPPORT")) or 0
            origins = session.scalar(select(func.count()).select_from(O)) or 0
            pending = session.scalar(select(func.count()).select_from(V)
                                     .where(V.embedding_status != "READY")) or 0
            return {
                "versions_by_status": by_status,
                "procedures": session.scalar(select(func.count()).select_from(P)) or 0,
                "distillation_jobs_by_status": jobs,
                "merge_rate": (merges / origins) if origins else 0.0,
                "pending_embeddings": pending,
            }

    def list_versions(self, status: ProcedureStatus | None = None,
                      limit: int = 100) -> list[dict[str, Any]]:
        stmt = (select(V, P.task_type, P.field_type).join(P, P.procedure_id == V.procedure_id)
                .options(defer(V.embedding)).order_by(V.procedure_id, V.version).limit(limit))
        if status:
            stmt = stmt.where(V.status == status.value)
        with Session(self.engine) as session:
            out = []
            for v, task_type, field_type in session.execute(stmt).all():
                outcomes = v.success_count + v.failure_count
                out.append({
                    "procedure_version_id": v.id, "procedure_id": v.procedure_id,
                    "version": v.version, "status": v.status, "task_type": task_type,
                    "field_type": field_type, "title": v.title,
                    "support_runs": v.support_runs, "support_cities": v.support_cities,
                    "usage_count": v.usage_count, "success_count": v.success_count,
                    "failure_count": v.failure_count,
                    "recent_success_rate": v.recent_success_rate,
                    "human_correction_rate": (v.human_correction_count / outcomes)
                    if outcomes else None,
                    "distiller_version": v.distiller_version,
                    "workflow_version": v.workflow_version,
                })
            return out

    # --- helpers ------------------------------------------------------------------------

    def _procedure_id(self, procedure_key: str) -> str | None:
        with Session(self.engine) as session:
            return session.scalar(select(P.procedure_id).where(P.procedure_key == procedure_key))

    def _increment(self, version_id: str, **values: Any) -> None:
        with Session(self.engine) as session, session.begin():
            session.execute(update(V).where(V.id == version_id).values(**values))

class _NotPromoted(Exception):
    pass

def _claimable(now: datetime, max_attempts: int):
    return and_(
        J.attempts < max_attempts,
        or_(J.status == "PENDING", J.status == "FAILED",
            and_(J.status == "RUNNING", J.lease_until < now)),
    )

def _origin(version_id: str, o: OriginSpec) -> ProcedureOriginRecord:
    return O(procedure_version_id=version_id, run_id=o.run_id, trajectory_id=o.trajectory_id,
             distillation_key=o.distillation_key, city_name=o.city_name,
             contribution_type=o.contribution_type, human_corrected=o.human_corrected)

def _recompute_support(session: Session, version_id: str) -> None:
    # Recomputed from provenance rather than incremented, so it can't drift on retries.
    by_version = O.procedure_version_id == version_id
    session.execute(update(V).where(V.id == version_id).values(
        support_runs=select(func.count(distinct(O.run_id))).where(by_version)
        .scalar_subquery(),
        support_cities=select(func.count(distinct(O.city_name))).where(by_version)
        .scalar_subquery(),
        support_corrected_runs=select(func.count(distinct(O.run_id)))
        .where(by_version, O.human_corrected.is_(True)).scalar_subquery(),
    ))

def _snapshot(v: ProcedureVersionRecord, with_embedding: bool = True) -> VersionSnapshot:
    return VersionSnapshot(
        id=v.id, procedure_id=v.procedure_id, version=v.version, status=v.status,
        status_changed_at=v.status_changed_at, auto_promotable=v.auto_promotable,
        required_capabilities=list(v.required_capabilities),
        min_workflow_version=v.min_workflow_version, recipe_json=v.recipe_json,
        recipe_hash=v.recipe_hash,
        embedding=(list(v.embedding) if with_embedding and v.embedding is not None else None),
        support_runs=v.support_runs, support_cities=v.support_cities,
        support_corrected_runs=v.support_corrected_runs, usage_count=v.usage_count,
        success_count=v.success_count, failure_count=v.failure_count,
        recent_success_rate=v.recent_success_rate,
        human_correction_count=v.human_correction_count, last_success_at=v.last_success_at,
        approved_at=v.approved_at, created_at=v.created_at, embedding_model=v.embedding_model,
    )

def _row_dict(row: Any) -> dict[str, Any]:
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}

def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt else utcnow().isoformat()

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
