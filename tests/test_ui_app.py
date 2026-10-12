"""Streamlit dashboard, driven headlessly with AppTest and a fake API client."""

from pathlib import Path
from typing import ClassVar
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src" / "water_source_research" / "ui" / "app.py")

CITIES = [
    {"label": "Toronto, Canada", "city": "Toronto", "country": "Canada",
     "jurisdiction": "Ontario"},
    {"label": "Madrid, Spain", "city": "Madrid", "country": "Spain",
     "jurisdiction": "Community of Madrid"},
]

class FakeClient:
    def __init__(self, status="SUCCEEDED", healthy=True, duplicate=False):
        self.status = status
        self.healthy = healthy
        self.duplicate = duplicate
        self.submitted = []
        self.cancelled = []

    def health(self):
        return self.healthy

    def queue_stats(self):
        return {"by_status": {self.status: 1}, "oldest_queued_age_seconds": None}

    def cities(self):
        return CITIES

    def submit(self, city, country, priority=0):
        self.submitted.append((city, country, priority))
        return {"run_id": "run-1", "status": "QUEUED", "created": not self.duplicate}

    def _run(self):
        return {"run_id": "run-1", "city_name": "Toronto", "country": "Canada",
                "status": self.status, "attempts": 1, "max_attempts": 3, "resumed": False,
                "last_error": None, "created_at": "2026-10-09T10:00:00+00:00",
                "started_at": "2026-10-09T10:00:01+00:00", "finished_at": None,
                "cancel_requested": False}

    def runs(self, city=None, country=None, limit=50):
        return [self._run()] if city == "Toronto" else []

    def steps(self, run_id):
        return {"run": self._run(), "expected_nodes": ["plan", "discover", "acquire", "extract",
                                                       "validate", "human_review", "finalize"],
                "steps": [{"attempt": 1, "seq": 1, "node": "plan", "duration_ms": 12.0,
                           "finished_at": None,
                           "summary": {"events": [], "fields_to_research": 32}},
                          {"attempt": 1, "seq": 2, "node": "extract", "duration_ms": 40.0,
                           "finished_at": None,
                           "summary": {"events": [{"seq": 1, "kind": "NODE",
                                                   "field_key": "utility_name",
                                                   "attrs": {"bundle_status": "READY"}}],
                                       "fields_needing_evidence": ["reservoir_names"]}}]}

    def review(self, run_id):
        return {"available": True, "items": [{
            "field_key": "total_city_water_withdrawals_m3_day", "field_type": "withdrawal",
            "field_status": "CONFLICT", "reason_code": "CONFLICT",
            "reason": "2 conflicting candidate claims", "decision": None,
            "citations": [{"claim_value": 1050000, "confidence": 0.8,
                           "url": "https://toronto.ca/report.pdf", "title": "Annual Report",
                           "page_number": 41, "passage": "1,050 ML/day in 2023"}]}]}

    def fields(self, run_id):
        return {"available": True, "fields": [{
            "field_key": "utility_name", "field_type": "utility", "derived": False,
            "depends_on": [], "status": "RESOLVED", "value": "Toronto Water",
            "value_origin": "REPORTED", "value_status": "KNOWN", "confidence": 0.9,
            "citations": [{"url": "https://toronto.ca/water", "title": "Toronto Water",
                           "page_number": 3, "passage": "Toronto Water operates…"}],
            "candidates": [], "needs_evidence": False, "strategy": "default",
            "context_bundle_id": "bundle-1",
            "transitions": [{"at": "t1", "node": "extract", "from": "UNRESOLVED",
                             "to": "CANDIDATE"}],
            "extraction_attempts": [{"at": "t1", "bundle_status": "READY", "claims": 1}],
            "reviewer_decisions": [], "accepted_evidence": []}]}

    def cancel(self, run_id):
        self.cancelled.append(run_id)
        return {}

def app(client) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["client"] = client
    at.run()
    assert not at.exception, at.exception
    return at

def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [str(e.value) for e in (*at.success, *at.info, *at.error)]
    return "\n".join(parts)

def test_city_dropdown_lists_catalog_with_jurisdiction():
    at = app(FakeClient())
    city_box = at.selectbox[0]
    assert len(city_box.options) == 2
    assert city_box.format_func(0) == "Toronto, Canada — Ontario"

