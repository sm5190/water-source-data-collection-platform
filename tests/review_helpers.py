"""A review-enabled city run for tests: queue, API client and a graph with persistent review
(InMemorySaver shared across workers). The model's withdrawal answer contradicts its own quote
(INVALID) and nothing supports water_type, so round 1 has two items."""

import asyncio
from uuid import uuid4
from acquisition_helpers import make_acquisition
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from queue_helpers import make_queue, make_worker
from slice_helpers import ModelScript, make_discovery, make_gateway, make_site
from water_source_research.agents.graph import build_graph
from water_source_research.api.main import create_app
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.record_store import RecordRepository
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

class World:
    """Queue, API client, review-enabled graph sharing one checkpointer (a 'database')."""

    def __init__(self, engine, clock=None) -> None:
        self.engine = engine
        self.queue = make_queue(engine, clock)
        self.checkpoints = InMemorySaver()
        self.evidence = EvidenceRepository(engine)
        self.reviews = ReviewRepository(engine)
        self.records = RecordRepository(engine)
        self.client = TestClient(create_app(queue=self.queue, evidence=self.evidence,
                                            reviews=self.reviews, records=self.records))
        self.run_id = self.client.post("/research/city", json={
            "city_name": "Toronto", "country": "Canada", "field_keys": FIELDS}).json()["run_id"]

    def graph(self):
        gateway, _ = make_gateway(ModelScript(answers=ANSWERS), structured_max_attempts=1)
        return build_graph(gateway=gateway, discovery=make_discovery(),
                           acquisition=make_acquisition(make_site()), max_discovery_rounds=1,
                           evidence_store=self.evidence, review_store=self.reviews,
                           record_store=self.records, checkpointer=self.checkpoints)

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
