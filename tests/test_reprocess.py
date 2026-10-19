"""Reprocessing a run produced before validation/review/records existed: a new REPROCESS run
re-validates the parent's stored claims and evidence (no discovery, fetching or model calls),
pauses for review, resumes, assembles and exports — and the parent's rows are not touched."""

import asyncio
from uuid import uuid4
import pytest
from acquisition_helpers import make_acquisition
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from procedure_helpers import make_engine
from queue_helpers import make_queue, make_worker
from review_helpers import ANSWERS, FIELDS, W
from slice_helpers import ModelScript, make_discovery, make_gateway, make_site
from sqlalchemy import update
from sqlalchemy.orm import Session
from water_source_research.agents.graph import build_graph
from water_source_research.api.main import create_app
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.research_queue.queue import JobStatus
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.record_store import RecordRepository
from water_source_research.services.review import ReviewRepository

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path, "reprocess.db")

class Forbidden:
    """Gateway and discovery stand-ins that fail the test if a reprocess calls them."""

    settings = None

    async def extract(self, *a, **k):
        raise AssertionError("reprocessing must not call the LLM")

    async def discover(self, *a, **k):
        raise AssertionError("reprocessing must not run discovery")

def legacy_parent(engine, queue) -> str:
    """A finished run as the earlier code left it: claims without ids or validation, fields
    CANDIDATE, no resolution, no records (built by running extraction, then stripping)."""
    run_id = queue.submit("Toronto", "Canada", field_keys=FIELDS).job.run_id
    gateway, _ = make_gateway(ModelScript(answers=ANSWERS), structured_max_attempts=1)
    graph = build_graph(gateway=gateway, discovery=make_discovery(),
                        acquisition=make_acquisition(make_site()), max_discovery_rounds=1,
                        evidence_store=EvidenceRepository(engine), checkpointer=InMemorySaver())
    asyncio.run(make_worker(queue, graph).drain())
    result = dict(queue.result(run_id))
    for fs in result["research_state"]["fields"].values():
        fs["resolution"] = None
        fs["selected_claim"] = None
        fs["status"] = "CANDIDATE" if fs["candidate_claims"] else "UNRESOLVED"
        for c in fs["candidate_claims"]:
            for k in ("claim_uid", "original_value", "normalized_value", "qualifiers",
                      "validation", "entity_key"):
                c.pop(k, None)
    for k in ("resolution_log", "record_set", "completion"):
        result.pop(k, None)
    with Session(engine) as s, s.begin():
        s.execute(update(ResearchJobRecord).where(ResearchJobRecord.run_id == run_id)
                  .values(result=result, completion=None))
    return run_id

def reprocess_graph(engine, checkpoints, site):
    return build_graph(gateway=Forbidden(), discovery=Forbidden(),
                       acquisition=make_acquisition(site), max_discovery_rounds=1,
                       evidence_store=EvidenceRepository(engine),
                       review_store=ReviewRepository(engine),
                       record_store=RecordRepository(engine), checkpointer=checkpoints)

def test_reprocess_reuses_stored_evidence_reviews_and_exports(engine):
    queue = make_queue(engine)
    parent = legacy_parent(engine, queue)
    evidence = EvidenceRepository(engine)
    parent_counts = evidence.counts(parent)
    parent_claims = evidence.claims(parent)
    client = TestClient(create_app(queue=queue, evidence=evidence))

    r = client.post(f"/research/runs/{parent}/reprocess")
    assert r.status_code == 202
    child = r.json()["run_id"]
    assert (r.json()["mode"], r.json()["parent_run_id"]) == ("REPROCESS", parent)

    site = make_site()
    checkpoints = InMemorySaver()
    asyncio.run(make_worker(queue, reprocess_graph(engine, checkpoints, site)).drain())
    job = queue.get(child)
    assert job.status is JobStatus.AWAITING_REVIEW
    assert site.fetched() == []  # nothing fetched again (and Forbidden wasn't called)

    # Evidence copied into the new run; claims stored with ids and validation.
    counts = evidence.counts(child)
    assert counts["documents"] == parent_counts["documents"]
    assert counts["chunks"] == parent_counts["chunks"]
    claims = evidence.claims(child)
    assert claims and all(c["claim_uid"] and c["validation_outcome"] for c in claims)
    linked = [cite for c in claims for cite in c["citations"]]
    assert linked and all(cite["chunk_id"] for cite in linked)
    steps = [s["node"] for s in queue.steps(child)]
    assert steps[0] == "validate" and not {"plan", "discover", "acquire", "extract"} & set(steps)

    # Review in the new run, resume, assemble, export.
    items = {i["field_key"]: i for i in client.get(
        f"/research/runs/{child}/review/pending").json()["items"]}
    assert set(items) == {W, "water_type"}
    cite = items[W]["payload"]["candidates"][0]["citations"][0]
    client.post(f"/research/runs/{child}/review/decisions", json={
        "reviewer": "analyst@example.org", "decisions": [
            {"decision_key": uuid4().hex, "item_id": items[W]["id"], "round": 1,
             "decision": "CORRECT", "value": 980000, "reason": "the quote says 980,000",
             "evidence": {"chunk_id": cite["chunk_id"], "quote": "980,000 cubic metres"}},
            {"decision_key": uuid4().hex, "item_id": items["water_type"]["id"], "round": 1,
             "decision": "MARK_UNKNOWN", "reason": "not stated"}]}).raise_for_status()
    assert client.post(f"/research/runs/{child}/review/resume",
                       json={"reviewer": "analyst@example.org"}).status_code == 202
    asyncio.run(make_worker(queue, reprocess_graph(engine, checkpoints, site)).drain())
    job = queue.get(child)
    assert job.status is JobStatus.SUCCEEDED and job.completion == "FINALIZED_WITH_GAPS"
    export = client.get(f"/research/runs/{child}/export", params={"format": "csv"})
    assert export.status_code == 200 and b"Lake Ontario" in export.content

    # The parent run is untouched.
    assert evidence.counts(parent) == parent_counts
    assert evidence.claims(parent) == parent_claims
    assert queue.get(parent).completion is None

def test_reprocess_rebuilds_claims_from_tables_when_the_result_is_missing(engine):
    queue = make_queue(engine)
    parent = legacy_parent(engine, queue)
    with Session(engine) as s, s.begin():
        s.execute(update(ResearchJobRecord).where(ResearchJobRecord.run_id == parent)
                  .values(result={"unresolved_fields": FIELDS}))
    child = queue.submit_reprocess(parent).job.run_id
    asyncio.run(make_worker(queue, reprocess_graph(engine, InMemorySaver(),
                                                   make_site())).drain())
    assert queue.get(child).status is JobStatus.AWAITING_REVIEW
    stored = {c["field_key"] for c in EvidenceRepository(engine).claims(child)}
    assert {"utility_name", "water_source", W} <= stored

def test_only_finished_runs_can_be_reprocessed(engine):
    queue = make_queue(engine)
    run_id = queue.submit("Oslo", "Norway").job.run_id
    client = TestClient(create_app(queue=queue))
    assert client.post(f"/research/runs/{run_id}/reprocess").status_code == 409
    assert client.post("/research/runs/nope/reprocess").status_code == 404
