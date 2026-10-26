"""The semantic judge (evaluation/judge.py): strict structured output, deterministic caps, the
persistent cache, what it may and may not decide, and how its judgments flow into an
evaluation (provisional until a person verifies them). Mocked VT HTTP only; no real model."""

import json
from pathlib import Path
import pytest
import yaml
from eval_world import W, EvalWorld, import_world_reference
from llm_helpers import Server, chat_json, make_llm, vt_settings
from procedure_helpers import make_engine
from water_source_research.db.models import Base
from water_source_research.evaluation.evaluate import apply_judge, run_evaluation
from water_source_research.evaluation.judge import (
    JUDGE_PROMPT_VERSION, Judgment, JudgmentStore, SemanticJudge, eligible, load_descriptions,
    map_verdict,
)
from water_source_research.evaluation.report import effective
from water_source_research.evaluation.store import EvaluationStore
from water_source_research.schemas.standardized import COLUMNS

CASES = Path(__file__).parent / "judge_cases_toronto.yaml"

@pytest.fixture
def engine(tmp_path):
    e = make_engine(tmp_path, "judge.db")
    Base.metadata.create_all(e)
    return e

def answer(verdict="EQUIVALENT", measure="yes", scope="yes", period="not_applicable",
           differing=(), missing=(), overlap=("same thing",), why="Same organization."):
    return {"verdict": verdict, "overlapping_facts": list(overlap),
            "missing_facts": list(missing), "differing_facts": list(differing),
            "same_meaning_of_measure": measure, "same_geographic_scope": scope,
            "same_reporting_period": period, "justification": why}

def judge_with(engine, *responses, settings=None):
    server = Server(*responses)
    llm = make_llm(server, settings, task="eval_judge", prompt_version=JUDGE_PROMPT_VERSION)
    return SemanticJudge(llm, JudgmentStore(engine)), server

# --- caps -------------------------------------------------------------------------------------

@pytest.mark.parametrize("out, status", [
    (answer(), "MATCH"),
    (answer("PARTIAL_OVERLAP", missing=["intake"]), "PARTIAL_MATCH"),
    (answer("DIFFERENT"), "MISMATCH"),
    (answer("INSUFFICIENT_CONTEXT"), "NEEDS_REVIEW"),
    (answer(scope="no"), "MISMATCH"),  # e.g. a service area is not the city
    (answer(measure="no"), "MISMATCH"),  # e.g. treated vs raw water quality
    (answer(period="unclear"), "NEEDS_REVIEW"),
    (answer("PARTIAL_OVERLAP", period="unclear"), "NEEDS_REVIEW"),
    (answer(differing=["the year"]), "PARTIAL_MATCH"),  # "equivalent" yet facts differ
    (answer("DIFFERENT", scope="unclear"), "MISMATCH"),  # caps never improve a verdict
])
def test_verdicts_are_capped_deterministically(out, status):
    assert map_verdict(Judgment.model_validate(out)) == status

def test_the_output_schema_is_strict():
    with pytest.raises(ValueError):
        Judgment.model_validate({**answer(), "confidence": 0.9})
    with pytest.raises(ValueError):
        Judgment.model_validate({**answer(), "verdict": "PROBABLY_SAME"})
    with pytest.raises(ValueError):
        Judgment.model_validate({**answer(), "justification": "x" * 301})

# --- calls, cache and audit -------------------------------------------------------------------

def test_judgments_are_cached_and_recorded_with_their_inputs(engine):
    judge, server = judge_with(engine, chat_json(answer()))
    x = judge.make_input("utility_name", "City of Toronto Water Division", None,
                         "Toronto Water", None, ["Toronto Water is the City of Toronto division"])
    first = judge.judge(x)
    assert first["status"] == "MATCH" and not first["cached"]
    assert first["prompt_version"] == JUDGE_PROMPT_VERSION and first["model"] == "gpt-oss-120b"
    assert first["inputs"]["definition"].startswith("The utility that operates")
    assert first["output"]["justification"] == "Same organization."
    # same inputs: no second request, even from a new judge (the cache is in the database)
    again = SemanticJudge(judge.llm, JudgmentStore(engine)).judge(x)
    assert again["cached"] and again["judgment_id"] == first["judgment_id"]
    assert len(server.requests) == 1
    prompt = json.dumps(server.bodies()[0])
    assert "City of Toronto Water Division" in prompt and "DEFINITION" in prompt

def test_a_failing_model_is_not_cached_and_needs_a_person(engine):
    judge, _ = judge_with(engine, chat_json({"verdict": "SAME"}),
                               settings=vt_settings(structured_max_attempts=0))
    x = judge.make_input("seasonal_uses", "None Reported", None, "taste and odour", None, [])
    assert judge.judge(x) is None and judge.errors
    assert JudgmentStore(engine).get(x.cache_key(judge.provider, judge.model)) is None

def test_unavailable_judge_makes_no_calls(engine):
    judge = SemanticJudge(None, JudgmentStore(engine), unavailable="no VT key")
    x = judge.make_input("utility_name", "A", None, "B", None, [])
    assert judge.judge(x) is None and judge.unavailable == "no VT key"

# --- what the judge may decide ----------------------------------------------------------------

