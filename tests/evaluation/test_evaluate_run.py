"""End-to-end evaluation of a persisted (mocked) research run: stage separation, evidence
checks, diagnoses from saved provenance, metrics, persistence and the evaluation API.
No network and no model calls; the research data is only read."""

import pytest
from eval_world import W, EvalWorld, import_world_reference
from fastapi.testclient import TestClient
from procedure_helpers import make_engine
from review_helpers import FIELDS, decision, withdrawal_quote_evidence
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session
from water_source_research.api.main import create_app
from water_source_research.db.models import Base
from water_source_research.evaluation.diagnosis import volume_rates
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.store import EvaluationStore

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path, "evaluate.db")

def research_counts(engine) -> dict[str, int]:
    """Row counts of every non-evaluation table (the evaluator must not change them)."""
    out = {}
    with Session(engine) as s:
        for name, table in Base.metadata.tables.items():
            if not name.startswith("eval_") and name in inspect(engine).get_table_names():
                out[name] = s.scalar(select(func.count()).select_from(table))
    return out

def paused_run(engine, tmp_path):
    w = EvalWorld(engine)
    w.work()
    dataset = import_world_reference(tmp_path, engine)
    return w, dataset

def by(results, stage, field, record=None):
    return next(r for r in results if r["stage"] == stage and r["field_key"] == field
                and (record is None or r["record_key"] == record))

