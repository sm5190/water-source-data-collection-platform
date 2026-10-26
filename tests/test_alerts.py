"""Alert rules over stored records: each rule fires on its evidence and names it, stays quiet
without it, and can be switched off."""

from datetime import UTC, datetime, timedelta
import pytest
from procedure_helpers import make_engine
from sqlalchemy.orm import Session
from water_source_research.core.config import Settings
from water_source_research.db.observability_models import (
    ProviderUsageRecord, WorkerEventRecord, WorkerInstanceRecord,
)
from water_source_research.db.research_models import ResearchJobRecord, ResearchRunStepRecord
from water_source_research.observability import alerts

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None, observability={"sample_seconds": 30})
CONFIG = alerts.load_config()

def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)

def ev(kind, minutes, **kw):
    return WorkerEventRecord(event_id=f"{kind}-{minutes}-{kw.get('instance_id', '')}",
                             kind=kind, occurred_at=ago(minutes), source="supervisor",
                             facts=kw.pop("facts", {}), **kw)

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path, "alerts.db")
    yield eng
    eng.dispose()

def add(engine, *rows):
    with Session(engine) as s, s.begin():
        s.add_all(rows)

def run(engine, config=CONFIG, backends=()):
    return alerts.evaluate(engine, SETTINGS, now=NOW, config=config, backends=list(backends))

def rules(result):
    return [a["rule"] for a in result["alerts"]]

def test_quiet_system_has_no_alerts(engine):
    add(engine, WorkerInstanceRecord(instance_id="w1", last_heartbeat_at=ago(0.2),
                                     updated_at=NOW, last_sample={"rss_mb": 300}))
    assert run(engine)["alerts"] == []

def test_worker_crash_repeats_the_recorded_cause(engine):
    add(engine, ev("WORKER_EXITED_UNEXPECTEDLY", 10, instance_id="w1",
                   inferred_cause="UNKNOWN_TERMINATION", cause_confidence="UNKNOWN",
                   facts={"exit_code": 15, "exit_hex": "0x0000000F", "affected_runs": ["r1"]}))
    (a,) = run(engine)["alerts"]
    assert (a["rule"], a["severity"]) == ("worker_crash", "critical")
    assert "UNKNOWN_TERMINATION [UNKNOWN]" in a["detail"] and a["evidence"]["run_ids"] == ["r1"]

def test_old_crash_outside_the_window_is_ignored(engine):
    add(engine, ev("WORKER_EXITED_UNEXPECTEDLY", 60 * 30, instance_id="w1"))
    assert run(engine)["alerts"] == []

def test_heartbeat_missing_until_restored(engine):
    add(engine, ev("HEARTBEAT_MISSING", 5, instance_id="w1",
                   facts={"heartbeat_age_seconds": 95, "process_alive": True}),
        WorkerInstanceRecord(instance_id="w1", last_heartbeat_at=ago(0.1), updated_at=NOW))
    assert rules(run(engine)) == ["heartbeat_missing"]
    add(engine, ev("HEARTBEAT_RESTORED", 2, instance_id="w1"))
    assert run(engine)["alerts"] == []

def test_stale_worker_without_an_exit_record(engine):
    add(engine, WorkerInstanceRecord(instance_id="w2", last_heartbeat_at=ago(30),
                                     updated_at=NOW))
    (a,) = run(engine)["alerts"]
    assert a["rule"] == "heartbeat_missing" and "no recorded exit" in a["detail"]

def test_repeated_restarts_and_giving_up(engine):
    add(engine, *(ev("WORKER_RESTARTED", m, instance_id=f"w{m}") for m in (1, 2, 3)),
        ev("RESTART_FAILED", 0.5, facts={"reason": "restart limit reached"}))
    found = run(engine)["alerts"]
    assert [a["title"] for a in found] == ["Repeated worker restarts", "Worker restart failed"]

def test_stalled_jobs(engine):
    def job(run_id, lease_minutes_ago, started_ago):
        return ResearchJobRecord(run_id=run_id, city_name="Lima", country=None,
                                 city_key=run_id, status="RUNNING", priority=0, attempts=1,
                                 max_attempts=3, lease_owner="w1",
                                 lease_until=ago(lease_minutes_ago), started_at=ago(started_ago),
                                 created_at=ago(started_ago), updated_at=NOW)
    add(engine, job("expired", 2, 10), job("slow", -5, 120), job("fine", -5, 120),
        ResearchRunStepRecord(run_id="fine", attempt=1, seq=1, node="plan", duration_ms=10,
                              summary={}, finished_at=ago(3)))
    found = {a["evidence"]["run_ids"][0]: a["title"] for a in run(engine)["alerts"]}
    assert found == {"expired": "Research run stalled (lease expired)",
                     "slow": "Research run making no progress"}

def test_recovery_failed(engine):
    add(engine, ev("JOB_RECOVERY_FAILED", 3, run_id="r1",
                   facts={"reason": "lease expired on the final attempt", "will_retry": False}))
    (a,) = run(engine)["alerts"]
    assert a["rule"] == "recovery_failed" and "final attempt" in a["detail"]

def test_high_memory_only_for_live_workers(engine):
    add(engine, WorkerInstanceRecord(instance_id="hot", last_heartbeat_at=ago(0.1),
                                     updated_at=NOW, last_sample={"rss_mb": 5000,
                                                                  "commit_used_ratio": 0.97}),
        WorkerInstanceRecord(instance_id="gone", last_heartbeat_at=ago(0.1), updated_at=NOW,
                             stopped_at=ago(0.05), termination_class="CLEAN_SHUTDOWN",
                             last_sample={"rss_mb": 9000}))
    (a,) = run(engine)["alerts"]
    assert a["evidence"]["instance_id"] == "hot"
    assert "RSS 5,000 MB" in a["detail"] and "97%" in a["detail"]

def test_api_errors_from_the_ledger(engine):
    add(engine, *(ProviderUsageRecord(category="research", kind="LLM", provider="vt",
                                      requests=3, retries=2, cache_hit=False,
                                      attempt_errors={"rate_limited": 2},
                                      created_at=ago(10)) for _ in range(3)))
    found = run(engine)["alerts"]
    assert [a["evidence"]["metric"] for a in found] == ["rate_limited"]
    assert "6 attempts were rate limited" in found[0]["detail"]

def test_observability_backend_down_and_failing_exports(engine):
    add(engine, WorkerInstanceRecord(
        instance_id="w1", last_heartbeat_at=ago(0.1), updated_at=NOW,
        last_sample={"telemetry": {"exporter": "otlp", "exports_failed": 4,
                                   "last_failure_at": "2026-10-26T11:59:00+00:00",
                                   "last_success_at": None}}))
    found = run(engine, backends=[{"backend": "jaeger", "enabled": True, "status": "DOWN",
                                   "detail": "ConnectionRefusedError", "checked_at": None},
                                  {"backend": "langfuse", "enabled": False,
                                   "status": "DISABLED"}])["alerts"]
    assert [a["title"] for a in found] == ["jaeger unreachable", "Span export failing"]
    assert all("research continues" in a["detail"] for a in found)

def test_rules_can_be_switched_off(engine):
    add(engine, ev("WORKER_EXITED_UNEXPECTEDLY", 10, instance_id="w1"))
    off = {**CONFIG, "rules": {**CONFIG["rules"], "worker_crash": {"enabled": False}}}
    result = run(engine, config=off)
    assert result["alerts"] == [] and result["rules"]["worker_crash"] is False
