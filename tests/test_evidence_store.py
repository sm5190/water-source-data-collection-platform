"""Persistence of a real run: API submit -> research worker -> graph -> database rows, and the
result/provenance/evidence endpoints reading them back. SQLite here; Postgres in
tests/integration/test_evidence_store_pg.py."""

import asyncio
import pytest
from acquisition_helpers import MemoryArtifacts, make_acquisition
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from procedure_helpers import make_engine
from queue_helpers import make_queue, make_worker
from slice_helpers import (
    PDF_URL, TARGETS, UTIL_URL, ModelScript, initial_state, make_discovery, make_gateway,
    make_site,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from water_source_research.agents.graph import build_graph
from water_source_research.api.main import create_app
from water_source_research.context_manager import ContextBundleStore, ContextService
from water_source_research.db.models import ContextBundleRecord
from water_source_research.db.research_models import (
    DistillationJobRecord, ResearchRunRecord, RunEventRecord,
)
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.run_recorder import RunRecorder

def persisted_graph(engine, artifacts=None, evidence=None, script=None, checkpointer=None):
    gateway, _ = make_gateway(script or ModelScript(), structured_max_attempts=1)
    return build_graph(
        gateway=gateway, discovery=make_discovery(),
        acquisition=make_acquisition(make_site(), artifact_store=artifacts),
        evidence_store=evidence or EvidenceRepository(engine),
        context_service=ContextService(store=ContextBundleStore(engine)),
        run_recorder=RunRecorder(engine, enqueue_distillation_jobs=False),
        max_discovery_rounds=2, checkpointer=checkpointer)

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path, "evidence.db")

def count(engine, model, **where):
    with Session(engine) as s:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return s.scalar(q)

# --- API -> worker -> DB -> endpoints ---------------------------------------------------------

def test_api_submission_processed_by_worker_and_exposed(engine):
    queue = make_queue(engine)
    artifacts = MemoryArtifacts()
    client = TestClient(create_app(queue=queue, evidence=EvidenceRepository(engine)))

    bad = client.post("/research/city", json={"city_name": "Toronto", "country": "Canada",
                                              "field_keys": ["utility_name", "bogus_field"]})
    assert bad.status_code == 422

    r = client.post("/research/city", json={"city_name": "Toronto", "country": "Canada",
                                            "field_keys": TARGETS})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert r.json()["field_keys"] == TARGETS

    graph = persisted_graph(engine, artifacts, checkpointer=InMemorySaver())
    assert asyncio.run(make_worker(queue, graph).drain()) == 1
    assert client.get(f"/research/runs/{run_id}").json()["status"] == "SUCCEEDED"

    result = client.get(f"/research/runs/{run_id}/result").json()
    assert result["unresolved_fields"] == TARGETS
    assert {d["requested_url"] for d in result["acquired_documents"]} >= {UTIL_URL, PDF_URL}
    assert {c["field_key"] for c in result["claims"]} == set(TARGETS)

    fields = {f["field_key"]: f for f in client.get(f"/research/runs/{run_id}/fields")
              .json()["fields"]}
    ws = fields["water_source"]
    assert ws["status"] == "CANDIDATE" and ws["review_required"]
    assert {c["value"] for c in ws["candidates"]} == {"Lake Ontario",
                                                      "Groundwater wells (Toronto Island)"}
    assert ws["candidates"][0]["citations"][0]["acquisition_method"] == "DIRECT_HTTP"
    assert not fields["water_type"]["researched"]

    evidence = client.get(f"/research/runs/{run_id}/evidence").json()
    docs = {d["requested_url"]: d for d in evidence["documents"]}
    assert docs[PDF_URL]["status"] == "OK" and docs[PDF_URL]["page_count"] == 2
    assert docs[PDF_URL]["raw_object_key"].endswith(".pdf")
    assert artifacts.objects[docs[PDF_URL]["raw_object_key"]].startswith(b"%PDF-")
    assert evidence["counts"]["field_states"] == 3  # only the researched subset
    assert evidence["counts"]["chunks"] > 0

    chunks = client.get(f"/research/runs/{run_id}/evidence/chunks",
                        params={"document_id": docs[PDF_URL]["document_id"]}).json()["chunks"]
    assert {c["page_number"] for c in chunks} == {1, 2}

    stored = client.get(f"/research/runs/{run_id}/claims").json()
    withdrawal = next(c for c in stored["claims"]
                      if c["field_key"] == "total_city_water_withdrawals_m3_day")
    cite = withdrawal["citations"][0]
    assert withdrawal["value"] == 980000 and not withdrawal["is_selected"]
    assert cite["url"] == PDF_URL and cite["page_number"] == 2
    assert cite["chunk_id"] in {c["chunk_id"] for c in chunks}  # linked to the exact chunk
    assert cite["source_date"] == "2024-03-15"
    assert cite["raw_object_key"] == docs[PDF_URL]["raw_object_key"]
    assert {s["field_key"]: s["review_required"] for s in stored["field_states"]} == {
        k: True for k in TARGETS}

    # run/events always recorded; no distillation jobs with procedure memory off
    assert count(engine, ResearchRunRecord, run_id=run_id) == 1
    assert count(engine, RunEventRecord, run_id=run_id) > 0
    assert count(engine, DistillationJobRecord) == 0
    assert count(engine, ContextBundleRecord) >= 3  # one per extracted field at least

    discovery = client.get(f"/research/runs/{run_id}/discovery").json()
    assert discovery["available"] and discovery["provider_status"]["searxng"] == "ok"
    assert UTIL_URL in {c["url"] for c in discovery["candidates"]}

    steps = client.get(f"/research/runs/{run_id}/steps").json()["steps"]
    acquire = next(s for s in steps if s["node"] == "acquire")
    assert acquire["summary"]["documents_ok"] >= 2

