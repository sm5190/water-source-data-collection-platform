"""Evaluation mode of the dashboard, driven headlessly with AppTest and a fake API client."""

import json
import pytest

pytest.importorskip("streamlit")
from test_ui_app import W, FakeClient, all_text, app

EVAL_ID = "eval-0001-aaaa"
REFERENCE = {"field_key": W, "raw": 1159745, "normalized": 1159745, "value_status": "KNOWN",
             "unit": "m3/d", "reference_year": 2024, "issues": [],
             "source_urls": ["https://toronto.ca/summary-2024.pdf"]}
RESULTS = [
    {"id": 11, "stage": "RAW", "scope": "CITY", "record_key": "", "field_key": W,
     "status": "TEMPORAL_MISMATCH", "original_status": "TEMPORAL_MISMATCH", "adjudication": None,
     "reference": REFERENCE, "comparison": {"detail": "different year", "tags": ["TEMPORAL"]},
     "predicted": {"candidates": [{
         "claim_uid": "c1", "value": 1420000, "status": "TEMPORAL_MISMATCH", "confidence": 0.8,
         "validation": "VALID_WITH_WARNINGS", "fail_codes": [], "tags": ["TEMPORAL"],
         "sources": [["https://uni.example/paper", "ACADEMIC"]]}],
         "top_candidate": "c1", "top_candidate_status": "TEMPORAL_MISMATCH"}, "links": {}},
    {"id": 12, "stage": "AUTO", "scope": "CITY", "record_key": "", "field_key": W,
     "status": "MISSING_PREDICTION", "original_status": "MISSING_PREDICTION",
     "adjudication": None, "reference": REFERENCE, "comparison": {"detail": "escalated"},
     "predicted": {"outcome": "ESCALATED", "value": None, "rule": None,
                   "reason_codes": ["NOT_OFFICIAL"]}, "links": {}},
]
DIAGNOSIS = {"id": 21, "stage": "RAW", "record_key": "", "field_key": W,
             "root_cause": "DISCOVERY_FAILURE", "original_root_cause": "DISCOVERY_FAILURE",
             "adjudication": None, "earliest_stage": "discovery", "confidence": "PROBABLE",
             "symptoms": ["TEMPORAL_MISMATCH"], "explanation": "source never a candidate",
             "probes": [{"probe": "evidence", "observed": {"chunks_with_value_count": 0}}]}
METRICS = [
    {"scope": "stage", "scope_key": "RAW", "metric": "coverage", "numerator": 14,
     "denominator": 27, "value": 14 / 27, "detail": {}},
    {"scope": "stage", "scope_key": "AUTO", "metric": "precision", "numerator": 4,
     "denominator": 4, "value": 1.0, "detail": {"coverage": "4/27"}},
]

def summary(label, match, partial, mismatch, missing, answered, comparable, provisional=0):
    return {"label": label, "match": match, "partial": partial, "mismatch": mismatch,
            "needs_verification": 0, "missing_automated": missing, "missing_manual": 3,
            "not_comparable": 1, "not_researched": 0, "provisional": provisional,
            "answered": answered, "comparable": comparable, "cells": comparable + 4,
            "coverage": answered / comparable if comparable else None,
            "accuracy_strict": match / answered if answered else None,
            "accuracy_lenient": (match + 0.5 * partial) / answered if answered else None}

def comparison_row(field_key, field, manual, automated, group, status, result_id, **kw):
    return {"field_key": field_key, "field": field, "record_key": "", "record_label": None,
            "result_id": result_id, "manual": manual, "automated": automated,
            "status": status, "group": group, "sub_label": kw.pop("sub_label", None),
            "provisional": kw.pop("provisional", False),
            "proposed_not_accepted": kw.pop("proposed", False),
            "explanation": kw.pop("explanation", ""), "details": {
                "manual_year": kw.pop("manual_year", None),
                "manual_sources": kw.pop("manual_sources", []),
                "automated_year": kw.pop("automated_year", None),
                "citations": kw.pop("citations", []), "source": "PROPOSED",
                "selection_reason": None, "candidate_count": len(kw.get("candidates", [])),
                "candidates": kw.pop("candidates", []), "judgment": kw.pop("judgment", None),
                "original_status": kw.pop("original_status", status), "adjudication": None}}