def test_start_button_queues_selected_city():
    client = FakeClient()
    at = app(client)
    at.selectbox[0].set_value(0).run()
    at.button[0].click().run()
    assert client.submitted == [("Toronto", "Canada", 0)]
    assert any("Queued Toronto, Canada" in s.value for s in at.success)

def test_duplicate_submission_shows_existing_run():
    client = FakeClient(duplicate=True)
    at = app(client)
    at.button[0].click().run()
    assert any("already has an active run" in i.value for i in at.info)

def test_dashboard_shows_steps_review_citations_and_provenance():
    at = app(FakeClient())
    text = all_text(at)
    assert "> 1,050 ML/day in 2023" in text  # review log quotes the cited passage
    assert "[Annual Report](https://toronto.ca/report.pdf), p. 41" in text
    assert "Toronto Water operates" in text  # provenance citation for the selected field
    assert "**Status:** RESOLVED" in text
    assert [t.label for t in at.tabs] == ["📈 Progress & step outputs", "🧑‍⚖️ Human review log",
                                          "🧬 Field tracking & provenance"]
    assert any("Extract claims" in e.label for e in at.expander)

def test_city_without_runs_shows_hint():
    at = app(FakeClient())
    at.selectbox[0].set_value(1).run()  # Madrid has no runs
    assert "No runs yet for Madrid, Spain." in all_text(at)

def test_running_run_renders_and_can_be_cancelled():
    client = FakeClient(status="RUNNING")
    at = app(client)
    cancel = next(b for b in at.button if "Cancel" in b.label)
    cancel.click().run()
    assert client.cancelled == ["run-1"]

def test_unreachable_api_stops_with_error():
    at = app(FakeClient(healthy=False))
    assert any("API not reachable" in e.value for e in at.error)

class TwoRunClient(FakeClient):
    """Toronto has an older run with only UNRESOLVED fields and a newer one with CANDIDATE
    fields: the filters must not carry the first run's selection over to the second."""

    RUNS: ClassVar[dict[str, str]] = {"run-old": "UNRESOLVED", "run-new": "CANDIDATE"}

    def runs(self, city=None, country=None, limit=50):
        if city != "Toronto":
            return []
        return [{**self._run(), "run_id": rid, "created_at": f"2026-10-0{i}T10:00:00+00:00"}
                for i, rid in enumerate(self.RUNS, start=1)]

    def review(self, run_id):
        status = self.RUNS[run_id]
        code = "AWAITING_VALIDATION" if status == "CANDIDATE" else "NO_SUFFICIENT_EVIDENCE"
        items = [{"field_key": "water_type", "field_type": "source_type",
                  "field_status": "UNRESOLVED", "reason_code": "NO_SUFFICIENT_EVIDENCE",
                  "reason": "No sufficient evidence", "decision": None, "citations": []}]
        if status == "CANDIDATE":
            items.append({"field_key": "utility_name", "field_type": "utility",
                          "field_status": "CANDIDATE", "reason_code": code,
                          "reason": "Candidate claim awaiting reviewer validation",
                          "decision": None, "citations": []})
        return {"available": True, "items": items}

    def fields(self, run_id):
        base = super().fields(run_id)["fields"][0]
        rows = [{**base, "field_key": "water_type", "field_type": "source_type",
                 "status": "UNRESOLVED", "value": None, "citations": []}]
        if self.RUNS[run_id] == "CANDIDATE":
            rows.append({**base, "status": "CANDIDATE", "value": None, "citations": []})
        return {"available": True, "fields": rows}

def test_filters_reset_when_switching_runs():
    at = app(TwoRunClient())
    run_box = next(s for s in at.selectbox if s.label == "Run")
    run_box.set_value("run-old").run()  # first view: only UNRESOLVED / NO_SUFFICIENT_EVIDENCE
    run_box = next(s for s in at.selectbox if s.label == "Run")
    run_box.set_value("run-new").run()
    assert not at.exception, at.exception
    statuses = next(m for m in at.multiselect if m.label == "Status")
    reasons = next(m for m in at.multiselect if m.label == "Reason")
    assert statuses.value == ["CANDIDATE", "UNRESOLVED"]
    assert reasons.value == ["AWAITING_VALIDATION", "NO_SUFFICIENT_EVIDENCE"]
    shown = next(df.value for df in at.dataframe if "candidates" in df.value.columns)
    assert sorted(shown["field"]) == ["utility_name", "water_type"]
