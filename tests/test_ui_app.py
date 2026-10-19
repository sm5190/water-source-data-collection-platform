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
        self.decisions = []
        self.resumed = []

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
                "cancel_requested": False, "review_round": 1}

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

    # --- human review ---

    def review_pending(self, run_id):
        return {"round": 1, "items": [REVIEW_ITEM]}

    def review_history(self, run_id):
        return {"items": [], "decisions": [], "applied": []}

    def submit_decisions(self, run_id, reviewer, decisions):
        self.decisions.append((reviewer, decisions))
        return {"round": 1, "decisions": []}

    def resume_review(self, run_id, reviewer):
        self.resumed.append((run_id, reviewer))
        return {}

W = "total_city_water_withdrawals_m3_day"
REVIEW_ITEM = {
    "id": "item-1", "round": 1, "field_key": W, "entity_key": "city", "label": None,
    "reason_codes": ["VALUE_IN_QUOTE"], "status": "OPEN", "decision": None,
    "payload": {
        "field": {"key": W, "type": "number"}, "entity": {"key": "city", "label": None},
        "field_status": "HITL_REQUIRED", "reason_codes": ["VALUE_IN_QUOTE"],
        "sources": [{"key": "lake ontario", "label": "Lake Ontario"}],
        "allowed_decisions": ["APPROVE", "REJECT", "CORRECT", "MARK_UNKNOWN", "MARK_NA",
                              "DEFER"],
        "candidates": [{
            "claim_uid": "c1", "value": 450000, "normalized_value": 450000,
            "value_status": "KNOWN", "value_origin": "REPORTED", "confidence": 0.99,
            "entity_key": None, "review_status": None,
            "validation": {"outcome": "INVALID", "checks": [
                {"code": "VALUE_IN_QUOTE", "result": "FAIL",
                 "detail": "450000 does not match the quote's numbers: 980000"}]},
            "qualifiers": {"reference_year": 2023, "source_types": ["GOVERNMENT"]},
            "citations": [{"url": "https://toronto.ca/report.pdf", "title": "Annual Report",
                           "page_number": 2, "chunk_id": "ch-9", "source_type": "GOVERNMENT",
                           "source_date": "2024-03-15",
                           "quote": "produced an average of 980,000 cubic metres per day"}]}],
    },
}

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
    assert [t.label for t in at.tabs] == ["📈 Progress & step outputs", "✍️ Review queue",
                                          "🧑‍⚖️ Human review log",
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

# --- review queue -----------------------------------------------------------------------------

def test_waiting_run_shows_candidates_side_by_side_with_evidence():
    at = app(FakeClient(status="AWAITING_REVIEW"))
    text = all_text(at)
    assert any("waiting for human review" in w.value for w in at.warning)
    assert "**Review round 1** · 1 item(s) · 0 decided" in text
    assert "> produced an average of 980,000 cubic metres per day" in text
    assert "VALUE_IN_QUOTE: 450000 does not match the quote's numbers: 980000" in text
    assert any(b.label == "Open source" for b in at.get("link_button"))
    # no reviewer yet: deciding and resuming are disabled
    resume = next(b for b in at.button if "resume" in b.label)
    assert resume.disabled

def test_reviewer_corrects_a_value_with_a_chunk_quote_and_resumes():
    client = FakeClient(status="AWAITING_REVIEW")
    at = app(client)
    at.text_input(key="reviewer").set_value("analyst@example.org").run()
    at.radio(key="kind:item-1").set_value("CORRECT")
    at.text_input(key="value:item-1").set_value("980000")
    at.text_area(key="reason:item-1").set_value("the quote says 980,000")
    at.selectbox(key="chunk:item-1").set_value("ch-9")
    at.text_area(key="quote:item-1").set_value("980,000 cubic metres")
    at.multiselect(key="claims:item-1").set_value(["c1"])
    next(b for b in at.button if b.label == "Save decision").click().run()
    assert not at.exception, at.exception
    [(reviewer, [body])] = client.decisions
    assert reviewer == "analyst@example.org"
    assert body["decision"] == "CORRECT" and body["value"] == 980000
    assert body["evidence"] == {"chunk_id": "ch-9", "quote": "980,000 cubic metres"}
    assert body["claim_uids"] == ["c1"] and body["item_id"] == "item-1"
    assert len(body["decision_key"]) == 32

    REVIEW_ITEM["decision"] = {"decision": "CORRECT", "reviewer": "analyst@example.org",
                               "reason": "the quote says 980,000"}
    try:
        at.run()
        next(b for b in at.button if "resume" in b.label).click().run()
        assert client.resumed == [("run-1", "analyst@example.org")]
    finally:
        REVIEW_ITEM["decision"] = None