ROWS = [
    comparison_row("utility_name", "Utility Name", "City of Toronto Water Division",
                   "Toronto Water", "MATCH", "MATCH", 31, provisional=True,
                   explanation="The quote says Toronto Water is the City's water division.",
                   judgment={"verdict": "EQUIVALENT", "overlapping_facts": ["same utility"],
                             "missing_facts": [], "differing_facts": [], "model": "gpt-oss",
                             "prompt_version": "judge-1"}, original_status="PARTIAL_MATCH"),
    comparison_row(W, "Total City Water Withdrawals (m3/d)", "1159745 (2024)", "1420000",
                   "MISMATCH", "TEMPORAL_MISMATCH", 32, sub_label="different year",
                   proposed=True, manual_year=2024, automated_year=2010,
                   manual_sources=["https://toronto.ca/summary-2024.pdf"],
                   citations=[{"url": "https://uni.example/paper", "title": "Study",
                               "quote": "an estimated 1420 MLD in 2010"}],
                   candidates=[{"value": "1420000", "status": "TEMPORAL_MISMATCH",
                                "selected": True, "sources": []},
                               {"value": "980000", "status": "MISMATCH", "selected": False,
                                "sources": []}]),
    comparison_row("water_type", "Water Type", "Surface", "", "MISSING_AUTOMATED",
                   "MISSING_PREDICTION", 33),
    {**comparison_row("references", "References", "", "", "NOT_EVALUATED", "NOT_EVALUATED",
                      None), "result_id": None},
]
PERFORMANCE = {
    "stages": ["PROPOSED", "AUTO"],
    "stage_labels": {"PROPOSED": "Proposed", "AUTO": "Auto-accepted", "FINAL": "Final reviewed"},
    "cities": [{"city_key": "toronto|canada", "eval_run_id": EVAL_ID, "research_run_id": "run-1",
                "created_at": "2026-10-25T10:00:00", "metrics_basis": "as evaluated",
                "scores": {"PROPOSED": summary("Proposed", 6, 2, 8, 6, 16, 22, 1),
                           "AUTO": summary("Auto-accepted", 4, 0, 0, 18, 4, 22)}}],
    "fields": [{"field_key": W, "field": "Total City Water Withdrawals (m3/d)",
                "PROPOSED": {**summary("Proposed", 0, 0, 1, 0, 1, 1), "n_cities": 1},
                "AUTO": {**summary("Auto-accepted", 0, 0, 0, 1, 0, 1), "n_cities": 1}}],
    "dataset": {"PROPOSED": {**summary("Proposed", 6, 2, 8, 6, 16, 22), "n_cities": 1,
                             "mean_city_accuracy_strict": None,
                             "mean_city_accuracy_lenient": None, "missing_data_rate": 0.2727},
                "AUTO": {**summary("Auto-accepted", 4, 0, 0, 18, 4, 22), "n_cities": 1,
                         "mean_city_accuracy_strict": None, "mean_city_accuracy_lenient": None,
                         "missing_data_rate": 0.8182}},
    "judge": {"judged": 1, "verified": 0, "agreement_with_people": None, "provisional": 1,
              "validated": False},
    "history": [{"eval_run_id": EVAL_ID, "city_key": "toronto|canada",
                 "created_at": "2026-10-25T10:00:00", "compare_policy_version": "compare-4",
                 "scores": {"PROPOSED": {
                     "accuracy_strict": {"numerator": 6, "denominator": 16, "value": 0.375},
                     "accuracy_lenient": {"numerator": 7, "denominator": 16, "value": 0.4375},
                     "answer_coverage": {"numerator": 16, "denominator": 22,
                                         "value": 16 / 22}}}}],
}

