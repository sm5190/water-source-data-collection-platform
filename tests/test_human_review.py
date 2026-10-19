"""Persistent human review: the run pauses (interrupt) on open review items, is parked as
AWAITING_REVIEW, takes decisions through the API, resumes from its checkpoint and finalizes.
SQLite + InMemorySaver here; Postgres in tests/integration/test_human_review_pg.py."""

import asyncio
from uuid import uuid4
import pytest
from acquisition_helpers import make_acquisition
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from procedure_helpers import make_engine
from queue_helpers import Clock, Crash, make_queue, make_worker
from slice_helpers import ModelScript, make_discovery, make_gateway, make_site
from water_source_research.agents.graph import build_graph
from water_source_research.api.main import create_app
from water_source_research.research_queue.queue import JobStatus
from water_source_research.research_queue.worker import paused_round
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.review import ReviewRepository

W = "total_city_water_withdrawals_m3_day"
FIELDS = ["utility_name", "water_source", W, "water_type"]
# The withdrawal answer contradicts its own quote (INVALID); nothing supports water_type.
ANSWERS = {
    "utility_name": [{"value": "Toronto Water", "quote": "Toronto Water is the City of Toronto "
                      "division that provides drinking water", "confidence": 0.9}],
    "water_source": [{"value": "Lake Ontario", "quote": "Toronto draws its drinking water from "
                      "Lake Ontario", "confidence": 0.9}],
    W: [{"value": 450000, "quote": "produced an average of 980,000 cubic metres of drinking "
                                   "water per day", "confidence": 0.99}],
}

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path, "review.db")

class World:
    """Queue, API client, review-enabled graph sharing one checkpointer (a 'database')."""

    def __init__(self, engine, clock=None) -> None:
        self.engine = engine
        self.queue = make_queue(engine, clock)
        self.checkpoints = InMemorySaver()
        self.evidence = EvidenceRepository(engine)
        self.reviews = ReviewRepository(engine)
        self.client = TestClient(create_app(queue=self.queue, evidence=self.evidence,
                                            reviews=self.reviews))
        self.run_id = self.client.post("/research/city", json={
            "city_name": "Toronto", "country": "Canada", "field_keys": FIELDS}).json()["run_id"]

    def graph(self):
        gateway, _ = make_gateway(ModelScript(answers=ANSWERS), structured_max_attempts=1)
        return build_graph(gateway=gateway, discovery=make_discovery(),
                           acquisition=make_acquisition(make_site()), max_discovery_rounds=1,
                           evidence_store=self.evidence, review_store=self.reviews,
                           checkpointer=self.checkpoints)

    def work(self, worker_id=None) -> int:
        return asyncio.run(make_worker(self.queue, self.graph(), worker_id).drain())

    def job(self):
        return self.queue.get(self.run_id)

    def pending(self) -> dict:
        return self.client.get(f"/research/runs/{self.run_id}/review/pending").json()

    def item(self, field_key: str) -> dict:
        return next(i for i in self.pending()["items"] if i["field_key"] == field_key)

    def decide(self, *decisions: dict, reviewer="analyst@example.org"):
        return self.client.post(f"/research/runs/{self.run_id}/review/decisions",
                                json={"reviewer": reviewer, "decisions": list(decisions)})

    def resume(self):
        return self.client.post(f"/research/runs/{self.run_id}/review/resume",
                                json={"reviewer": "analyst@example.org"})

def decision(item: dict, kind: str, **kw) -> dict:
    return {"decision_key": kw.pop("decision_key", uuid4().hex), "item_id": item["id"],
            "round": item["round"], "decision": kind, **kw}

def withdrawal_quote_evidence(item: dict) -> dict:
    cite = item["payload"]["candidates"][0]["citations"][0]
    assert cite["chunk_id"]
    return {"chunk_id": cite["chunk_id"], "quote": "980,000 cubic metres"}

# --- pause, decide, resume, finalize ----------------------------------------------------------