def test_pre_review_run_is_evaluated_at_raw_and_auto(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    before = research_counts(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    assert research_counts(engine) == before  # nothing written to research tables
    store = EvaluationStore(engine)
    run = store.run(eval_id)
    assert run["stages"] == ["RAW", "AUTO"] and run["status"] == "COMPLETED"
    results = store.field_results(eval_id)
    # Rules accepted the utility and source; both match the reference.
    for field in ("utility_name", "water_source"):
        auto = by(results, "AUTO", field)
        assert auto["status"] == "MATCH" and auto["predicted"]["outcome"] == "RESOLVED"
    # The model's withdrawal contradicts its own quote: wrong raw value, escalated (not wrong).
    raw = by(results, "RAW", W)
    assert raw["status"] == "MISMATCH" and raw["predicted"]["candidates"][0]["value"] == 450000
    auto = by(results, "AUTO", W)
    assert auto["predicted"]["outcome"] == "ESCALATED" and auto["status"] == "MISSING_PREDICTION"

    metrics = {(m["scope_key"], m["metric"]): m for m in store.metrics(eval_id)}
    precision = metrics[("AUTO", "precision")]
    coverage = metrics[("AUTO", "coverage")]
    assert precision["numerator"] == precision["denominator"]  # all accepted fields correct
    assert coverage["denominator"] > coverage["numerator"]  # ...at partial coverage
    assert precision["detail"]["coverage"] == f"{coverage['numerator']:g}/" \
                                              f"{coverage['denominator']:g}".replace(".0", "")

def test_extraction_failure_is_diagnosed_from_the_bundle(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    store = EvaluationStore(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    [d] = [d for d in store.diagnoses(eval_id) if d["field_key"] == W and d["stage"] == "RAW"]
    assert d["root_cause"] == "EXTRACTION_FAILURE" and d["confidence"] == "CONFIRMED"
    evidence = next(p for p in d["probes"] if p["probe"] == "evidence")["observed"]
    assert evidence["chunks_with_value_count"] >= 1  # "980,000 cubic metres" was acquired
    retrieval = next(p for p in d["probes"] if p["probe"] == "retrieval")["observed"]
    assert retrieval["value_chunks_in_bundles"]  # ...and shown to the model for this field
    assert "GROUNDING" in d["symptoms"]  # its quote doesn't support 450,000

def test_discovery_and_acquisition_failures_use_the_references_sources(engine, tmp_path):
    w = EvalWorld(engine, fields=FIELDS + ["water_quality_notes", "infrastructure_notes"])
    w.work()
    store = EvaluationStore(engine)
    dataset = import_world_reference(tmp_path, engine, water_quality_notes="pristine upland",
                                     infrastructure_notes="aqueduct")
    eval_id = run_evaluation(engine, w.run_id, dataset)
    diagnoses = {d["field_key"]: d for d in store.diagnoses(eval_id) if d["stage"] == "RAW"}
    # The quality reference cites toronto.ca/quality-2024, never a discovery candidate.
    quality = diagnoses["water_quality_notes"]
    assert (quality["root_cause"], quality["confidence"]) == ("DISCOVERY_FAILURE", "PROBABLE")
    [trail] = quality["probes"][0]["observed"]["reference_sources"]
    assert trail["in_discovery_candidates"] is False
    # The infrastructure reference cites the down host: discovered, fetched, failed.
    infra = diagnoses["infrastructure_notes"]
    assert (infra["root_cause"], infra["confidence"]) == ("ACQUISITION_FAILURE", "CONFIRMED")
    assert infra["probes"][0]["observed"]["reference_sources"][0]["document_status"] == "FAILED"
    run = store.run(eval_id)
    assert any(g["code"] == "RAW_SEARCH_RESULTS" for g in run["gaps"])

def test_fields_outside_the_researched_subset_are_not_scored(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    store = EvaluationStore(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    city = by(store.field_results(eval_id), "RAW", "city_population_millions")
    assert city["status"] == "NOT_RESEARCHED"
    assert not [d for d in store.diagnoses(eval_id)
                if d["field_key"] == "city_population_millions"]
    cells = {(m["scope_key"], m["metric"]): m for m in store.metrics(eval_id)}
    auto = cells[("AUTO", "coverage")]
    assert auto["denominator"] == len(FIELDS)
    assert auto["detail"]["excluded"]["NOT_RESEARCHED"] > 0

def test_reviewed_run_keeps_raw_predictions_and_scores_final_output(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    item = w.item(W)
    w.decide(decision(item, "CORRECT", value=980000, reason="the quote says 980,000",
                      claim_uids=[item["payload"]["candidates"][0]["claim_uid"]],
                      evidence=withdrawal_quote_evidence(item)),
             decision(w.item("water_type"), "MARK_UNKNOWN", reason="not stated"))
    w.resume()
    w.work()
    store = EvaluationStore(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    results = store.field_results(eval_id)
    assert store.run(eval_id)["stages"] == ["RAW", "AUTO", "FINAL"]
    # The reviewer's 980,000 is not an extraction: RAW still shows the model's 450,000.
    raw = by(results, "RAW", W)
    assert [c["value"] for c in raw["predicted"]["candidates"]] == [450000]
    final = by(results, "FINAL", W)
    assert final["status"] == "MATCH" and final["predicted"]["origin"] == "REVIEWER"
    contribution = by(results, "FINAL", "source_withdrawal_contribution_m3_day")
    assert contribution["predicted"]["origin"] == "DERIVED" and contribution["status"] == "MATCH"
    metrics = {(m["scope_key"], m["metric"]): m for m in store.metrics(eval_id)}
    assert metrics[("FINAL", "reviewer_decided_accuracy")]["numerator"] >= 1
    checks = store.evidence_checks(eval_id)
    corrected = [c for c in checks if c["check"] == "CORRECTION_DOCUMENTED"]
    assert corrected and corrected[0]["result"] == "PASS"
    traceable = [c for c in checks if c["check"] == "TRACEABLE"]
    assert traceable and all(c["result"] in ("PASS", "WARN") for c in traceable)

def test_reevaluation_creates_a_new_run_and_keeps_the_old_one(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    first = run_evaluation(engine, w.run_id, dataset)
    second = run_evaluation(engine, w.run_id, dataset)
    store = EvaluationStore(engine)
    assert first != second
    assert {r["id"] for r in store.runs(w.run_id)} == {first, second}
    assert store.run(first)["snapshot_sha256"] == store.run(second)["snapshot_sha256"]
    assert len(store.field_results(first)) == len(store.field_results(second))

def test_evidence_checks_verify_quotes_against_stored_text(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    store = EvaluationStore(engine)
    checks = store.evidence_checks(run_evaluation(engine, w.run_id, dataset))
    quotes = [c for c in checks if c["check"] == "QUOTE_VERIFIED"]
    assert quotes and all(c["result"] == "PASS" for c in quotes)
    support = {c["claim_uid"]: c["result"] for c in checks if c["check"] == "SEMANTIC_SUPPORT"}
    assert "NEEDS_REVIEW" in support.values()  # the 450,000 claim: quote verified, unsupported

def test_api_evaluates_investigates_and_adjudicates(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    client = TestClient(create_app(queue=w.queue, evidence=w.evidence, reviews=w.reviews,
                                   records=w.records))
    r = client.post("/evaluation/runs", json={"research_run_id": w.run_id,
                                              "dataset_id": dataset})
    assert r.status_code == 201
    eval_id = r.json()["id"]
    assert client.get(f"/evaluation/runs?research_run_id={w.run_id}").json()[0]["id"] == eval_id
    field = client.get(f"/evaluation/runs/{eval_id}/fields/{W}").json()
    assert {x["stage"] for x in field["results"]} == {"RAW", "AUTO"}
    assert field["diagnoses"] and field["evidence"]
    raw_id = next(x["id"] for x in field["results"] if x["stage"] == "RAW")
    bad = client.post(f"/evaluation/runs/{eval_id}/adjudications", json={
        "target": "FIELD_RESULT", "target_id": raw_id, "decision": "OVERRIDE_STATUS",
        "reviewer": "analyst"})
    assert bad.status_code == 422
    ok = client.post(f"/evaluation/runs/{eval_id}/adjudications", json={
        "target": "FIELD_RESULT", "target_id": raw_id, "decision": "CONFIRM_MISMATCH",
        "reviewer": "analyst", "note": "450,000 isn't in the quote"})
    assert ok.status_code == 201
    assert client.get(f"/evaluation/runs/{eval_id}/fields/{W}").json()["adjudications"]
    assert client.post(f"/evaluation/runs/{eval_id}/adjudications", json={
        "target": "DIAGNOSIS", "target_id": 999999, "decision": "CONFIRM_DIAGNOSIS",
        "reviewer": "a"}).status_code == 404
    assert client.post("/evaluation/runs", json={
        "research_run_id": "nope"}).status_code == 404
    assert client.post("/evaluation/references", json={
        "path": "../outside.xlsx"}).status_code == 422

@pytest.mark.parametrize("text, value", [
    (("In 2023 the plants produced an average of 980,000 cubic metres of drinking water "
      "per day."), 980000),
    ("Production averaged 980 ML/d in 2023.", 980000),
    ("About 357.7 million cubic metres a year were treated in 2023.", 980000),
])
def test_volume_rates_read_numbers_with_units_split_by_filler(text, value):
    rates = volume_rates(text)
    assert any(abs((r if p == "day" else r / 365) - value) / value < 0.01 for r, p, _ in rates)
    assert not volume_rates("Call 416-392-2489 or see page 980000.")

def test_wrong_automatic_acceptance_is_a_resolution_failure(engine, tmp_path):
    w = EvalWorld(engine)
    w.work()
    dataset = import_world_reference(tmp_path, engine, utility_name="Ontario Clean Water Agency")
    store = EvaluationStore(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    auto = by(store.field_results(eval_id), "AUTO", "utility_name")
    assert auto["predicted"]["outcome"] == "RESOLVED" and auto["status"] == "MISMATCH"
    causes = {(d["stage"], d["root_cause"]) for d in store.diagnoses(eval_id)
              if d["field_key"] == "utility_name"}
    assert ("AUTO", "RESOLUTION_FAILURE") in causes
    wrong = {(m["scope_key"], m["metric"]): m for m in store.metrics(eval_id)}
    assert wrong[("AUTO", "wrong_acceptance_rate")]["numerator"] == 1

def test_a_reviewer_value_that_differs_is_attributed_to_review(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    item = w.item(W)
    w.decide(decision(item, "CORRECT", value=500000, reason="analyst's own estimate",
                      claim_uids=[item["payload"]["candidates"][0]["claim_uid"]],
                      evidence=withdrawal_quote_evidence(item)),
             decision(w.item("water_type"), "MARK_UNKNOWN", reason="not stated"))
    w.resume()
    w.work()
    store = EvaluationStore(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    final = [d for d in store.diagnoses(eval_id) if d["field_key"] == W and d["stage"] == "FINAL"]
    assert [d["root_cause"] for d in final] == ["REVIEW_INTRODUCED_DIFFERENCE"]
    # ...and the extraction diagnosis is still about the model's 450,000, not the review.
    raw = [d for d in store.diagnoses(eval_id) if d["field_key"] == W and d["stage"] == "RAW"]
    assert raw[0]["root_cause"] == "EXTRACTION_FAILURE"