class EvalClient(FakeClient):
    def __init__(self, evaluated=True, **kw):
        super().__init__(**kw)
        self.evaluated = evaluated
        self.evaluations = []
        self.adjudications = []
        self.reports = []
        self.comparisons = []

    def eval_cities(self):
        if not self.evaluated:
            return []
        return [{"city_key": "toronto|canada", "latest_eval_run_id": EVAL_ID,
                 "evaluations": [{"id": EVAL_ID, "stages": ["RAW", "PROPOSED", "AUTO"],
                                  "created_at": "2026-10-25T10:00:00",
                                  "research_run_id": "run-1"}]}]

    def eval_comparison(self, eval_id, stage):
        self.comparisons.append(stage)
        return {"stage": stage, "stages": ["PROPOSED", "AUTO"], "rows": ROWS,
                "metrics_basis": "as evaluated", "judge_validated": False,
                "summaries": {"PROPOSED": summary("Proposed", 6, 2, 8, 6, 16, 22, 1),
                              "AUTO": summary("Auto-accepted", 4, 0, 0, 18, 4, 22)}}

    def eval_performance(self):
        if not self.evaluated:
            return {**PERFORMANCE, "cities": [], "history": []}
        return PERFORMANCE

    def runs(self, city=None, country=None, limit=50):
        return [self._run()]

    def eval_references(self):
        return [{"id": "ds1", "filename": "Sarina Water Source.xlsx", "cities": ["Toronto"]}]

    def eval_overview(self):
        if not self.evaluated:
            return []
        return [{"eval_run_id": EVAL_ID, "city_key": "toronto|canada", "research_run_id": "run-1",
                 "stages": ["RAW", "AUTO"], "created_at": "2026-10-19T10:00:00",
                 "metrics_basis": "as evaluated",
                 "metrics": {"RAW.coverage": {"numerator": 14, "denominator": 27,
                                              "value": 14 / 27},
                             "AUTO.precision": {"numerator": 4, "denominator": 4,
                                                "value": 1.0}},
                 "quote_verified": None,
                 "root_causes": {"DISCOVERY_FAILURE": 4, "EXTRACTION_FAILURE": 2}}]

    def eval_runs(self, research_run_id=None):
        return [{"id": EVAL_ID, "city_key": "toronto|canada", "stages": ["RAW", "AUTO"],
                 "created_at": "2026-10-19T10:00:00"}] if self.evaluated else []

    def eval_run(self, eval_id):
        return {"id": EVAL_ID, "research_run_id": "run-1", "origin_run_id": "run-0",
                "dataset_id": "ds1", "city_key": "toronto|canada", "stages": ["RAW", "AUTO"],
                "compare_policy_version": "compare-1", "snapshot_sha256": "ab" * 32,
                "metrics": METRICS, "metrics_basis": "as evaluated", "adjudicated_results": 0,
                "adjudicated_diagnoses": 0,
                "gaps": [{"code": "RAW_SEARCH_RESULTS", "detail": "not persisted"}]}

    def evaluate(self, research_run_id, dataset_id=None):
        self.evaluations.append((research_run_id, dataset_id))
        return {"id": EVAL_ID, "stages": ["RAW", "AUTO"]}

    def eval_fields(self, eval_id):
        return RESULTS

    def eval_field(self, eval_id, field_key):
        return {"field_key": field_key, "results": RESULTS, "diagnoses": [DIAGNOSIS],
                "evidence": [{"claim_uid": "c1abcdefghij", "citation_index": 0,
                              "check": "QUOTE_VERIFIED", "result": "PASS", "detail": None}],
                "adjudications": []}

    def adjudicate(self, eval_id, body):
        self.adjudications.append(body)
        return {"id": 1, **body}

    def eval_report(self, eval_id, kind, fmt):
        self.reports.append((kind, fmt))
        if fmt == "json":
            rows = [{"field": W, "record": "", "reference": "1159745 (2024)",
                     "reference_sources": "https://toronto.ca/summary-2024.pdf",
                     "RAW prediction": "1420000 [TEMPORAL_MISMATCH]",
                     "RAW status": "TEMPORAL_MISMATCH", "AUTO prediction": "escalated",
                     "AUTO status": "MISSING_PREDICTION"}]
            return json.dumps({"rows": rows}).encode(), "r.json", "application/json"
        return b"# Evaluation", f"r.{fmt}", "text/markdown"

