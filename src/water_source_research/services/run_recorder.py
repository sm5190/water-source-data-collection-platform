"""Persist a finished run's trusted events and enqueue distillation jobs (soft post-processing).

Called from graph finalize after the scientific result is complete; failures here must never
change that result. Distillation itself runs later in the worker.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from water_source_research.core.versions import DISTILLER_VERSION, WORKFLOW_VERSION
from water_source_research.db.models import utcnow
from water_source_research.db.research_models import (
    DistillationJobRecord, ResearchRunRecord, RunEventRecord,
)
from water_source_research.procedures.trajectory import distillation_key, trajectory_id
from water_source_research.schemas.events import EventKind, EventResult, WorkflowEvent
from water_source_research.schemas.procedures import task_type_for
from water_source_research.schemas.research import FieldDefinition
from water_source_research.services.field_state import load_field_definitions

class RunRecorder:
    def __init__(self, engine: Engine,
                 field_definitions: Mapping[str, FieldDefinition] | None = None) -> None:
        self.engine = engine
        self.fields = dict(field_definitions or load_field_definitions())

    def persist(self, state: Mapping[str, Any]) -> int:
        """Upsert the run row and insert any events not yet stored. Returns events inserted."""
        run_id = state["run_id"]
        events = [WorkflowEvent.model_validate(e) for e in state.get("events", [])]
        statuses = {k: f.get("status") for k, f in
                    (state.get("research_state") or {}).get("fields", {}).items()}
        extractable = [k for k in statuses if k in self.fields and not self.fields[k].derived]
        values = {
            "city_name": state["city_name"], "country": state.get("country"),
            "workflow_version": WORKFLOW_VERSION,
            "outcome": "COMPLETED" if state.get("finalized") else "FAILED",
            "fields_total": len(extractable),
            "fields_resolved": sum(statuses[k] == "RESOLVED" for k in extractable),
            "fields_conflicted": sum(statuses[k] == "CONFLICT" for k in extractable),
            "human_review_required": bool(state.get("needs_human_review")),
            "human_correction_count": sum(e.kind is EventKind.HITL
                                       and e.result is EventResult.CORRECTED for e in events),
            "rejected_claim_count": sum(e.kind is EventKind.VALIDATION
                                     and e.result is EventResult.REJECTED for e in events),
            "started_at": datetime.fromisoformat(events[0].created_at) if events else None,
            "finished_at": utcnow(),
        }
        with Session(self.engine) as session, session.begin():
            run = session.get(ResearchRunRecord, run_id)
            if run is None:
                session.add(ResearchRunRecord(run_id=run_id, **values))
            else:
                for key, value in values.items():
                    setattr(run, key, value)
            session.flush()
            stored = set(session.scalars(select(RunEventRecord.seq)
                                         .where(RunEventRecord.run_id == run_id)))
            new = [e for e in events if e.seq not in stored]
            session.add_all(RunEventRecord(
                run_id=run_id, seq=e.seq, kind=e.kind.value, node=e.node,
                field_key=e.field_key, capability=e.capability.value if e.capability else None,
                provider=e.provider, strategy=e.strategy,
                result=e.result.value if e.result else None, attrs=e.attrs,
                created_at=datetime.fromisoformat(e.created_at),
            ) for e in new)
            return len(new)

    def enqueue_distillation(self, state: Mapping[str, Any]) -> list[str]:
        """One job per field type with at least one accepted field. Idempotent per key."""
        if not state.get("finalized"):
            return []
        run_id = state["run_id"]
        fields = (state.get("research_state") or {}).get("fields", {})
        field_types = sorted({
            self.fields[k].field_type for k, f in fields.items()
            if k in self.fields and not self.fields[k].derived and f.get("status") == "RESOLVED"
        })
        keys = []
        for field_type in field_types:
            task_type = task_type_for(field_type)
            tid = trajectory_id(run_id, task_type, field_type, WORKFLOW_VERSION)
            key = distillation_key(tid, DISTILLER_VERSION)
            try:
                with Session(self.engine) as session, session.begin():
                    session.add(DistillationJobRecord(
                        distillation_key=key, trajectory_id=tid, run_id=run_id,
                        task_type=task_type, field_type=field_type,
                        workflow_version=WORKFLOW_VERSION, distiller_version=DISTILLER_VERSION,
                    ))
            except IntegrityError:
                pass  # already enqueued (retry or duplicate finalize)
            keys.append(key)
        return keys
