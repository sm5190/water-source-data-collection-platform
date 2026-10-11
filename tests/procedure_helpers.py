"""Shared fakes/builders for procedural-memory tests (no network, no real providers)."""

import hashlib
import math
import re
from datetime import timedelta
from typing import Any
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from water_source_research.core.capabilities import Capability
from water_source_research.db.models import utcnow
from water_source_research.db.procedure_models import ProcedureVersionRecord
from water_source_research.db.session import init_db
from water_source_research.procedures.config import ProcedureMemorySettings
from water_source_research.procedures.dedupe import procedure_key
from water_source_research.procedures.repository import OriginSpec, ProcedureRepository
from water_source_research.schemas.procedures import ProcedureStatus, Recipe, RecipeStep
from water_source_research.core.versions import EMBEDDING_DIMENSIONS

class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder: similar text -> similar vectors."""

    model = "fake-embedding"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIMENSIONS
        for token in re.findall(r"[a-z_]+", text.lower()):
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

class FailingEmbedder:
    model = "failing"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError("embedding service down")

def make_engine(tmp_path, name="proc.db"):
    engine = create_engine(f"sqlite:///{tmp_path / name}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    init_db(engine)
    return engine

def settings(**overrides: Any) -> ProcedureMemorySettings:
    return ProcedureMemorySettings(**overrides)

def recipe(field_type="withdrawal", water_type=None, sources=("utility_annual_report",),
           queries=("{utility} annual water report",),
           caps=(Capability.SOURCE_DISCOVERY, Capability.STATIC_DOCUMENT_FETCH),
           summary="Search official utility reports first.") -> Recipe:
    return Recipe(
        task_type=f"{field_type}_research", field_type=field_type, water_type=water_type,
        title=f"{field_type} research", summary=summary,
        recommended_steps=[
            RecipeStep(order=i, capability=c, reason="observed successful step",
                       source_preferences=list(sources) if i == 1 else [])
            for i, c in enumerate(caps, 1)
        ],
        query_patterns=list(queries), preferred_source_types=list(sources),
    )

_counter = {"n": 0}

def seed_version(
    repo: ProcedureRepository, r: Recipe, status=ProcedureStatus.ACTIVE,
    embedder: FakeEmbedder | None = None, city="Toronto", procedure_tag: str | None = None,
    **stats: Any,
) -> str:
    """Insert a procedure version directly (bypassing distillation) and set test stats.

    Each call creates a distinct logical procedure unless ``procedure_tag`` is shared
    (the tag is stored as jurisdiction_type, which an unspecified request doesn't filter).
    """
    _counter["n"] += 1
    tag = procedure_tag or f"seed-{_counter['n']}"
    key = procedure_key(r, tag, None)
    pid = repo.get_or_create_procedure(key, r, tag, None)
    existing = repo.versions_for_procedure(pid)
    emb = embedder.embed([r.embedding_text()])[0] if embedder else None
    vid = repo.create_version_with_origin(
        pid, len(existing) + 1, r, ProcedureStatus.CANDIDATE, True, "test",
        OriginSpec(f"seed-run-{_counter['n']}", f"traj-{_counter['n']}",
                   f"dk-{_counter['n']}", city, "CREATED", False),
        emb, "fake-embedding" if emb else None, "1" if emb else None,
        "h" if emb else None,
    )
    values: dict[str, Any] = dict(stats)
    if status is ProcedureStatus.ACTIVE:
        assert repo.promote(vid, {ProcedureStatus.CANDIDATE})
    elif status is not ProcedureStatus.CANDIDATE:
        values["status"] = status.value
    if values:
        with Session(repo.engine) as s, s.begin():
            s.execute(update(ProcedureVersionRecord)
                      .where(ProcedureVersionRecord.id == vid).values(**values))
    return vid

def days_ago(n: float):
    return utcnow() - timedelta(days=n)

# --- finished-run builders (trusted events only) -------------------------------------

from water_source_research.procedures.distiller import DeterministicDistiller
from water_source_research.procedures.worker import DistillationWorker
from water_source_research.schemas.events import (
    EventKind, EventResult, append_event,
)
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)
from water_source_research.services.run_recorder import RunRecorder
from water_source_research.schemas.common import FieldResolutionStatus

FIELDS = load_field_definitions()
WITHDRAWAL_FIELDS = [k for k, d in FIELDS.items()
                     if d.field_type == "withdrawal" and not d.derived]
PRIMARY = "total_city_water_withdrawals_m3_day"

def discovery(query: str, provider="searxng", result=EventResult.SUCCESS, retained=3,
              capability=Capability.SOURCE_DISCOVERY, field_key=PRIMARY) -> dict:
    return {"kind": EventKind.TOOL_CALL, "node": "discover", "field_key": field_key,
            "capability": capability, "provider": provider,
            "strategy": "official_domain_search", "result": result,
            "attrs": {"query": query, "sources_found": 8, "sources_retained": retained}}

def fetch(provider="direct_http", capability=Capability.STATIC_DOCUMENT_FETCH,
          result=EventResult.SUCCESS, field_key=PRIMARY) -> dict:
    return {"kind": EventKind.TOOL_CALL, "node": "acquire", "field_key": field_key,
            "capability": capability, "provider": provider, "result": result,
            "attrs": {"chunks": 12}}

def default_steps() -> list[dict]:
    # Queries use the literal city/utility; distillation must template them back.
    return [discovery("{utility_value} annual water report"),
            discovery("{city_value} average daily production mgd"),
            fetch()]

def run_state(run_id: str, city: str, country: str = "Canada", utility: str | None = None,
              steps: list[dict] | None = None, resolve=None, conflict=(), corrected=(),
              finalized=True, source_type="utility_annual_report", water_type=None,
              evidence=True) -> dict:
    utility = utility or f"{city} Water"
    research = initialize_city_state(city, country)
    events: list[dict] = []
    events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="human_review",
                          field_key="utility_name",
                          attrs={"from_status": "CANDIDATE", "to_status": "RESOLVED",
                                 "anchor_value": utility})
    research.fields["utility_name"].status = FieldResolutionStatus.RESOLVED
    if water_type:
        events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="human_review",
                              field_key="water_type",
                              attrs={"to_status": "RESOLVED", "anchor_value": water_type})
        research.fields["water_type"].status = FieldResolutionStatus.RESOLVED
    for step in (steps if steps is not None else default_steps()):
        step = dict(step, attrs=dict(step.get("attrs", {})))
        if "query" in step["attrs"]:
            step["attrs"]["query"] = step["attrs"]["query"].format(
                utility_value=utility, city_value=city)
        events = append_event(events, **step)
    for f in (WITHDRAWAL_FIELDS if resolve is None else resolve):
        if evidence:
            events = append_event(events, kind=EventKind.EVIDENCE_ACCEPTED, node="validate",
                                  field_key=f, attrs={"url": f"https://{run_id}.example/report",
                                                      "source_type": source_type})
        events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="validate",
                              field_key=f, attrs={"to_status": "RESOLVED"})
        research.fields[f].status = FieldResolutionStatus.RESOLVED
    for f in conflict:
        events = append_event(events, kind=EventKind.FIELD_TRANSITION, node="validate",
                              field_key=f, attrs={"to_status": "CONFLICT"})
        research.fields[f].status = FieldResolutionStatus.CONFLICT
    for f in corrected:
        events = append_event(events, kind=EventKind.HITL, node="human_review", field_key=f,
                              result=EventResult.CORRECTED,
                              attrs={"correction_category": "source_type_preference"})
    return {"run_id": run_id, "city_name": city, "country": country,
            "research_state": research.model_dump(mode="json"), "events": events,
            "finalized": finalized, "needs_human_review": bool(corrected),
            "unresolved_fields": list(FIELDS)}

def persist_run(engine, state) -> list[str]:
    recorder = RunRecorder(engine, FIELDS)
    recorder.persist(state)
    return recorder.enqueue_distillation(state)

def make_worker(repo, embedder=None, distiller=None, **overrides) -> DistillationWorker:
    s = settings(**overrides)
    return DistillationWorker(repo, distiller or DeterministicDistiller(FIELDS, s), embedder,
                              s, FIELDS)
