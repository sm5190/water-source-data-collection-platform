"""Evaluator vs independently written manual findings (evaluation/manual.py)."""

from pathlib import Path
from water_source_research.evaluation.manual import compare_manual, load_findings

TORONTO = Path(__file__).with_name("toronto_manual_findings.yaml")

def view(results, diagnoses=()):
    return {"results": results, "diagnoses": list(diagnoses)}

def result(field, stage, status):
    return {"field_key": field, "stage": stage, "record_key": "", "status": status}

def diagnosis(field, cause, symptoms=(), stage="RAW", probes=()):
    return {"field_key": field, "stage": stage, "record_key": "", "root_cause": cause,
            "confidence": "PROBABLE", "explanation": "x", "symptoms": list(symptoms),
            "probes": list(probes)}

def test_the_toronto_findings_file_is_well_formed():
    data = load_findings(TORONTO)
    assert data["research_run_id"].startswith("8ad1fa90")
    assert all({"field", "stage", "status"} <= set(f) for f in data["findings"])

def test_agreement_disagreement_and_missing_cells():
    manual = {"findings": [
        {"field": "a", "stage": "RAW", "status": ["MATCH"]},
        {"field": "b", "stage": "RAW", "status": ["MISMATCH", "SCOPE_MISMATCH"],
         "root_cause": ["DISCOVERY_FAILURE"], "symptoms": ["TEMPORAL_MISMATCH"]},
        {"field": "c", "stage": "FINAL", "status": ["MATCH"]}],
        "run_findings": [{"symptom": "SOURCE_CLASSIFICATION_ERROR", "mentions": "toronto.name"}]}
    rows = compare_manual(view(
        [result("a", "RAW", "MATCH"), result("b", "RAW", "TEMPORAL_MISMATCH")],
        [diagnosis("b", "EVIDENCE_UNAVAILABLE", ["TEMPORAL_MISMATCH"]),
         diagnosis("", "UNKNOWN", ["SOURCE_CLASSIFICATION_ERROR"], stage="RUN",
                   probes=[{"probe": "source_type", "observed": {"url": "https://toronto.name/x"}}])]),
        manual)
    verdicts = {(r["field"], r["attribute"]): r["verdict"] for r in rows}
    assert verdicts == {("a", "status"): "AGREE", ("b", "status"): "DISAGREE",
                        ("b", "root_cause"): "DISAGREE", ("b", "symptoms"): "AGREE",
                        ("c", "status"): "NOT_EVALUATED", ("(run)", "symptom"): "AGREE"}
