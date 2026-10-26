"""Reviewer corrections keep their reference year and source metadata through records and
export, and the evidence class says how the evidence was established: reviewer-supplied
sources are never INVALID and never machine-verified; overrides are human-verified."""

import csv
import io
import pytest
from procedure_helpers import make_engine
from review_helpers import W, World, decision, withdrawal_quote_evidence
from water_source_research.research_queue.queue import JobStatus

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path, "meta.db")

def finish(w, *decisions):
    r = w.decide(*decisions)
    assert r.status_code == 200, r.text
    w.resume()
    w.work()
    assert w.job().status is JobStatus.SUCCEEDED

def test_a_reviewer_supplied_correction_keeps_its_year_and_is_not_invalid(engine):
    w = World(engine)
    w.work()
    item = w.item(W)
    finish(w, decision(item, "CORRECT", value=980000,
                       claim_uids=[item["payload"]["candidates"][0]["claim_uid"]],
                       evidence={"url": "https://example.org/annual-report-2023.pdf",
                                 "title": "Annual report", "reference_year": 2023,
                                 "source_date": "2024-03-01", "page_number": 12}),
           decision(w.item("water_type"), "MARK_UNKNOWN"))
    corrected = next(c for c in w.evidence.claims(w.run_id)
                     if c["field_key"] == W and c.get("review_status") == "CORRECTED")
    q = corrected["qualifiers"]
    assert (q["evidence_class"], q["reference_year"], q["year_basis"]) == (
        "REVIEWER_SUPPLIED", 2023, "reviewer")
    assert "2024-03-01" in q["source_dates"]
    assert corrected["validation_outcome"] != "INVALID"
    linked = next(k for k in corrected["validation"]["checks"] if k["code"] == "CITATION_LINKED")
    assert linked["result"] == "WARN" and "not machine-verified" in linked["detail"]
    assert corrected["citations"][0]["page_number"] == 12

    records = w.client.get(f"/research/runs/{w.run_id}/records").json()
    total = records["city_values"].get(W) or {}
    assert total.get("reference_year") == 2023
    year = records["city_values"].get("total_city_water_withdrawals_year") or {}
    assert year.get("value") == 2023  # derived from the reviewer's year

    prov = w.client.get(f"/research/runs/{w.run_id}/export",
                        params={"format": "csv", "kind": "provenance"}).content
    rows = list(csv.DictReader(io.StringIO(prov.decode("utf-8-sig"))))
    row = next(r for r in rows if r["field_key"] == W and r["claim_uid"])
    assert row["evidence_class"] == "REVIEWER_SUPPLIED"
    assert row["claim_validation"] != "INVALID"
    assert row["reference_year"] == "2023"

def test_a_correction_quoting_the_runs_evidence_is_machine_verified(engine):
    w = World(engine)
    w.work()
    item = w.item(W)
    finish(w, decision(item, "CORRECT", value=980000,
                       claim_uids=[item["payload"]["candidates"][0]["claim_uid"]],
                       evidence=withdrawal_quote_evidence(item)),
           decision(w.item("water_type"), "MARK_NA"))
    corrected = next(c for c in w.evidence.claims(w.run_id)
                     if c["field_key"] == W and c.get("review_status") == "CORRECTED")
    assert corrected["qualifiers"]["evidence_class"] == "MACHINE_VERIFIED"

def test_an_override_is_human_verified_not_machine_verified(engine):
    w = World(engine)
    w.work()
    item = w.item(W)
    uid = item["payload"]["candidates"][0]["claim_uid"]
    finish(w, decision(item, "APPROVE", claim_uids=[uid], override_validation=True),
           decision(w.item("water_type"), "MARK_NA"))
    approved = next(c for c in w.evidence.claims(w.run_id) if c["claim_uid"] == uid)
    assert approved["qualifiers"]["evidence_class"] == "HUMAN_VERIFIED"
    # the machine checks' result is kept as it was: a person confirmed it anyway
    assert approved["validation_outcome"] == "INVALID"

def test_extracted_claims_with_located_quotes_are_machine_verified(engine):
    w = World(engine)
    w.work()
    utility = next(c for c in w.evidence.claims(w.run_id) if c["field_key"] == "utility_name")
    assert utility["qualifiers"]["evidence_class"] == "MACHINE_VERIFIED"