def test_run_pauses_for_review_and_finishes_after_decisions(engine):
    w = World(engine)
    assert w.work() == 1
    job = w.job()
    assert job.status is JobStatus.AWAITING_REVIEW and job.review_round == 1
    # Not failed, not complete: the worker is free and the checkpoint waits.
    assert job.finished_at is None and job.lease_owner is None
    pending = w.pending()
    assert {(i["field_key"], i["entity_key"]) for i in pending["items"]} == {
        (W, "city"), ("water_type", "*")}
    withdrawal = w.item(W)
    assert "VALUE_IN_QUOTE" in withdrawal["reason_codes"]  # why it failed validation
    [candidate] = withdrawal["payload"]["candidates"]
    assert candidate["validation"]["outcome"] == "INVALID"
    # every claim the item names is already stored
    stored = {c["claim_uid"] for c in w.evidence.claims(w.run_id)}
    assert candidate["claim_uid"] in stored
    # a partial result is readable while waiting
    result = w.client.get(f"/research/runs/{w.run_id}/result").json()
    assert result["partial"]
    assert result["research_state"]["fields"][W]["status"] == "HITL_REQUIRED"

    # resuming before every item has a decision is refused
    r = w.resume()
    assert r.status_code == 409 and len(r.json()["detail"]["undecided_items"]) == 2

    correct = decision(withdrawal, "CORRECT", value=980000, reason="the quote says 980,000",
                       claim_uids=[candidate["claim_uid"]],
                       evidence=withdrawal_quote_evidence(withdrawal))
    unknown = decision(w.item("water_type"), "MARK_UNKNOWN",
                       reason="no source in this run names the water type")
    r = w.decide(correct, unknown)
    assert r.status_code == 200, r.text
    assert all(d["created"] for d in r.json()["decisions"])
    assert w.resume().status_code == 202
    assert w.job().status is JobStatus.QUEUED

    assert w.work() == 1
    job = w.job()
    assert job.status is JobStatus.SUCCEEDED and job.review_round == 1
    states = {s["field_key"]: s for s in w.evidence.field_states(w.run_id)}
    assert states[W]["status"] == "RESOLVED" and not states[W]["review_required"]
    assert states[W]["resolution"]["decided_by"] == "REVIEWER"
    assert states["water_type"]["status"] == "UNAVAILABLE"
    assert states["utility_name"]["resolution"]["decided_by"] == "RULES"

    claims = [c for c in w.evidence.claims(w.run_id) if c["field_key"] == W]
    corrected = next(c for c in claims if c["value"] == 980000)
    original = next(c for c in claims if c["value"] == 450000)  # kept, not overwritten
    assert corrected["is_selected"] and not original["is_selected"]
    assert corrected["citations"][0]["chunk_id"] == correct["evidence"]["chunk_id"]
    assert corrected["citations"][0]["quote"] == "980,000 cubic metres"

    history = w.client.get(f"/research/runs/{w.run_id}/review/history").json()
    assert {d["decision"] for d in history["decisions"]} == {"CORRECT", "MARK_UNKNOWN"}
    corr = next(d for d in history["decisions"] if d["decision"] == "CORRECT")
    assert corr["reviewer"] == "analyst@example.org" and corr["original_value"] == 450000
    assert corr["reviewed_value"] == 980000 and corr["status_before"] == "HITL_REQUIRED"
    applied = {a["field_key"]: a for a in history["applied"]}
    assert applied[W]["review_decision_id"] == corr["id"]
    assert applied[W]["selected_claim_uids"] == [corrected["claim_uid"]]
    assert applied["water_type"]["to_status"] == "UNAVAILABLE"
    assert all(i["status"] == "APPLIED" for i in history["items"])

    result = w.client.get(f"/research/runs/{w.run_id}/result").json()
    hitl = [e for e in result["events"] if e["kind"] == "HITL"]
    assert {(e["field_key"], e["result"]) for e in hitl} == {(W, "CORRECTED"),
                                                             ("water_type", "SKIPPED")}

# --- decision validation ----------------------------------------------------------------------

