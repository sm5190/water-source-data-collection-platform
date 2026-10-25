"""Researcher-facing scores (evaluation/metrics.py::simple_scores) and the proposed-value
order (evaluate.py::proposal_order), on hand-computed fixtures."""

from water_source_research.evaluation.evaluate import proposal_order
from water_source_research.evaluation.metrics import simple_scores
from water_source_research.services.field_state import load_field_definitions

def cell(status, judged=False, adjudicated=False):
    return {"status": status, "comparison": {"judgment_id": 7} if judged else {},
            "adjudication": {"decision": "CONFIRM_MATCH"} if adjudicated else None}

def test_scores_keep_accuracy_and_coverage_separate():
    rows = ([cell("MATCH")] * 4 + [cell("PARTIAL_MATCH")] * 2 + [cell("MISMATCH"),
            cell("TEMPORAL_MISMATCH"), cell("SCOPE_MISMATCH"), cell("NEEDS_REVIEW")]
            + [cell("MISSING_PREDICTION")] * 10
            + [cell("MISSING_REFERENCE")] * 3 + [cell("NOT_COMPARABLE"), cell("NOT_RESEARCHED")])
    s = simple_scores(rows)
    # comparable = 4 + 2 + 3 + 1 + 10 = 20; answered = 10
    assert (s["comparable"], s["answered"]) == (20, 10)
    assert s["coverage"] == 0.5
    assert s["accuracy_strict"] == 0.4  # 4 / 10
    assert s["accuracy_lenient"] == 0.5  # (4 + 0.5 x 2) / 10
    assert (s["mismatch"], s["needs_verification"], s["missing_automated"],
            s["missing_manual"]) == (3, 1, 10, 3)

def test_few_correct_answers_cannot_look_like_success():
    rows = [cell("MATCH")] * 2 + [cell("MISSING_PREDICTION")] * 18
    s = simple_scores(rows)
    assert s["accuracy_strict"] == 1.0 and s["coverage"] == 0.1

def test_empty_denominators_are_not_scores():
    s = simple_scores([cell("MISSING_REFERENCE"), cell("NOT_RESEARCHED")])
    assert s["comparable"] == 0 and s["coverage"] is None and s["accuracy_strict"] is None

def test_unverified_semantic_judgments_are_provisional():
    rows = [cell("MATCH", judged=True), cell("PARTIAL_MATCH", judged=True),
            cell("MATCH", judged=True, adjudicated=True), cell("MATCH")]
    assert simple_scores(rows)["provisional"] == 2

def claim(uid, outcome="VALID", source_type="UTILITY", value_in_quote="PASS"):
    return {"claim_uid": uid, "validation_outcome": outcome,
            "validation": {"checks": [{"code": "VALUE_IN_QUOTE", "result": value_in_quote}]},
            "citations": [{"url": f"https://x/{uid}", "source_type": source_type}]}

def test_proposed_candidate_order_never_uses_confidence():
    d = load_field_definitions()["total_city_water_withdrawals_m3_day"]
    claims = [{**claim("invalid", "INVALID"), "confidence": 0.99},
              {**claim("academic", source_type="ACADEMIC"), "confidence": 0.9},
              {**claim("warned", "VALID_WITH_WARNINGS"), "confidence": 0.95},
              {**claim("official"), "confidence": 0.1},
              {**claim("official-later"), "confidence": 0.8}]
    assert [c["claim_uid"] for c in proposal_order(d, claims)] == [
        "official", "official-later", "academic", "warned", "invalid"]