def result(field, status, stage="PROPOSED", tags=(), value="x", ref="y"):
    return {"stage": stage, "field_key": field, "status": status, "record_key": "",
            "comparison": {"status": status, "tags": list(tags)},
            "predicted": {"value": value, "selected_claim_uids": []},
            "reference": {"raw": ref, "normalized": ref}}

@pytest.mark.parametrize("r, ok", [
    (result("water_quality_notes", "NEEDS_REVIEW"), True),
    (result("infrastructure_notes", "PARTIAL_MATCH"), True),
    (result("utility_name", "PARTIAL_MATCH"), True),
    (result("water_source", "MISMATCH"), True),
    (result("utility_name", "MATCH"), False),  # already decided
    (result(W, "MISMATCH"), False),  # numbers are never judged
    (result("water_type", "MISMATCH"), False),  # categories neither
    (result("city_population_millions", "SCOPE_MISMATCH"), False),
    (result("total_city_water_withdrawals_year", "TEMPORAL_MISMATCH"), False),
    (result("water_transfer_system", "MISMATCH", tags=["VALUE_WHERE_NOT_APPLICABLE"]), False),
    (result("utility_name", "MISSING_PREDICTION"), False),
    (result("water_quality_notes", "NEEDS_REVIEW", tags=["SCOPE:GEOGRAPHIC_SCOPE"]), False),
])
def test_hard_rules_are_never_sent_to_the_judge(r, ok):
    assert eligible(r) is ok

def test_apply_judge_keeps_the_deterministic_status_and_marks_unavailable_cells(engine):
    judge, _ = judge_with(engine, chat_json(answer(
        "PARTIAL_OVERLAP", overlap=["intake"], missing=["tunnel"], why="Only the intake.")))
    rows = [result("infrastructure_notes", "NEEDS_REVIEW", value="lake intake",
                   ref="intake, tunnel"),
            result(W, "MISMATCH")]
    info = apply_judge(rows, judge, {"claims": []})
    judged, number = rows
    assert judged["status"] == "PARTIAL_MATCH" and judged["comparison"]["judgment_id"]
    assert judged["comparison"]["deterministic_status"] == "NEEDS_REVIEW"
    assert judged["comparison"]["judgment"]["missing_facts"] == ["tunnel"]
    assert number["status"] == "MISMATCH" and "judgment_id" not in number["comparison"]
    assert info["judged"] == 1 and info["prompt_version"] == JUDGE_PROMPT_VERSION

    off = SemanticJudge(None, JudgmentStore(engine), unavailable="no VT key")
    rows = [result("water_quality_notes", "PARTIAL_MATCH")]
    apply_judge(rows, off, {"claims": []})
    assert rows[0]["status"] == "NEEDS_REVIEW"
    assert "SEMANTIC_UNAVAILABLE" in rows[0]["comparison"]["tags"]

# --- end to end: provisional until verified ---------------------------------------------------

def test_judged_cells_are_provisional_until_a_person_verifies_them(tmp_path):
    engine = make_engine(tmp_path, "e2e.db")
    w = EvalWorld(engine)
    w.work()
    dataset = import_world_reference(tmp_path, engine,
                                     utility_name="City of Toronto Water Division")
    judge, server = judge_with(engine, chat_json(answer()))
    eval_id = run_evaluation(engine, w.run_id, dataset, judge=judge)
    store = EvaluationStore(engine)
    proposed = next(r for r in store.field_results(eval_id)
                    if r["stage"] == "PROPOSED" and r["field_key"] == "utility_name")
    assert proposed["status"] == "MATCH"
    assert proposed["comparison"]["deterministic_status"] == "PARTIAL_MATCH"
    assert store.run(eval_id)["config"]["judge"]["judged"] >= 1
    metrics = {(m["scope_key"], m["metric"]): m for m in store.metrics(eval_id)}
    assert metrics[("PROPOSED", "accuracy_strict")]["detail"]["provisional"] >= 1
    # PROPOSED and AUTO ask the same question: one request
    assert len(server.requests) == 1

    store.add_adjudication(eval_id, "FIELD_RESULT", proposed["id"], "CONFIRM_MATCH", "analyst")
    view = effective(store, eval_id)
    verified = next(r for r in view["results"] if r["id"] == proposed["id"])
    assert verified["status"] == "MATCH" and verified["adjudication"]
    metrics = {(m["scope_key"], m["metric"]): m for m in view["metrics"]}
    before = metrics[("PROPOSED", "accuracy_strict")]["detail"]["provisional"]
    # the person overrides the judge: the cell no longer counts as provisional
    assert before == sum(1 for r in view["results"] if r["stage"] == "PROPOSED"
                         and (r["comparison"] or {}).get("judgment_id")
                         and not r["adjudication"])

# --- Toronto calibration cases ----------------------------------------------------------------

def test_toronto_calibration_cases_are_well_formed():
    data = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    keys = {k for k, _ in COLUMNS}
    descriptions = load_descriptions()
    assert set(descriptions) == keys
    assert data["labels_confirmed_by"] in (None, "") or isinstance(data["labels_confirmed_by"],
                                                                  str)
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids)) >= 8
    for c in data["cases"]:
        assert c["field"] in keys and c["expected"] in (
            "MATCH", "PARTIAL_MATCH", "MISMATCH", "NEEDS_REVIEW")
        assert c["manual"] and c["automated"] and c["why"]