def test_invalid_stale_and_duplicate_decisions(engine):
    w = World(engine)
    w.work()
    withdrawal = w.item(W)
    uid = withdrawal["payload"]["candidates"][0]["claim_uid"]

    # a claim that failed validation can't be approved
    r = w.decide(decision(withdrawal, "APPROVE", claim_uids=[uid]))
    assert r.status_code == 422 and "use CORRECT" in str(r.json())
    # corrections need a reason and evidence that really is in the run
    r = w.decide(decision(withdrawal, "CORRECT", value=980000, reason="x",
                          evidence={"chunk_id": withdrawal_quote_evidence(withdrawal)
                                    ["chunk_id"], "quote": "1,200,000 cubic metres"}))
    assert r.status_code == 422 and "does not occur" in str(r.json())
    r = w.decide(decision(withdrawal, "CORRECT", value=980000, reason="x"))
    assert r.status_code == 422
    r = w.decide(decision(withdrawal, "CORRECT", value="lots", reason="x",
                          evidence={"url": "https://example.org/report"}))
    assert r.status_code == 422 and "not a number" in str(r.json())
    r = w.decide(decision(w.item("water_type"), "MARK_NA"))
    assert r.status_code == 422 and "reason" in str(r.json())
    # claims that weren't shown can't be named
    r = w.decide(decision(withdrawal, "REJECT", claim_uids=["not-a-claim"]))
    assert r.status_code == 422
    # stale round
    r = w.decide(decision(withdrawal, "REJECT", claim_uids=[uid], round=2))
    assert r.status_code == 409

    # idempotent per decision_key; reusing a key for something else is refused
    reject = decision(withdrawal, "REJECT", claim_uids=[uid], reason="wrong number",
                      decision_key="key-reject-0001")
    assert w.decide(reject).json()["decisions"][0]["created"]
    again = w.decide(reject)
    assert again.status_code == 200 and not again.json()["decisions"][0]["created"]
    other = decision(withdrawal, "DEFER", reason="later", decision_key="key-reject-0001")
    assert w.decide(other).status_code == 409
    assert len(w.reviews.decisions(w.run_id)) == 1

def test_reviewer_supplied_source_is_marked_as_such(engine):
    w = World(engine)
    w.work()
    withdrawal = w.item(W)
    w.decide(decision(withdrawal, "CORRECT", value=980000, reason="2023 annual report",
                      evidence={"url": "https://www.toronto.ca/annual-report-2023.pdf",
                                "title": "Toronto Water Annual Report 2023",
                                "note": "Table 2: 980 ML/d average"}),
             decision(w.item("water_type"), "MARK_NA", reason="test"))
    w.resume()
    w.work()
    claim = next(c for c in w.evidence.claims(w.run_id)
                 if c["field_key"] == W and c["value"] == 980000)
    assert claim["qualifiers"]["source_types"] == ["REVIEWER_SUPPLIED"]
    assert claim["citations"][0]["url"] == "https://www.toronto.ca/annual-report-2023.pdf"
    assert claim["citations"][0]["chunk_id"] is None  # not machine-verified evidence

# --- several rounds ---------------------------------------------------------------------------

def test_rejection_sends_the_field_back_and_opens_a_second_round(engine):
    w = World(engine)
    w.work()
    withdrawal = w.item(W)
    uid = withdrawal["payload"]["candidates"][0]["claim_uid"]
    w.decide(decision(withdrawal, "REJECT", claim_uids=[uid], reason="contradicts its quote"),
             decision(w.item("water_type"), "MARK_NA", reason="test"))
    assert w.resume().status_code == 202
    w.work()
    job = w.job()
    assert job.status is JobStatus.AWAITING_REVIEW and job.review_round == 2
    [item] = w.pending()["items"]
    assert (item["field_key"], item["round"], item["reason_codes"]) == (
        W, 2, ["ALL_CLAIMS_REJECTED"])
    assert item["payload"]["allowed_decisions"] == ["CORRECT", "MARK_UNKNOWN", "MARK_NA",
                                                    "DEFER"]
    w.decide(decision(item, "DEFER", reason="ask the utility for the 2023 figure"))
    w.resume()
    w.work()
    assert w.job().status is JobStatus.SUCCEEDED
    states = {s["field_key"]: s for s in w.evidence.field_states(w.run_id)}
    assert states[W]["status"] == "UNAVAILABLE"
    assert states["water_type"]["status"] == "NOT_APPLICABLE"
    rejected = next(c for c in w.evidence.claims(w.run_id) if c["field_key"] == W)
    assert not rejected["is_selected"]  # kept for the audit trail, never selected

