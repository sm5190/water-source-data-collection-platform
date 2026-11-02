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
from water_source_research.schemas.standardized import COLUMNS

# The mocked worlds model production with an approved city identity (Milestone 3):
# without one, a utility's own statement naming only the city isn't authoritative.
pytestmark = pytest.mark.usefixtures("identity_approved")


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
    assert run["stages"] == ["RAW", "PROPOSED", "AUTO"] and run["status"] == "COMPLETED"
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

def test_proposed_stage_scores_the_value_put_forward_before_review(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    store = EvaluationStore(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    results = store.field_results(eval_id)
    # Accepted by the rules: the proposed value is the accepted one.
    utility = by(results, "PROPOSED", "utility_name")
    assert utility["status"] == "MATCH" and utility["predicted"]["source"] == "AUTO_ACCEPTED"
    # Escalated: AUTO has no answer, but the system still proposed (and got wrong) 450,000.
    proposed = by(results, "PROPOSED", W)
    p = proposed["predicted"]
    assert (proposed["status"], p["source"], p["value"]) == ("MISMATCH", "PROPOSED", 450000)
    assert p["candidate_count"] == 1 and p["selection_reason"] == "the only candidate"
    assert p["selected_claim_uids"] == [p["candidates"][0]["claim_uid"]]
    assert p["citations"][0]["quote"]  # the quote is kept for the comparison view
    # Nothing extracted for water_type: missing, not "N/A".
    assert by(results, "PROPOSED", "water_type")["status"] in ("MISSING_PREDICTION",
                                                               "MISSING_REFERENCE")

    metrics = {(m["scope_key"], m["metric"]): m for m in store.metrics(eval_id)}
    cov, strict = metrics[("PROPOSED", "answer_coverage")], metrics[("PROPOSED", "accuracy_strict")]
    auto_cov = metrics[("AUTO", "answer_coverage")]
    # Proposed answers more cells than the rules accepted, and is scored on all of them.
    assert cov["numerator"] > auto_cov["numerator"]
    assert strict["denominator"] == cov["numerator"]
    assert strict["numerator"] < strict["denominator"]  # the 450,000 is wrong
    lenient = metrics[("PROPOSED", "accuracy_lenient")]
    assert lenient["detail"]["partial_credit"] == 0.5
    assert lenient["numerator"] >= strict["numerator"]
    assert strict["detail"]["provisional"] == 0  # no semantic judgment involved
    recall = metrics[("RAW", "any_candidate_recall")]
    assert "not the accuracy of the proposed value" in recall["detail"]["note"]

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
    # The run's searches were recorded: the source wasn't returned by any of them. That is
    # all it shows (not that the source was unavailable), so it stays PROBABLE.
    assert trail["returned_by_recorded_search"] is False
    assert "URL_NOT_RETURNED_IN_RECORDED_SEARCHES" in quality["symptoms"]
    assert "not that the source was unavailable" in quality["explanation"]
    # The infrastructure reference cites the down host: discovered, fetched, failed.
    infra = diagnoses["infrastructure_notes"]
    assert (infra["root_cause"], infra["confidence"]) == ("ACQUISITION_FAILURE", "CONFIRMED")
    assert infra["probes"][0]["observed"]["reference_sources"][0]["document_status"] == "FAILED"
    run = store.run(eval_id)
    assert not any(g["code"] == "RAW_SEARCH_RESULTS" for g in run["gaps"])
    hits = w.evidence.discovery_results(w.run_id)
    assert hits and {h["provider"] for h in hits} and all(h["url"] for h in hits)
    assert any(h["retained"] for h in hits) and all(h["position"] >= 1 for h in hits)

def test_ranked_out_and_unrecorded_searches_are_told_apart(engine, tmp_path):
    from water_source_research.evaluation.diagnosis import Diagnoser
    from water_source_research.evaluation.policy import load_policy
    from water_source_research.services.field_state import load_field_definitions
    url = "https://www.toronto.ca/quality-2024/"
    base = {"chunks": [], "discovery_candidates": [], "documents": [], "context_bundles": []}
    fields, policy = load_field_definitions(), load_policy()
    unrecorded = Diagnoser({**base, "discovery_results": []}, fields, policy)
    d = unrecorded._discovery("", "water_quality_notes",
                              unrecorded.source_trail([url]), [], [])
    assert d["confidence"] == "PROBABLE" and "weren't recorded" in d["explanation"]
    hit = {"url": url, "provider": "searxng", "query": "toronto water quality", "position": 14,
           "retained": False}
    ranked = Diagnoser({**base, "discovery_results": [hit]}, fields, policy)
    d = ranked._discovery("", "water_quality_notes", ranked.source_trail([url]), [], [])
    assert d["confidence"] == "PROBABLE" and "RANKED_OUT" in d["symptoms"]
    assert "position 14" in d["explanation"]

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
    assert store.run(eval_id)["stages"] == ["RAW", "PROPOSED", "AUTO", "FINAL"]
    # The reviewer's 980,000 is not an extraction: RAW still shows the model's 450,000.
    raw = by(results, "RAW", W)
    assert [c["value"] for c in raw["predicted"]["candidates"]] == [450000]
    final = by(results, "FINAL", W)
    assert final["status"] == "MATCH" and final["predicted"]["origin"] == "REVIEWER"
    # ...and so does PROPOSED: the reviewer's correction never counts as automated output.
    proposed = by(results, "PROPOSED", W)
    assert proposed["predicted"]["value"] == 450000 and proposed["status"] == "MISMATCH"
    metrics = {(m["scope_key"], m["metric"]): m for m in store.metrics(eval_id)}
    assert metrics[("FINAL", "accuracy_strict")]["value"] != \
        metrics[("PROPOSED", "accuracy_strict")]["value"]
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
    assert {x["stage"] for x in field["results"]} == {"RAW", "PROPOSED", "AUTO"}
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

def test_comparison_view_lists_every_field_side_by_side(engine, tmp_path):
    w, dataset = paused_run(engine, tmp_path)
    client = TestClient(create_app(queue=w.queue, evidence=w.evidence, reviews=w.reviews,
                                   records=w.records))
    eval_id = client.post("/evaluation/runs", json={"research_run_id": w.run_id,
                                                    "dataset_id": dataset}).json()["id"]
    cities = client.get("/evaluation/cities").json()
    assert cities[0]["latest_eval_run_id"] == eval_id
    view = client.get(f"/evaluation/runs/{eval_id}/comparison", params={"stage": "PROPOSED"})
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["stages"] == ["PROPOSED", "AUTO"] and body["stage_label"] == "Proposed"
    assert not body["judge_validated"]
    # all 32 standardized fields are listed, even those this stage doesn't produce
    assert {r["field_key"] for r in body["rows"]} == {k for k, _ in COLUMNS}
    rows = {r["field_key"]: r for r in body["rows"]}
    withdrawal = rows[W]
    assert withdrawal["automated"] == "450000" and withdrawal["group"] == "MISMATCH"
    assert withdrawal["proposed_not_accepted"]
    assert withdrawal["details"]["candidate_count"] == 1
    assert withdrawal["details"]["candidates"][0]["selected"]
    assert withdrawal["details"]["citations"][0]["quote"]
    assert rows["references"]["status"] == "NOT_EVALUATED"
    assert rows["percentage_total_population"]["status"] == "NOT_EVALUATED"
    assert rows["city_population_millions"]["status"] == "NOT_RESEARCHED"
    proposed, auto = body["summaries"]["PROPOSED"], body["summaries"]["AUTO"]
    assert proposed["answered"] > auto["answered"]  # both stages' scores are always served
    auto_view = client.get(f"/evaluation/runs/{eval_id}/comparison",
                           params={"stage": "AUTO"}).json()
    auto_rows = {r["field_key"]: r for r in auto_view["rows"]}
    assert auto_rows[W]["automated"] == "— (sent to review)"
    assert auto_rows[W]["group"] == "MISSING_AUTOMATED"
    assert client.get(f"/evaluation/runs/{eval_id}/comparison",
                      params={"stage": "FINAL"}).status_code == 409

    perf = client.get("/evaluation/performance").json()
    assert perf["stages"] == ["PROPOSED", "AUTO"]
    [city] = perf["cities"]
    assert city["scores"]["PROPOSED"]["comparable"] == proposed["comparable"]
    # one city: no cross-city average is claimed
    assert perf["dataset"]["PROPOSED"]["mean_city_accuracy_strict"] is None
    by_field = {f["field_key"]: f for f in perf["fields"]}
    assert by_field[W]["PROPOSED"]["n_cities"] == 1
    assert perf["judge"]["validated"] is False
    assert [h["eval_run_id"] for h in perf["history"]] == [eval_id]

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

def test_a_final_stage_reviewed_from_the_reference_is_not_independent(engine, tmp_path):
    from water_source_research.evaluation.views import performance
    w, dataset = paused_run(engine, tmp_path)
    item = w.item(W)
    w.decide(decision(item, "CORRECT", value=980000,
                      claim_uids=[item["payload"]["candidates"][0]["claim_uid"]],
                      evidence={"url": "https://example.org/workbook-source"}),
             decision(w.item("water_type"), "MARK_UNKNOWN"),
             reviewer="Sarina workbook (applied by Claude)")
    w.resume()
    w.work()
    store = EvaluationStore(engine)
    eval_id = run_evaluation(engine, w.run_id, dataset)
    assert store.run(eval_id)["config"]["final_basis"] == "REFERENCE_DERIVED"
    final = [m for m in store.metrics(eval_id) if m["scope"] == "stage"
             and m["scope_key"] == "FINAL"]
    assert final and all(m["detail"].get("reference_derived") for m in final)
    perf = performance(store)
    assert perf["cities"][0]["final_reference_derived"]
    assert "FINAL" not in perf["stages"]  # never pooled into the automated scores
