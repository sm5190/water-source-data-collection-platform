"""Revalidation (mode REVALIDATE): stored claims as extracted re-run through validation and
resolution with no model calls; it reproduces the parent's automatic decisions, leaves the
parent untouched, leaves reviewer corrections out, and is kept apart from research runs."""

import asyncio
import pytest
from sqlalchemy.orm import Session
from test_replay import PARENT, Gateway, engine, snapshot_parent  # noqa: F401 (fixture)
from water_source_research.db.evidence_models import FieldClaimRecord
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.evaluation.store import is_revalidation
from water_source_research.observability import monitoring
from water_source_research.services.replay import replay_run
from water_source_research.services.revalidate import (
    claims_as_extracted, compare_decisions, revalidate_run,
)

@pytest.fixture
def researched(engine):  # noqa: F811
    """A finished run with extracted claims and automatic decisions (made by a replay)."""
    run_id, _ = asyncio.run(replay_run(engine, PARENT, Gateway()))
    return run_id

def _snapshot(engine, run_id):  # noqa: F811
    import test_replay
    old, test_replay.PARENT = test_replay.PARENT, run_id
    try:
        return snapshot_parent(engine)
    finally:
        test_replay.PARENT = old

def test_revalidation_reproduces_the_automatic_decisions(engine, researched):  # noqa: F811
    before = _snapshot(engine, researched)
    run_id, final = asyncio.run(revalidate_run(engine, researched))
    assert final["revalidation"]["claims"] > 0
    assert compare_decisions(engine, researched, run_id) == []
    assert _snapshot(engine, researched) == before  # the parent is only read
    with Session(engine) as s:
        job = s.get(ResearchJobRecord, run_id)
        assert (job.mode, job.parent_run_id, job.status) == ("REVALIDATE", researched,
                                                             "SUCCEEDED")

def test_revalidation_makes_no_model_calls(engine, researched, monkeypatch):  # noqa: F811
    from water_source_research.agents import graph as graph_module

    called = []

    def extract_node(*a, **k):
        async def node(state):
            called.append(True)
            raise AssertionError("revalidation must not extract")
        return node
    monkeypatch.setattr(graph_module, "make_extract_node", extract_node)
    # (the graph's default gateway also raises on any extraction)
    _, final = asyncio.run(revalidate_run(engine, researched))
    assert final.get("reprocess_of") == researched and not called

def test_reviewer_corrections_are_left_out(engine, researched):  # noqa: F811
    with Session(engine) as s, s.begin():
        s.add(FieldClaimRecord(run_id=researched, field_key="water_type", value="Surface",
                               value_status="KNOWN", value_origin="REPORTED", confidence=1.0,
                               claim_uid="reviewer-correction", review_status="CORRECTED"))
    from water_source_research.services.evidence_store import EvidenceRepository
    stored = EvidenceRepository(engine).claims(researched)
    research, counts = claims_as_extracted(stored, "Nairobi", "Kenya")
    assert counts["excluded_corrected"] == 1
    assert not research.fields["water_type"].candidate_claims
    assert all(c.review_status is None for fs in research.fields.values()
               for c in fs.candidate_claims)

def test_revalidation_runs_are_kept_apart_from_research_runs(engine, researched):  # noqa: F811
    run_id, _ = asyncio.run(revalidate_run(engine, researched))
    listed = {r["run_id"] for r in monitoring.runs(engine)}
    assert run_id not in listed and researched in listed
    assert run_id in {r["run_id"] for r in monitoring.runs(engine, include_revalidation=True)}
    assert is_revalidation({"config": {"research_mode": "REVALIDATE"}})
    assert not is_revalidation({"config": {"research_mode": "REPLAY"}})
    assert not is_revalidation({"config": {}})