def evaluation_app(client):
    at = app(client)
    at.radio(key="mode").set_value("Evaluation").run()
    assert not at.exception, at.exception
    return at

def frame(at, column):
    return next(df.value for df in at.dataframe if column in df.value.columns)

# --- main view: manual vs automated --------------------------------------------------------

def test_city_comparison_shows_manual_and_automated_side_by_side():
    client = EvalClient()
    at = evaluation_app(client)
    assert [t.label for t in at.tabs][:3] == ["🏙️ City comparison", "📊 Research performance",
                                              "🛠 Advanced"]
    table = frame(at, "Manual Data")
    assert list(table.columns) == ["Field", "Manual Data", "Automated Data", "Match Status"]
    rows = {r["Field"]: r for r in table.to_dict("records")}
    assert len(rows) == len(ROWS)  # every field is listed, even those not produced
    withdrawal = rows["Total City Water Withdrawals (m3/d)"]
    assert withdrawal["Manual Data"] == "1159745 (2024)"
    assert withdrawal["Automated Data"] == ("1420000 (proposed — sent to review) "
                                            "[1 of 2 values found]")
    assert withdrawal["Match Status"] == "🔴 Mismatch (different year)"
    assert rows["Utility Name"]["Match Status"] == "🟢 Match · provisional"
    assert rows["Water Type"]["Match Status"] == "⚪ Missing in automated data"
    assert rows["References"]["Match Status"] == "▫️ Not produced at this stage"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Accuracy (strict)"] == "6/16 (38%)"
    assert metrics["Accuracy (lenient)"] == "7/16 (44%)"
    assert metrics["Coverage"] == "16/22 (73%)"
    text = all_text(at)
    assert "Auto-accepted: accuracy 4/4 (100%) strict" in text  # both stages, always
    assert "coverage 4/22 (18%)" in text
    assert "provisional" in text and "has not been validated yet" in text
    assert "Final reviewed results appear after" in text
    assert client.comparisons[-1] == "PROPOSED"

def test_switching_to_auto_accepted_results():
    client = EvalClient()
    at = evaluation_app(client)
    at.radio(key="eval_stage:toronto|canada").set_value("AUTO").run()
    assert not at.exception, at.exception
    assert client.comparisons[-1] == "AUTO"

def test_field_details_show_evidence_candidates_and_verify_buttons():
    client = EvalClient()
    at = evaluation_app(client)
    text = all_text(at)
    assert "> an estimated 1420 MLD in 2010" in text
    assert "Source: [Study](https://uni.example/paper)" in text
    assert "- [https://toronto.ca/summary-2024.pdf](https://toronto.ca/summary-2024.pdf)" in text
    assert "**All 2 values the system found** (the compared one is marked)" in text
    assert "- **→** 1420000" in text and "- 980000" in text
    assert "Agree: same utility" in text
    assert "Provisional — decided by the automatic meaning check" in text
    verify = [b for b in at.button if b.label == "They match"]
    assert verify and all(b.disabled for b in verify)  # needs a name
    at.text_input(key="reviewer").set_value("analyst").run()
    next(b for b in at.button if b.key == "verify:31:CONFIRM_MATCH:").click().run()
    assert client.adjudications[-1] == {
        "target": "FIELD_RESULT", "target_id": 31, "reviewer": "analyst", "status": None,
        "root_cause": None, "note": None, "decision": "CONFIRM_MATCH"}
    next(b for b in at.button if b.key == "verify:32:OVERRIDE_STATUS:PARTIAL_MATCH")\
        .click().run()
    assert client.adjudications[-1]["status"] == "PARTIAL_MATCH"