def test_api_without_field_keys_keeps_existing_behavior(engine):
    queue = make_queue(engine)
    client = TestClient(create_app(queue=queue, evidence=EvidenceRepository(engine)))
    r = client.post("/research/city", json={"city_name": "Toronto"})
    assert r.status_code == 202 and r.json()["field_keys"] is None

# --- idempotency and failure semantics -------------------------------------------------------

def test_rerunning_finalize_and_acquire_does_not_duplicate_rows(engine):
    graph = persisted_graph(engine)
    state = initial_state()
    first = asyncio.run(graph.ainvoke(state))
    repo = EvidenceRepository(engine)
    before = repo.counts("run-toronto")
    # Simulate a retried/resumed run writing the same evidence and results again.
    acquired = first["acquired_documents"]
    from water_source_research.schemas.research import EvidenceChunk
    chunks = [EvidenceChunk.model_validate(c) for c in first["evidence_chunks"]]
    repo.save_documents("run-toronto", acquired, chunks)
    repo.save_results("run-toronto", first["research_state"],
                      first["fields_needing_evidence"], first["unresolved_fields"])
    assert repo.counts("run-toronto") == before
    assert before["claims"] == 4 and before["citations"] == 4

def test_review_approval_persists_selected_claim(engine):
    graph = persisted_graph(engine)
    asyncio.run(graph.ainvoke(initial_state(review_decisions=[
        {"field_key": "utility_name", "decision": "APPROVED"}])))
    repo = EvidenceRepository(engine)
    states = {s["field_key"]: s for s in repo.field_states("run-toronto")}
    assert states["utility_name"]["status"] == "RESOLVED"
    assert not states["utility_name"]["review_required"]
    selected = [c for c in repo.claims("run-toronto") if c["is_selected"]]
    assert [c["field_key"] for c in selected] == ["utility_name"]
    assert states["utility_name"]["selected_claim_id"] == selected[0]["claim_id"]

def test_minio_failure_keeps_evidence(engine):
    graph = persisted_graph(engine, artifacts=MemoryArtifacts(fail=True))
    result = asyncio.run(graph.ainvoke(initial_state()))
    docs = {d["requested_url"]: d for d in EvidenceRepository(engine).documents("run-toronto")}
    assert docs[UTIL_URL]["status"] == "OK" and docs[UTIL_URL]["raw_object_key"] is None
    assert "minio down" in docs[UTIL_URL]["artifact_error"]
    assert result["research_state"]["fields"]["utility_name"]["status"] == "CANDIDATE"

def test_database_failure_fails_the_run(engine):
    class BrokenStore(EvidenceRepository):
        def save_documents(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    graph = persisted_graph(engine, evidence=BrokenStore(engine))
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(graph.ainvoke(initial_state()))

def test_failed_run_is_retried_by_worker_without_duplicates(engine):
    """First attempt dies on a DB write after acquisition; the retry completes cleanly."""
    class FlakyStore(EvidenceRepository):
        failures = 1

        def save_results(self, *args, **kwargs):
            if FlakyStore.failures:
                FlakyStore.failures -= 1
                raise RuntimeError("transient DB error")
            return super().save_results(*args, **kwargs)

    queue = make_queue(engine)
    run_id = queue.submit("Toronto", "Canada", field_keys=TARGETS).job.run_id
    graph = persisted_graph(engine, evidence=FlakyStore(engine), checkpointer=InMemorySaver())
    worker = make_worker(queue, graph)
    asyncio.run(worker.drain())
    assert queue.get(run_id).status.value == "SUCCEEDED"
    assert queue.get(run_id).attempts == 2
    repo = EvidenceRepository(engine)
    counts = repo.counts(run_id)
    assert counts["claims"] == 4 and counts["field_states"] == 3
    urls = [d["requested_url"] for d in repo.documents(run_id)]
    assert len(urls) == len(set(urls))
