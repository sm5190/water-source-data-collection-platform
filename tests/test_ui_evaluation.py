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

class EvalClient(FakeClient):
    def __init__(self, evaluated=True, **kw):
        super().__init__(**kw)
        self.evaluated = evaluated
        self.evaluations = []
        self.adjudications = []
        self.reports = []

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

def test_evaluation_mode_shows_overview_comparison_and_investigation():
    at = evaluation_app(EvalClient())
    assert [t.label for t in at.tabs] == ["📊 Overview", "🏙️ City comparison",
                                          "🔎 Field investigation", "📄 Reports"]
    overview = at.dataframe[0].value
    assert overview.iloc[0]["Raw coverage"] == "14/27 (52%)"
    assert overview.iloc[0]["Auto precision"] == "4/4 (100%)"
    comparison = at.dataframe[1].value
    assert comparison.iloc[0]["RAW"] == "1420000 [TEMPORAL_MISMATCH]"
    assert "TEMPORAL_MISMATCH" in comparison.iloc[0]["RAW result"]
    assert [m.value for m in at.metric][:2] == ["14/27 (52%)", "4/4 (100%)"]
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
