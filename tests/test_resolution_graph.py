"""validate -> resolve inside the graph, and persistence of claims and resolution decisions."""

import asyncio
import pytest
from acquisition_helpers import make_acquisition
from slice_helpers import (
    TARGETS, ModelScript, initial_state, make_discovery, make_gateway, make_site,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from water_source_research.agents.graph import build_graph
from water_source_research.db.evidence_models import FieldClaimRecord
from water_source_research.db.session import init_db
from water_source_research.services.evidence_store import EvidenceRepository

# The mocked worlds model production with an approved city identity (Milestone 3):
# without one, a utility's own statement naming only the city isn't authoritative.
pytestmark = pytest.mark.usefixtures("identity_approved")

@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resolution.db'}")
    init_db(engine)
    yield engine
    engine.dispose()

def run(graph, state):
    return asyncio.run(graph.ainvoke(state))

def slice_graph(script=None, **kw):
    gateway, server = make_gateway(script or ModelScript(), structured_max_attempts=1)
    graph = build_graph(gateway=gateway, discovery=make_discovery(),
                        acquisition=make_acquisition(make_site()), max_discovery_rounds=1, **kw)
    return graph, None, server

# Withdrawal answered with a number that isn't in the quote, utility from a blog.
DISAGREEING = {
    "utility_name": [{"value": "Toronto Water", "quote": "Toronto Water is the City of Toronto "
                      "division that provides drinking water", "confidence": 0.9}],
    "water_source": [{"value": "Lake Ontario", "quote": "Toronto draws its drinking water from "
                      "Lake Ontario", "confidence": 0.9}],
    "total_city_water_withdrawals_m3_day": [
        {"value": 450000, "quote": "withdrew an average of 980,000 cubic metres of raw water "
                                   "per day", "confidence": 0.99}],
}

def test_graph_emits_validation_and_resolution_events():
    graph, _, _ = slice_graph()
    result = run(graph, initial_state())
    kinds = [e["kind"] for e in result["events"]]
    assert kinds.index("VALIDATION") < kinds.index("RESOLUTION")
    validations = [e for e in result["events"] if e["kind"] == "VALIDATION"]
    assert len(validations) == len(result["claims"]) == 4
    assert all(e["attrs"]["claim_uid"] for e in validations)
    accepted = [e for e in result["events"] if e["kind"] == "EVIDENCE_ACCEPTED"]
    assert {e["field_key"] for e in accepted} == set(TARGETS)
    transitions = [e for e in result["events"] if e["kind"] == "FIELD_TRANSITION"
                   and e["node"] == "resolve"]
    assert {(e["attrs"]["from_status"], e["attrs"]["to_status"]) for e in transitions} == {
        ("CANDIDATE", "RESOLVED")}
    utility = next(e for e in transitions if e["field_key"] == "utility_name")
    assert utility["attrs"]["anchor_value"] == "Toronto Water"
    assert [e["seq"] for e in result["resolution_log"]] == list(
        range(1, len(result["resolution_log"]) + 1))
    assert not result["needs_human_review"]

def test_unsupported_value_is_kept_but_routed_to_review():
    graph, _, _ = slice_graph(ModelScript(answers=DISAGREEING))
    result = run(graph, initial_state())
    fs = result["research_state"]["fields"]["total_city_water_withdrawals_m3_day"]
    [c] = fs["candidate_claims"]  # never silently dropped
    assert c["validation"]["outcome"] == "INVALID"
    assert fs["status"] == "HITL_REQUIRED" and fs["selected_claim"] is None
    assert result["needs_human_review"]
    assert result["fields_requiring_review"] == ["total_city_water_withdrawals_m3_day"]
    assert "total_city_water_withdrawals_m3_day" in result["review_reason"]
    rejected = [e for e in result["events"] if e["kind"] == "VALIDATION"
                and e["result"] == "REJECTED"]
    assert [e["field_key"] for e in rejected] == ["total_city_water_withdrawals_m3_day"]
    assert "VALUE_IN_QUOTE" in rejected[0]["attrs"]["reason_code"]

def test_claims_upsert_by_uid_and_resolutions_append(engine):
    repo = EvidenceRepository(engine)
    graph, _, _ = slice_graph(evidence_store=repo)
    result = run(graph, initial_state())
    before = repo.counts("run-toronto")
    assert before["claims"] == 4 and before["citations"] == 4
    assert before["resolutions"] == len(result["resolution_log"]) == 4

    # A retried finalize writes the same rows: nothing duplicated, nothing deleted.
    repo.save_results("run-toronto", result["research_state"], [],
                      result["unresolved_fields"], result["resolution_log"])
    assert repo.counts("run-toronto") == before
    uids = {c["claim_uid"] for c in repo.claims("run-toronto")}
    assert uids == {c["claim_uid"] for f in result["research_state"]["fields"].values()
                    for c in f["candidate_claims"]}

    # A later pass that changes a decision appends; the claim row is updated in place.
    research = result["research_state"]
    research["fields"]["utility_name"]["status"] = "HITL_REQUIRED"
    research["fields"]["utility_name"]["selected_claim"] = None
    research["fields"]["utility_name"]["resolution"]["entities"][0]["selected_claim_uids"] = []
    log = result["resolution_log"] + [{**result["resolution_log"][0], "seq": 5,
                                       "to_status": "HITL_REQUIRED", "rule": "TEST"}]
    repo.save_results("run-toronto", research, [], result["unresolved_fields"], log)
    after = repo.counts("run-toronto")
    assert after["claims"] == 4 and after["resolutions"] == 5
    utility = next(c for c in repo.claims("run-toronto") if c["field_key"] == "utility_name")
    assert not utility["is_selected"]
    assert [e["rule"] for e in repo.resolutions("run-toronto")][-1] == "TEST"

def test_legacy_claim_rows_are_left_untouched(engine):
    """Rows written before claim_uid existed (NULL) are never rewritten or deleted."""
    with Session(engine) as session, session.begin():
        session.add(FieldClaimRecord(run_id="run-toronto", field_key="utility_name",
                                     value="Old", value_status="KNOWN", value_origin="REPORTED",
                                     confidence=0.5, is_selected=True))
    graph, _, _ = slice_graph(evidence_store=EvidenceRepository(engine))
    run(graph, initial_state())
    legacy = [c for c in EvidenceRepository(engine).claims("run-toronto") if c["value"] == "Old"]
    assert len(legacy) == 1 and legacy[0]["claim_uid"] is None and legacy[0]["is_selected"]