def test_research_performance_shows_scores_without_inventing_averages():
    at = evaluation_app(EvalClient())
    by_city = frame(at, "City")
    assert by_city.iloc[0]["Proposed accuracy (strict)"] == "6/16 (38%)"
    assert by_city.iloc[0]["Auto-accepted coverage"] == "4/22 (18%)"
    by_field = frame(at, "Cities")
    assert by_field.iloc[0]["Proposed accuracy (strict)"] == "0/1 (0%)"
    dataset = frame(at, "Missing-data rate")
    assert dataset.iloc[0]["Average of cities (strict)"] == "Not enough data"
    assert "**Not validated yet**" in all_text(at)

# --- Advanced (technical views) ---------------------------------------------------------------

def test_evaluation_mode_shows_overview_comparison_and_investigation():
    at = evaluation_app(EvalClient())
    assert [t.label for t in at.tabs][3:] == ["Overview", "Stage matrix", "Field investigation",
                                              "Reports"]
    overview = frame(at, "Raw coverage")
    assert overview.iloc[0]["Raw coverage"] == "14/27 (52%)"
    assert overview.iloc[0]["Auto precision"] == "4/4 (100%)"
    comparison = frame(at, "RAW result")
    assert comparison.iloc[0]["RAW"] == "1420000 [TEMPORAL_MISMATCH]"
    assert "TEMPORAL_MISMATCH" in comparison.iloc[0]["RAW result"]
    assert {"14/27 (52%)", "4/4 (100%)"} <= {m.value for m in at.metric}
    text = all_text(at)
    assert "**Diagnosis (RAW):** DISCOVERY_FAILURE" in text
    assert "source never a candidate" in text
    assert "https://toronto.ca/summary-2024.pdf" in text
    assert "RAW_SEARCH_RESULTS" in text

def test_adjudication_needs_a_reviewer_and_is_sent_to_the_api():
    client = EvalClient()
    at = evaluation_app(client)
    submit = [b for b in at.button if b.label == "Record adjudication"]
    assert submit and all(b.disabled for b in submit)
    at.text_input(key="reviewer").set_value("analyst").run()
    at.selectbox(key="adj_kind:DIAGNOSIS:21").set_value("CORRECT_DIAGNOSIS")
    at.selectbox(key="adj_cause:DIAGNOSIS:21").set_value("ACQUISITION_FAILURE")
    at.text_input(key="adj_note:DIAGNOSIS:21").set_value("the report page is scripted")
    form_button = next(b for b in at.button if b.label == "Record adjudication"
                       and b.form_id == "adj:DIAGNOSIS:21")
    form_button.click().run()
    assert client.adjudications == [{
        "target": "DIAGNOSIS", "target_id": 21, "decision": "CORRECT_DIAGNOSIS",
        "reviewer": "analyst", "status": None, "root_cause": "ACQUISITION_FAILURE",
        "note": "the report page is scripted"}]

def test_reports_are_prepared_on_request():
    client = EvalClient()
    at = evaluation_app(client)
    before = list(client.reports)  # the comparison table reads the city report as JSON
    next(b for b in at.button if b.label == "Prepare Summary (Markdown)").click().run()
    assert client.reports[len(before):].count(("summary", "md")) == 1
    assert any(d.label == "⬇️ Summary (Markdown)" for d in at.get("download_button"))

def test_new_evaluation_and_empty_state():
    client = EvalClient(evaluated=False)
    at = evaluation_app(client)
    assert "No completed evaluations yet." in all_text(at)
    next(b for b in at.button if b.label == "Evaluate").click().run()
    assert client.evaluations == [("run-1", None)]
