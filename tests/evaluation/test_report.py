"""Adjudicated view, reports and the report/overview API of an evaluation run."""

import csv
import io
import json
import pytest
from eval_world import W, EvalWorld, import_world_reference
from fastapi.testclient import TestClient
from procedure_helpers import make_engine
from water_source_research.api.main import create_app
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.report import effective, overview, render
from water_source_research.evaluation.store import EvaluationStore

@pytest.fixture
def evaluated(tmp_path):
    engine = make_engine(tmp_path, "report.db")
    w = EvalWorld(engine)
    w.work()
    dataset = import_world_reference(tmp_path, engine)
    return w, EvaluationStore(engine), run_evaluation(engine, w.run_id, dataset)

def stage_metric(view, stage, name):
    return next(m for m in view["metrics"] if m["scope"] == "stage"
                and m["scope_key"] == stage and m["metric"] == name)

def raw_result(store, eval_id, field):
    return next(r for r in store.field_results(eval_id, "RAW", field))

def test_without_adjudication_the_view_is_the_evaluation(evaluated):
    _, store, eval_id = evaluated
    view = effective(store, eval_id)
    assert view["metrics_basis"] == "as evaluated"
    assert view["metrics"] == store.metrics(eval_id)
    assert all(r["status"] == r["original_status"] for r in view["results"])

def test_adjudication_changes_the_view_and_metrics_but_not_stored_results(evaluated):
    _, store, eval_id = evaluated
    before = stage_metric(effective(store, eval_id), "RAW", "accuracy")
    target = raw_result(store, eval_id, W)
    assert target["status"] == "MISMATCH"
    store.add_adjudication(eval_id, "FIELD_RESULT", target["id"], "CONFIRM_MATCH", "a")
    view = effective(store, eval_id)
    after = stage_metric(view, "RAW", "accuracy")
    assert view["metrics_basis"] == "adjudicated" and view["adjudicated_results"] == 1
    assert after["numerator"] == before["numerator"] + 1
    assert after["denominator"] == before["denominator"]
    adjusted = next(r for r in view["results"] if r["id"] == target["id"])
    assert (adjusted["status"], adjusted["original_status"]) == ("MATCH", "MISMATCH")
    assert raw_result(store, eval_id, W)["status"] == "MISMATCH"  # stored row untouched

def test_reference_error_excludes_the_cell_and_latest_adjudication_wins(evaluated):
    _, store, eval_id = evaluated
    target = raw_result(store, eval_id, W)
    cells = stage_metric(effective(store, eval_id), "RAW", "cells")["numerator"]
    store.add_adjudication(eval_id, "FIELD_RESULT", target["id"], "CONFIRM_MATCH", "a")
    store.add_adjudication(eval_id, "FIELD_RESULT", target["id"], "REFERENCE_ERROR", "b",
                           note="the workbook's year is wrong")
    view = effective(store, eval_id)
    assert stage_metric(view, "RAW", "cells")["numerator"] == cells - 1
    assert len(view["adjudications"]) == 2  # append-only history

def test_corrected_diagnosis_keeps_the_original_cause(evaluated):
    _, store, eval_id = evaluated
    d = next(d for d in store.diagnoses(eval_id) if d["field_key"] == W)
    store.add_adjudication(eval_id, "DIAGNOSIS", d["id"], "CORRECT_DIAGNOSIS", "a",
                           root_cause="GROUNDING_FAILURE")
    view = effective(store, eval_id)
    adjusted = next(x for x in view["diagnoses"] if x["id"] == d["id"])
    assert adjusted["root_cause"] == "GROUNDING_FAILURE"
    assert adjusted["original_root_cause"] == d["root_cause"]

def test_reports_render_in_each_format(evaluated):
    _, store, eval_id = evaluated
    view = effective(store, eval_id)
    content, name, media = render(view, "summary", "md")
    text = content.decode("utf-8")
    assert name.endswith(".md") and media == "text/markdown"
    assert "## Metrics (numerator/denominator)" in text and "## Instrumentation gaps" in text
    assert "at coverage" in text  # auto precision always carries its coverage
    content, name, _ = render(view, "city", "csv")
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    withdrawal = next(r for r in rows if r["field"] == W)
    assert withdrawal["RAW status"] == "MISMATCH" and "450000" in withdrawal["RAW prediction"]
    assert withdrawal["reference"] == "980000 (2023)"
    failures = json.loads(render(view, "failures", "json")[0])["rows"]
    assert any(f["root_cause"] == "EXTRACTION_FAILURE" for f in failures)
    for kind in ("fields", "evidence", "metrics"):
        assert render(view, kind, "csv")[0]
    with pytest.raises(ValueError):
        render(view, "summary", "csv")
    with pytest.raises(ValueError):
        render(view, "fields", "md")

def test_overview_takes_the_latest_evaluation_per_city(evaluated):
    w, store, eval_id = evaluated
    second = run_evaluation(store.engine, w.run_id, None)
    [row] = overview(store)
    assert row["eval_run_id"] == second != eval_id
    assert row["metrics"]["AUTO.precision"]["denominator"] >= 1
    assert "EXTRACTION_FAILURE" in row["root_causes"]

def test_api_serves_adjudicated_views_reports_and_validates(evaluated):
    w, store, eval_id = evaluated
    client = TestClient(create_app(queue=w.queue, evidence=w.evidence, reviews=w.reviews,
                                   records=w.records))
    target = raw_result(store, eval_id, W)
    diagnosis = next(d for d in store.diagnoses(eval_id) if d["field_key"] == W)
    post = lambda body: client.post(f"/evaluation/runs/{eval_id}/adjudications",
                                    json={"reviewer": "analyst", **body})
    assert post({"target": "FIELD_RESULT", "target_id": target["id"],
                 "decision": "CORRECT_DIAGNOSIS", "root_cause": "UNKNOWN"}).status_code == 422
    assert post({"target": "FIELD_RESULT", "target_id": target["id"],
                 "decision": "OVERRIDE_STATUS", "status": "GREAT"}).status_code == 422
    assert post({"target": "DIAGNOSIS", "target_id": diagnosis["id"],
                 "decision": "CORRECT_DIAGNOSIS", "root_cause": "BAD_LUCK"}).status_code == 422
    assert post({"target": "FIELD_RESULT", "target_id": target["id"],
                 "decision": "OVERRIDE_STATUS", "status": "SCOPE_MISMATCH"}).status_code == 201
    run = client.get(f"/evaluation/runs/{eval_id}").json()
    assert run["metrics_basis"] == "adjudicated" and run["adjudicated_results"] == 1
    raw = [r for r in client.get(f"/evaluation/runs/{eval_id}/fields?stage=RAW").json()
           if r["field_key"] == W]
    assert raw[0]["status"] == "SCOPE_MISMATCH" and raw[0]["original_status"] == "MISMATCH"
    r = client.get(f"/evaluation/runs/{eval_id}/report?kind=city&format=csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert "SCOPE_MISMATCH*" in r.content.decode("utf-8-sig")
    assert client.get(f"/evaluation/runs/{eval_id}/report?kind=summary&format=md"
                      ).text.startswith("# Evaluation")
    assert client.get(f"/evaluation/runs/{eval_id}/report?kind=nope").status_code == 422
    assert client.get(f"/evaluation/runs/{eval_id}/report?kind=summary&format=csv"
                      ).status_code == 422
    assert client.get("/evaluation/runs/missing/report").status_code == 404
    [row] = client.get("/evaluation/overview").json()
    assert row["eval_run_id"] == eval_id and row["metrics_basis"] == "adjudicated"