# --- queue lifecycle and recovery -------------------------------------------------------------

def test_waiting_run_is_the_active_run_and_can_be_cancelled(engine):
    w = World(engine)
    w.work()
    again = w.client.post("/research/city", json={"city_name": "Toronto", "country": "Canada"})
    assert again.status_code == 200 and again.json()["run_id"] == w.run_id
    assert w.work() == 0  # nothing claimable while waiting for review
    assert w.client.post(f"/research/runs/{w.run_id}/cancel").json()["status"] == "CANCELLED"
    assert w.resume().status_code == 409

def test_review_survives_a_worker_restart(engine):
    """A different worker (the original one is gone) resumes from the shared checkpoint."""
    w = World(engine)
    w.work("worker-a")
    w.decide(decision(w.item(W), "DEFER", reason="later"),
             decision(w.item("water_type"), "DEFER", reason="later"))
    w.resume()
    w.work("worker-b")
    assert w.job().status is JobStatus.SUCCEEDED

def test_crash_after_resume_does_not_apply_decisions_twice(engine):
    clock = Clock()
    w = World(engine, clock)
    w.work()
    w.decide(decision(w.item(W), "DEFER", reason="later"),
             decision(w.item("water_type"), "MARK_UNKNOWN", reason="none"))
    w.resume()
    original = w.evidence.save_results
    crashed = {"done": False}

    def crash_in_finalize(*args, **kwargs):
        if not crashed["done"]:
            crashed["done"] = True
            raise Crash("worker died while finalizing")
        return original(*args, **kwargs)

    w.evidence.save_results = crash_in_finalize

    async def dying():
        worker = make_worker(w.queue, w.graph(), "dying")
        await worker._fill_slots()
        await asyncio.gather(*worker.in_flight, return_exceptions=True)

    asyncio.run(dying())
    assert w.job().status is JobStatus.RUNNING  # the worker "died"
    clock.advance(31)
    w.work("rescuer")
    assert w.job().status is JobStatus.SUCCEEDED
    reviewer_rows = [r for r in w.evidence.resolutions(w.run_id) if r["decided_by"] == "REVIEWER"]
    assert sorted(r["field_key"] for r in reviewer_rows) == [W, "water_type"]

def test_pause_that_was_never_recorded_is_parked_again(engine):
    """Crash between the interrupt and await_review: the next worker re-parks the run
    instead of resuming it without decisions."""
    clock = Clock()
    w = World(engine, clock)
    original = w.queue.await_review

    def crash(*args, **kwargs):
        raise Crash("died before recording the pause")

    w.queue.await_review = crash

    async def dying():
        worker = make_worker(w.queue, w.graph(), "dying")
        await worker._fill_slots()
        await asyncio.gather(*worker.in_flight, return_exceptions=True)

    asyncio.run(dying())
    w.queue.await_review = original
    clock.advance(31)
    w.work("rescuer")
    job = w.job()
    assert job.status is JobStatus.AWAITING_REVIEW and job.review_round == 1
    assert w.reviews.decisions(w.run_id) == []
    snapshot = asyncio.run(w.graph().aget_state({"configurable": {"thread_id": w.run_id}}))
    assert paused_round(snapshot) == 1

def test_review_store_requires_a_checkpointer(engine):
    with pytest.raises(ValueError, match="checkpointer"):
        build_graph(review_store=ReviewRepository(engine))
