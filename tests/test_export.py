"""Research to export: a run pauses for review, the reviewer decides, the run resumes, the
SupplyRecord is assembled with derived values, and the standardized CSV/XLSX and provenance
exports trace every value to its claim and source (mocked services, SQLite)."""

import csv
import io
import pytest
from procedure_helpers import make_engine
from review_helpers import W, World, decision, withdrawal_quote_evidence
from slice_helpers import PDF_URL, UTIL_URL
from water_source_research.research_queue.queue import JobStatus
from water_source_research.schemas.standardized import COLUMNS, HEADERS

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path, "export.db")

def reviewed_run(engine) -> World:
    w = World(engine)
    w.work()
    item = w.item(W)
    r = w.decide(decision(item, "CORRECT", value=980000, reason="the quote says 980,000",
                          claim_uids=[item["payload"]["candidates"][0]["claim_uid"]],
                          evidence=withdrawal_quote_evidence(item)),
                 decision(w.item("water_type"), "MARK_UNKNOWN", reason="not stated"))
    assert r.status_code == 200, r.text
    assert w.resume().status_code == 202
    w.work()
    assert w.job().status is JobStatus.SUCCEEDED
    return w

def read_csv(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))

def test_reviewed_run_exports_one_standardized_row_per_source(engine):
    w = reviewed_run(engine)
    assert w.job().completion == "FINALIZED_WITH_GAPS"  # water_type is an explicit Unknown

    records = w.client.get(f"/research/runs/{w.run_id}/records").json()
    [rec] = records["records"]
    assert rec["water_source"] == "Lake Ontario" and rec["utility_name"] == "Toronto Water"
    assert records["completion"] == "FINALIZED_WITH_GAPS"
    assert not [c for c in records["quality"] if c["result"] == "FAIL"]

    r = w.client.get(f"/research/runs/{w.run_id}/export", params={
        "format": "csv", "requested_by": "analyst@example.org"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].endswith('_standardized.csv"')
    rows = read_csv(r.content)
    assert list(rows[0]) == [h for _, h in COLUMNS]  # the workbook's 32 columns, in order
    [row] = rows
    assert row["Water Source"] == "Lake Ontario" and row["Utility Name"] == "Toronto Water"
    assert row["Total City Water Withdrawals (m3/d)"] == "980000"
    assert row["Total City Water Withdrawals Date"] == "2025"
    # one source: its contribution is the total (allocated), 100% of withdrawals
    assert row["Source Water Withdrawal Contribution (m3/d)"] == "980000"
    assert row["Source Water Withdrawal Contribution Method"] == "Reported Daily"
    assert row["Source Water Withdrawal Contribution Date"] == "2025"
    assert row["Source Water Withdrawal Percentage"] == "100.000"
    assert row["Water Type"] == "Unknown"  # reviewer: not stated anywhere
    assert row["POD Latitude"] == "Unknown"  # not researched in this run: never blank
    assert all(v != "" for v in row.values())
    assert UTIL_URL in row["References"] and PDF_URL in row["References"]

def test_provenance_traces_every_value_to_a_source_or_calculation(engine):
    w = reviewed_run(engine)
    r = w.client.get(f"/research/runs/{w.run_id}/export",
                     params={"format": "csv", "kind": "provenance"})
    assert r.status_code == 200
    prov = read_csv(r.content)
    known = [p for p in prov if p["value_status"] == "KNOWN"]
    assert known and all(p["citation_url"] or p["calculation_rule"] or p["resolution_status"]
                         == "FROM_CITY_CATALOG" for p in known)
    total = next(p for p in prov if p["field_key"] == W)
    assert total["scope"] == "CITY" and total["citation_url"] == PDF_URL
    assert total["quote"] == "980,000 cubic metres" and total["chunk_id"]
    assert total["review_decision_ids"]  # the reviewer's correction
    contrib = [p for p in prov if p["field_key"] == "source_withdrawal_contribution_m3_day"]
    assert contrib[0]["calculation_rule"] == "SINGLE_SOURCE_TOTAL"
    assert contrib[0]["citation_url"] == PDF_URL  # through its input, the total
    water_type = next(p for p in prov if p["field_key"] == "water_type")
    assert water_type["export_value"] == "Unknown" and water_type["review_decision_ids"]

def test_xlsx_has_records_provenance_quality_and_review_log(engine):
    from openpyxl import load_workbook
    w = reviewed_run(engine)
    r = w.client.get(f"/research/runs/{w.run_id}/export", params={"format": "xlsx"})
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Standardization", "Provenance", "Quality control", "Review log"]
    sheet = wb["Standardization"]
    header = [c.value for c in sheet[1]]
    assert header == [h for _, h in COLUMNS]
    values = dict(zip(header, [c.value for c in sheet[2]], strict=True))
    assert values[HEADERS["water_source"]] == "Lake Ontario"
    log = list(wb["Review log"].values)
    assert {row[log[0].index("decision")] for row in log[1:]} == {"CORRECT", "MARK_UNKNOWN"}
    exports = w.client.get(f"/research/runs/{w.run_id}/records").json()["exports"]
    assert [e["format"] for e in exports] == ["xlsx"]
    assert exports[0]["sha256"] == r.headers["x-export-sha256"]

def test_trace_reaches_the_quoted_chunk_and_document(engine):
    w = reviewed_run(engine)
    [rec] = w.client.get(f"/research/runs/{w.run_id}/records").json()["records"]
    trace = w.client.get(f"/research/runs/{w.run_id}/records/{rec['record_key']}/trace",
                         params={"field_key": "source_withdrawal_percentage"}).json()
    assert trace["value"]["calculation"]["rule"] == "PERCENT_OF_TOTAL_WITHDRAWAL"
    inputs = {i["field_key"]: i for i in trace["inputs"]}
    [claim] = inputs[W]["claims"]
    [cite] = claim["citations"]
    assert "980,000 cubic metres" in cite["chunk_text"]
    assert cite["document"]["requested_url"] == PDF_URL and cite["document"]["status"] == "OK"
    total = w.client.get(f"/research/runs/{w.run_id}/records/{rec['record_key']}/trace",
                         params={"field_key": W}).json()
    assert [d["decision"] for d in total["review_decisions"]] == ["CORRECT"]
    assert any(r["decided_by"] == "REVIEWER" for r in total["resolution_history"])

def test_export_is_refused_until_the_run_is_finished_and_complete(engine):
    w = World(engine)
    w.work()  # waiting for review
    r = w.client.get(f"/research/runs/{w.run_id}/export", params={"format": "csv"})
    assert r.status_code == 409 and "AWAITING_REVIEW" in r.json()["detail"]
    r = w.client.get(f"/research/runs/{w.run_id}/export", params={"format": "pdf"})
    assert r.status_code == 422
