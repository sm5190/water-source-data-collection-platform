"""Dashboard backend: city catalog, step progress, human review log, field provenance, API."""

import asyncio
import pytest
from fastapi.testclient import TestClient
from procedure_helpers import make_engine
from queue_helpers import make_graph, make_queue, make_worker
from water_source_research.api.main import create_app
from water_source_research.reporting.run_views import field_provenance, human_review_log
from water_source_research.research_queue.steps import NODE_ORDER
from water_source_research.services.city_catalog import load_city_catalog
from water_source_research.services.field_state import load_field_definitions

FIELDS = load_field_definitions()
WITHDRAWAL = "total_city_water_withdrawals_m3_day"

def evidence(url, passage, page=None):
    return {"url": url, "title": "Annual Report 2023", "page_number": page, "passage": passage,
            "retrieved_at": None}

def claim(field, value, conf, *evs):
    return {"field_key": field, "value": value, "confidence": conf, "value_status": "KNOWN",
            "value_origin": "REPORTED", "evidence": list(evs)}

@pytest.fixture
def result():
    """A finished run: one corrected+accepted field, one conflict, one candidate, one missing."""
    utility = claim("utility_name", "Toronto Water", 0.9,
                    evidence("https://toronto.ca/water", "Toronto Water operates…", 3))
    w1 = claim(WITHDRAWAL, 1_050_000, 0.8,
               evidence("https://toronto.ca/report.pdf", "1,050 ML/day in 2023", 41))
    w2 = claim(WITHDRAWAL, 980_000, 0.6,
               evidence("https://news.example/x", "about 980 million litres a day"))
    source = claim("water_source", "Lake Ontario", 0.85,
                   evidence("https://toronto.ca/water", "drawn from Lake Ontario"))
    fields = {k: {"field_key": k, "status": "UNRESOLVED", "selected_claim": None,
                  "candidate_claims": []} for k in FIELDS}
    fields["utility_name"].update(status="RESOLVED", selected_claim=utility,
                                  candidate_claims=[utility])
    fields[WITHDRAWAL].update(status="CONFLICT", candidate_claims=[w1, w2])
    fields["water_source"].update(status="CANDIDATE", candidate_claims=[source])
    events = [
        {"seq": 1, "kind": "NODE", "node": "extract", "field_key": "utility_name",
         "attrs": {"bundle_status": "READY", "claims": 1}, "created_at": "2026-10-09T10:00:00"},
        {"seq": 2, "kind": "FIELD_TRANSITION", "node": "extract", "field_key": "utility_name",
         "attrs": {"from_status": "UNRESOLVED", "to_status": "CANDIDATE"},
         "created_at": "2026-10-09T10:00:01"},
        {"seq": 3, "kind": "HITL", "node": "human_review", "field_key": "utility_name",
         "result": "CORRECTED", "attrs": {"correction_category": "name_normalization"},
         "created_at": "2026-10-09T10:05:00"},
        {"seq": 4, "kind": "EVIDENCE_ACCEPTED", "node": "human_review",
         "field_key": "utility_name",
         "attrs": {"url": "https://toronto.ca/water", "source_type": "UTILITY"},
         "created_at": "2026-10-09T10:05:00"},
        {"seq": 5, "kind": "FIELD_TRANSITION", "node": "human_review", "field_key": "utility_name",
         "attrs": {"from_status": "CANDIDATE", "to_status": "RESOLVED"},
         "created_at": "2026-10-09T10:05:00"},
    ]
    return {"research_state": {"city_name": "Toronto", "fields": fields}, "events": events,
            "fields_needing_evidence": ["reservoir_names"], "discovery_rounds": 3,
            "needs_human_review": True, "context_bundle_ids": {"utility_name": "bundle-1"},
            "procedure_selections": [{"field_type": "utility", "procedure_id": "p-1",
                                      "version": 2, "adapted": False, "applied": True}]}

# --- views ----------------------------------------------------------------------------

def test_human_review_log_lists_reason_and_citations_per_field(result):
    items = {(i["field_key"], i["reason_code"]): i for i in human_review_log(result, FIELDS)}
    missing = items[("reservoir_names", "NO_SUFFICIENT_EVIDENCE")]
    assert missing["reason"] == "No sufficient evidence after 3 discovery round(s)"
    assert missing["citations"] == []
    conflict = items[(WITHDRAWAL, "CONFLICT")]
    assert conflict["reason"] == "2 conflicting candidate claims"
    assert [(c["claim_value"], c["url"], c["page_number"]) for c in conflict["citations"]] == [
        (1_050_000, "https://toronto.ca/report.pdf", 41), (980_000, "https://news.example/x", None)]
    awaiting = items[("water_source", "AWAITING_VALIDATION")]
    assert awaiting["citations"][0]["passage"] == "drawn from Lake Ontario"
    decision = items[("utility_name", "REVIEWER_DECISION")]
    assert (decision["decision"], decision["correction_category"]) == (
        "CORRECTED", "name_normalization")
    assert decision["citations"][0]["url"] == "https://toronto.ca/water"
    # Derived fields are computed, never reviewed.
    assert not any(k == "percentage_total_population" for k, _ in items)

