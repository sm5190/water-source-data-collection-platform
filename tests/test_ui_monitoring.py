"""Monitoring mode of the dashboard (AppTest + a fake API client): every view renders the
data it gets, Unknown is shown as Unknown (never 0), and a worker restart is never presented
as resumed research."""

import pytest

pytest.importorskip("streamlit")
from test_ui_app import FakeClient, all_text, app

def usage(invocations=3, input_tokens=1200, output_tokens=300, unknown=1, cost_status="UNKNOWN",
          cost_known=None, unknown_cost=3, **kw):
    return {"calls": invocations + 1, "invocations": invocations, "cache_hits": 1,
            "requests": invocations, "retries": 2, "failed_calls": 1,
            "attempt_errors": {"rate_limited": 2}, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cached_tokens": None,
            "unknown_token_calls": unknown, "latency_p50_ms": 1500.0, "latency_p95_ms": 4200.0,
            "cost_usd_known": cost_known, "unknown_cost_calls": unknown_cost,
            "cost_status": cost_status, "source": "ledger", **kw}

RUN = {"run_id": "r-lima-0001", "city_name": "Lima", "country": "Peru", "status": "SUCCEEDED",
       "completion": "FINALIZED", "mode": "RESEARCH", "attempts": 2, "last_error": None,
       "created_at": "2026-10-26T10:00:00+00:00", "started_at": None, "finished_at": None,
       "review_round": 1, "trace_requested": True}
CRASH = {"event_id": "e1", "kind": "WORKER_EXITED_UNEXPECTEDLY",
         "label": "Worker process ended unexpectedly", "track": "worker",
         "occurred_at": "2026-10-26T11:04:00+00:00", "instance_id": "host-dead", "run_id": None,
         "attempt": None, "trace_id": None,
         "facts": {"exit_code": 15, "exit_hex": "0x0000000F", "affected_runs": ["r-lima-0001",
                                                                               "r-oslo-0002"]},
         "inferred_cause": "UNKNOWN_TERMINATION", "cause_confidence": "UNKNOWN",
         "rationale": "exit 15 (0x0000000F) with no exception, crash dump, kill record or "
                      "memory signal in the evidence."}

class MonitoringClient(FakeClient):
    def __init__(self, alerts=None, **kw):
        super().__init__(**kw)
        self.alerts = alerts or []

    def mon_alerts(self):
        return {"alerts": self.alerts}

    def mon_overview(self, hours=24):
        return {"window_hours": hours,
                "runs": {"active": 1, "queued": 0, "running": 1, "awaiting_review": 7,
                         "succeeded_total": 17, "failed_total": 2,
                         "finished_in_window": {"SUCCEEDED": 3}},
                "durations": {"runs": 3, "median_wall_s": 5400, "median_active_s": 610,
                              "median_queue_wait_s": 4, "median_review_wait_s": None},
                "workers": {"total": 2, "ALIVE": 1, "EXITED": 1},
                "recent_errors": [{"run_id": "r4", "city": "Quito", "error": "boom",
                                   "at": "2026-10-26T09:00:00+00:00"}],
                "usage": usage(),
                "backends": [{"backend": "jaeger", "status": "UP", "detail": "HTTP 200"},
                             {"backend": "langfuse", "status": "DISABLED",
                              "detail": "not configured"}]}

    def mon_runs(self, limit=200):
        return [RUN]

    def mon_run(self, run_id):
        return {"run": RUN, "time": {"wall_s": 4800, "active_s": 610, "queue_wait_s": 4,
                                     "review_wait_s": 3600, "other_wait_s": 300,
                                     "attempts_with_steps": 2},
                "progress": [{"node": n, "done": n != "human_review"} for n in (
                    "plan", "discover", "acquire", "extract", "validate", "resolve",
                    "human_review", "assemble", "finalize")],
                "stages": [{"attempt": 1, "seq": 1, "node": "plan", "seconds": 0.1,
                            "finished_at": "2026-10-26T10:00:01+00:00"}],
                "stage_totals": {"extract": {"runs": 2, "seconds": 300.0}},
                "retries": 1,
                "pipeline": {"search_results": 40, "search_results_kept": 18,
                             "documents": {"OK": 9, "FAILED": 2}, "documents_by_round": {},
                             "chunks": 412, "extraction": {"water_source": {
                                 "attempts": 3, "claims": 5, "reextractions": 2,
                                 "statuses": {"READY": 3}, "rounds": [1, 2, 3]}},
                             "claims_by_validation": {"VALID": 4, "INVALID": 1},
                             "validation_failures": 1},
                "llm": usage(), "llm_by_field": [{"field": "water_source", **usage()}],
                "errors": [{"what": "llm vt:gpt-oss-120b water_source",
                            "error": "rate_limited"}],
                "traces": [{"kind": "RUN", "attempt": 1, "trace_id": "ab" * 16,
                            "sampled": True, "jaeger_url": "http://localhost:16686/trace/abab",
                            "langfuse_url": None}],
                "lifecycle": [{"occurred_at": "2026-10-26T11:04:00+00:00",
                               "label": "Research run interrupted by the worker's exit",
                               "inferred_cause": "UNKNOWN_TERMINATION"}]}

    def mon_workers(self):
        return [{"instance_id": "host-live", "pid": 1234, "status": "ALIVE",
                 "cpu_percent": 3.5, "rss_mb": 410.2, "private_mb": 380.0,
                 "heartbeat_age_s": 12, "active_runs": {"r-lima-0001": {"stage": "extract"}},
                 "termination_class": None},
                {"instance_id": "host-dead", "pid": 999, "status": "EXITED",
                 "cpu_percent": None, "rss_mb": None, "private_mb": None,
                 "heartbeat_age_s": 3600, "active_runs": {"r-oslo-0002": {"stage": "acquire"}},
                 "termination_class": "UNKNOWN_TERMINATION"}]

    def mon_crashes(self, hours=24 * 7):
        return [CRASH]

    def mon_recovery(self, hours=24 * 7):
        return [{"crash": CRASH, "worker_restart": "RESTARTED", "restart_event": None,
                 "runs": [{"run_id": "r-lima-0001", "current_status": "SUCCEEDED",
                           "research_outcome": "RESUMED", "steps": [
                               {"occurred_at": "2026-10-26T11:05:00+00:00",
                                "label": "Another worker reclaimed the run"}]},
                          {"run_id": "r-oslo-0002", "current_status": "RUNNING",
                           "research_outcome": "PENDING", "steps": []}]}]

    def mon_usage(self, hours=24, group_by="provider_model"):
        return {"window_hours": hours, "group_by": group_by,
                "total": usage(cost_status="PARTIAL", cost_known=0.25, unknown_cost=2),
                "groups": [{"group": "vt:gpt-oss-120b", **usage()},
                           {"group": "openai:gpt-x", **usage(cost_status="KNOWN",
                                                              cost_known=0.25,
                                                              unknown_cost=0, unknown=0)}]}

