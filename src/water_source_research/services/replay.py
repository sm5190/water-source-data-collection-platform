"""Saved-document replay of a research run (mode REPLAY).

Re-runs the research graph — plan, discover (no-op), acquire, extract, validate, resolve,
assemble, finalize — on a parent run's stored documents, introduced in their original
acquisition rounds (``acquisition/replay.py``), with the current retrieval, extraction (real
LLM calls) and resolution code. No discovery, no fetching, no human review (the replay stops
before people: it measures the automated stages).

Separation from the parent: a new run id; its own source_documents / evidence_chunks rows
(copied, insert-or-skip), claims, resolutions, records, run events and context bundles; no
checkpoints; a research_jobs row (mode REPLAY, parent_run_id, status SUCCEEDED) written when it
finishes so it can be evaluated like any run. The parent's rows are only read.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from water_source_research.acquisition.replay import ReplayAcquisition
from water_source_research.agents.graph import build_graph
from water_source_research.context_manager import ContextBundleStore, ContextService
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.field_state import initialize_city_state, unresolved_fields
from water_source_research.services.record_store import RecordRepository
from water_source_research.services.run_recorder import RunRecorder

REPLAY = "REPLAY"
_DROPPED = ("evidence_chunks",)  # kept in evidence_chunks, not duplicated in the job result

async def replay_run(engine: Engine, parent_run_id: str, gateway: Any,
                     max_rounds: int | None = None) -> tuple[str, dict[str, Any]]:
    """(replay run id, final state)."""
    with Session(engine) as session:
        parent = session.get(ResearchJobRecord, parent_run_id)
        if parent is None:
            raise LookupError(f"unknown research run {parent_run_id}")
        city, country, keys = parent.city_name, parent.country, parent.field_keys
        city_key = parent.city_key
    evidence = EvidenceRepository(engine)
    acquisition = ReplayAcquisition.from_run(evidence, parent_run_id, city)
    if acquisition.rounds == 0:
        raise LookupError(f"run {parent_run_id} has no stored documents to replay")
    rounds = max_rounds or acquisition.rounds
    graph = build_graph(
        gateway=gateway, context_service=ContextService(store=ContextBundleStore(engine)),
        run_recorder=RunRecorder(engine, enqueue_distillation_jobs=False),
        acquisition=acquisition, evidence_store=evidence, record_store=RecordRepository(engine),
        max_discovery_rounds=rounds)
    run_id = str(uuid4())
    state = initialize_city_state(city, country)
    started = datetime.now(UTC)
    final = await graph.ainvoke({
        "run_id": run_id, "city_name": city, "country": country,
        "research_state": state.model_dump(mode="json"),
        "unresolved_fields": unresolved_fields(state, keys),
    }, {"recursion_limit": 100})
    final = dict(final)
    final["replay"] = {"parent_run_id": parent_run_id, "rounds_served": acquisition.served,
                       "max_rounds": rounds, "reclassified": acquisition.reclassified}
    with Session(engine) as session, session.begin():
        session.add(ResearchJobRecord(
            run_id=run_id, city_name=city, country=country, city_key=city_key,
            status="SUCCEEDED", priority=0, attempts=1, max_attempts=1,
            result={k: v for k, v in final.items() if k not in _DROPPED},
            field_keys=keys, review_round=0, completion=final.get("completion"),
            mode=REPLAY, parent_run_id=parent_run_id, created_at=started,
            started_at=started, finished_at=datetime.now(UTC)))
    return run_id, final