def test_field_provenance_tracks_value_citations_history_and_strategy(result):
    rows = {r["field_key"]: r for r in field_provenance(result, FIELDS)}
    assert len(rows) == len(FIELDS)
    u = rows["utility_name"]
    assert (u["status"], u["value"], u["confidence"]) == ("RESOLVED", "Toronto Water", 0.9)
    assert u["citations"][0]["page_number"] == 3
    assert [t["to"] for t in u["transitions"]] == ["CANDIDATE", "RESOLVED"]
    assert u["extraction_attempts"] == [{"at": "2026-10-09T10:00:00",
                                         "bundle_status": "READY", "claims": 1}]
    assert u["reviewer_decisions"][0]["decision"] == "CORRECTED"
    assert u["accepted_evidence"] == [{"url": "https://toronto.ca/water",
                                       "source_type": "UTILITY"}]
    assert u["strategy"] == "procedure p-1 v2" and u["context_bundle_id"] == "bundle-1"
    w = rows[WITHDRAWAL]
    assert w["value"] is None and len(w["candidates"]) == 2 and w["strategy"] == "default"
    assert rows["reservoir_names"]["needs_evidence"]
    pct = rows["percentage_total_population"]
    assert pct["derived"] and "population_served_millions" in pct["depends_on"]

def test_city_catalog_loads_project_cities():
    cities = load_city_catalog()
    labels = {c.label for c in cities}
    assert len(cities) >= 50
    assert {"Toronto, Canada", "Madrid, Spain", "Nairobi, Kenya"} <= labels
    assert len(labels) == len(cities)  # no duplicates

# --- API with real worker runs --------------------------------------------------------

@pytest.fixture
def setup(tmp_path):
    engine = make_engine(tmp_path, "dash.db")
    q = make_queue(engine)
    yield q, TestClient(create_app(queue=q))
    engine.dispose()

def test_steps_are_recorded_in_order_with_outputs(setup):
    q, client = setup
    run_id = client.post("/research/city", json={"city_name": "Toronto",
                                                 "country": "Canada"}).json()["run_id"]
    before = client.get(f"/research/runs/{run_id}/steps").json()
    assert before["steps"] == [] and before["expected_nodes"] == list(NODE_ORDER)
    assert client.get(f"/research/runs/{run_id}/review").json() == {"available": False,
                                                                    "items": []}
    asyncio.run(make_worker(q, make_graph()).drain())
    data = client.get(f"/research/runs/{run_id}/steps").json()
    nodes = [s["node"] for s in data["steps"]]
    assert nodes[0] == "plan" and nodes[-1] == "finalize"
    assert {"discover", "acquire", "extract", "validate"} <= set(nodes)
    assert [s["seq"] for s in data["steps"]] == list(range(1, len(nodes) + 1))
    extract = next(s for s in data["steps"] if s["node"] == "extract")
    assert "utility_name" in extract["summary"]["fields_needing_evidence"]
    assert any(e["kind"] == "NODE" for e in extract["summary"]["events"])
    final = data["steps"][-1]["summary"]
    assert final["finalized"] is True and final["field_statuses"]

def test_review_and_fields_endpoints_after_run(setup):
    q, client = setup
    run_id = client.post("/research/city", json={"city_name": "Madrid",
                                                 "country": "Spain"}).json()["run_id"]
    asyncio.run(make_worker(q, make_graph()).drain())
    review = client.get(f"/research/runs/{run_id}/review").json()
    assert review["available"]
    reasons = {i["reason_code"] for i in review["items"]}
    assert reasons == {"NO_SUFFICIENT_EVIDENCE"}  # stub discovery finds nothing yet
    fields = client.get(f"/research/runs/{run_id}/fields").json()
    assert fields["available"] and len(fields["fields"]) == len(FIELDS)

def test_cities_and_city_filtered_runs(setup):
    _, client = setup
    cities = client.get("/research/cities").json()
    assert {"label", "city", "country", "jurisdiction"} <= set(cities[0])
    client.post("/research/city", json={"city_name": "Toronto", "country": "Canada"})
    client.post("/research/city", json={"city_name": "Madrid", "country": "Spain"})
    toronto = client.get("/research/runs", params={"city": "toronto", "country": "canada"})
    assert [r["city_name"] for r in toronto.json()] == ["Toronto"]
    assert client.get("/research/runs/missing/steps").status_code == 404