def monitoring(client=None):
    at = app(client or MonitoringClient())
    at.sidebar.radio(key="mode").set_value("Monitoring").run()
    assert not at.exception, at.exception
    return at

def metric(at, label):
    return next(m.value for m in at.metric if m.label == label)

def test_overview_shows_runs_workers_durations_usage_and_errors():
    at = monitoring()
    assert metric(at, "Awaiting review") == "7"
    assert metric(at, "Total time") == "1h 30m"
    assert metric(at, "Waiting for review") == "Unknown"  # no data, not 0s
    assert metric(at, "Input tokens") == "1,200 (+1 Unknown)"
    assert metric(at, "Estimated cost") == "Unknown"
    assert "No alerts" in all_text(at)
    assert "Finished in this period: 3 succeeded" in all_text(at)
    frames = [f.value for f in at.dataframe]
    assert any("boom" in str(df.values) for df in frames)
    assert any("DISABLED" in str(df.values) for df in frames)

def test_alerts_are_shown_with_severity():
    at = monitoring(MonitoringClient(alerts=[
        {"rule": "worker_crash", "severity": "critical", "title": "Worker crashed",
         "detail": "host-dead exited (exit 15)", "since": "2026-10-26T11:04:00+00:00",
         "evidence": {"event_ids": ["e1"]}},
        {"rule": "high_memory", "severity": "warning", "title": "High memory",
         "detail": "host-live uses 3,900 MB", "since": None, "evidence": {}}]))
    assert any("Worker crashed" in e.value for e in at.error)
    assert any("High memory" in w.value for w in at.warning)

def test_run_view_shows_progress_time_split_pipeline_calls_and_links():
    at = monitoring()
    text = all_text(at)
    assert metric(at, "Waiting after errors") == "5m 00s"
    assert metric(at, "Evidence chunks") == "412"
    assert metric(at, "Failed validation") == "1"
    assert "[Jaeger](http://localhost:16686/trace/abab)" in text
    assert any("rate_limited" in e.value for e in at.error)
    frames = [f.value for f in at.dataframe]
    assert any("water_source" in str(df.values) and 2 in df.values for df in frames)

def test_workers_and_crashes_with_unknown_values_and_evidence():
    at = monitoring()
    frames = [f.value for f in at.dataframe]
    workers = next(df for df in frames if "Worker" in df.columns)
    assert list(workers["Status"]) == ["ALIVE", "EXITED"]
    assert workers.iloc[1]["CPU %"] == "Unknown"
    assert workers.iloc[0]["Runs (last heartbeat)"] == "r-lima-0 (extract)"
    assert workers.iloc[1]["Runs (last heartbeat)"] == "was running r-oslo-0 (acquire)"
    assert any("exit 15 (0x0000000F) · UNKNOWN_TERMINATION [UNKNOWN]" in e.label
               for e in at.expander)

def test_restart_is_not_presented_as_resumed_research():
    text = all_text(monitoring())
    assert "🔄 Worker restarted (a new worker process started)" in text
    assert "Run r-lima-0: ✅ Research resumed from its checkpoint" in text
    assert "Run r-oslo-0: ⏳ Research not resumed yet" in text

def test_usage_view_marks_unknown_and_partial_costs():
    at = monitoring()
    assert any("Cost is Unknown for 2 model call(s)" in i.value for i in at.info)
    frames = [f.value for f in at.dataframe]
    table = next(df for df in frames if "Estimated cost" in df.columns
                 and "Provider & model" in df.columns)
    assert list(table["Estimated cost"]) == ["Unknown", "$0.2500"]
    assert "$0.2500 + Unknown for 2 call(s)" in [m.value for m in at.metric]

def test_empty_monitoring_states():
    class Empty(MonitoringClient):
        def mon_runs(self, limit=200):
            return []

        def mon_workers(self):
            return []

        def mon_crashes(self, hours=24 * 7):
            return []

        def mon_recovery(self, hours=24 * 7):
            return []
    text = all_text(monitoring(Empty()))
    assert "No research runs yet." in text and "No worker has reported yet." in text
    assert "No recovery incidents" in text
