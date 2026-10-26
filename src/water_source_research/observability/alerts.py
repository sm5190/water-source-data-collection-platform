"""Monitoring alerts, computed on read from stored records (no alert table, no notifications).

Rules and thresholds are in ``config/alerts.yaml``. Each alert names its evidence (event ids,
run ids, worker ids) so it can be checked; an alert is a pointer to recorded facts, never a
new conclusion (e.g. a crash alert repeats the supervisor's evidence-based cause and
confidence, it doesn't add one).
"""

from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
import yaml
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from water_source_research.core.paths import CONFIG_DIR
from water_source_research.db.observability_models import ProviderUsageRecord, WorkerEventRecord
from water_source_research.db.research_models import ResearchJobRecord, ResearchRunStepRecord
from . import health, monitoring

ALERTS_FILE = CONFIG_DIR / "alerts.yaml"
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

@lru_cache
def load_config() -> dict[str, Any]:
    if not ALERTS_FILE.exists():
        return {"window_hours": 24, "rules": {}}
    return yaml.safe_load(ALERTS_FILE.read_text(encoding="utf-8")) or {}

def evaluate(engine: Engine, settings: Any = None, *, now: datetime | None = None,
             config: dict[str, Any] | None = None,
             backends: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if settings is None:
        from water_source_research.core.config import get_settings
        settings = get_settings()
    config = config or load_config()
    rules = config.get("rules") or {}
    now = now or monitoring.now_utc()
    since = now - timedelta(hours=float(config.get("window_hours", 24)))
    alerts: list[dict[str, Any]] = []

    def on(rule: str) -> dict[str, Any] | None:
        r = rules.get(rule) or {}
        return r if r.get("enabled", False) else None

    def add(rule: str, title: str, detail: str, since_at: Any = None, **evidence: Any) -> None:
        alerts.append({"rule": rule, "severity": (rules.get(rule) or {}).get("severity",
                                                                            "warning"),
                       "title": title, "detail": detail,
                       "since": since_at.isoformat() if isinstance(since_at, datetime)
                       else since_at, "evidence": evidence})

    with Session(engine) as session:
        events = list(session.scalars(
            select(WorkerEventRecord).where(WorkerEventRecord.occurred_at >= since)
            .order_by(WorkerEventRecord.occurred_at)))
        running = session.execute(
            select(ResearchJobRecord.run_id, ResearchJobRecord.city_name,
                   ResearchJobRecord.lease_until, ResearchJobRecord.lease_owner,
                   ResearchJobRecord.started_at)
            .where(ResearchJobRecord.status == "RUNNING")).all()
        last_step = dict(session.execute(
            select(ResearchRunStepRecord.run_id, func.max(ResearchRunStepRecord.finished_at))
            .where(ResearchRunStepRecord.run_id.in_([r.run_id for r in running]))
            .group_by(ResearchRunStepRecord.run_id)).all()) if running else {}
        ledger = list(session.scalars(select(ProviderUsageRecord)
                                      .where(ProviderUsageRecord.created_at >= since)))
    ws = monitoring.workers(engine, settings, now)

    if on("worker_crash"):
        for e in events:
            if e.kind == "WORKER_EXITED_UNEXPECTEDLY":
                f = e.facts or {}
                add("worker_crash", "Worker crashed",
                    f"{e.instance_id} ended with exit {f.get('exit_code')} "
                    f"({f.get('exit_hex')}): {e.inferred_cause} [{e.cause_confidence}]"
                    + (f"; runs interrupted: {len(f.get('affected_runs') or [])}"
                       if f.get("affected_runs") else ""),
                    e.occurred_at, event_ids=[e.event_id], instance_id=e.instance_id,
                    run_ids=f.get("affected_runs") or [])

    if on("heartbeat_missing"):
        flagged = set()
        latest: dict[str, Any] = {}
        for e in events:
            if e.kind in ("HEARTBEAT_MISSING", "HEARTBEAT_RESTORED", "WORKER_STOPPED",
                          "WORKER_EXITED_UNEXPECTEDLY"):
                latest[e.instance_id] = e
        for inst, e in latest.items():
            if e.kind == "HEARTBEAT_MISSING":
                flagged.add(inst)
                add("heartbeat_missing", "Worker heartbeat missing",
                    f"{inst}: no heartbeat for {(e.facts or {}).get('heartbeat_age_seconds')} s "
                    f"(process alive: {(e.facts or {}).get('process_alive')})",
                    e.occurred_at, event_ids=[e.event_id], instance_id=inst)
        for w in ws:
            if w["status"] == "STALE" and w["instance_id"] not in flagged:
                add("heartbeat_missing", "Worker heartbeat missing",
                    f"{w['instance_id']}: last heartbeat "
                    + (f"{round(w['heartbeat_age_s'])} s ago" if w["heartbeat_age_s"]
                       is not None else "never recorded")
                    + " and no recorded exit", w.get("last_heartbeat_at"),
                    instance_id=w["instance_id"])

    rule = on("repeated_restarts")
    if rule:
        restarts = [e for e in events if e.kind == "WORKER_RESTARTED"]
        if len(restarts) >= int(rule.get("restarts", 3)):
            add("repeated_restarts", "Repeated worker restarts",
                f"{len(restarts)} restarts in the last {config.get('window_hours', 24)} h",
                restarts[0].occurred_at, event_ids=[e.event_id for e in restarts])
        for e in events:
            if e.kind == "RESTART_FAILED":
                add("repeated_restarts", "Worker restart failed",
                    str((e.facts or {}).get("reason")), e.occurred_at, event_ids=[e.event_id])

    rule = on("stalled_job")
    if rule:
        limit = timedelta(minutes=float(rule.get("no_progress_minutes", 30)))
        for r in running:
            lease = monitoring._aware(r.lease_until)
            progress = monitoring._aware(last_step.get(r.run_id)) or \
                monitoring._aware(r.started_at)
            if lease is not None and lease < now:
                add("stalled_job", "Research run stalled (lease expired)",
                    f"{r.city_name} ({r.run_id[:8]}): lease of {r.lease_owner} expired at "
                    f"{lease:%H:%M:%S}; waiting for another worker to reclaim it", lease,
                    run_ids=[r.run_id], instance_id=r.lease_owner)
            elif progress is not None and now - progress > limit:
                add("stalled_job", "Research run making no progress",
                    f"{r.city_name} ({r.run_id[:8]}): no completed step for "
                    f"{int((now - progress).total_seconds() // 60)} min", progress,
                    run_ids=[r.run_id], instance_id=r.lease_owner)

    if on("recovery_failed"):
        for e in events:
            if e.kind == "JOB_RECOVERY_FAILED":
                add("recovery_failed", "Research recovery failed",
                    f"run {(e.run_id or '')[:8]}: {(e.facts or {}).get('reason') or (e.facts or {}).get('error')}"
                    + (" (will retry)" if (e.facts or {}).get("will_retry") else ""),
                    e.occurred_at, event_ids=[e.event_id], run_ids=[e.run_id])

    rule = on("high_memory")
    if rule:
        for w in ws:
            if w["status"] != "ALIVE":
                continue
            reasons = []
            if w.get("rss_mb") is not None and w["rss_mb"] > rule.get("rss_mb", 4000):
                reasons.append(f"RSS {w['rss_mb']:,.0f} MB")
            if w.get("private_mb") is not None and w["private_mb"] > rule.get("private_mb",
                                                                              4000):
                reasons.append(f"private {w['private_mb']:,.0f} MB")
            ratio = w.get("commit_used_ratio")
            if ratio is not None and ratio > rule.get("commit_ratio", 0.95):
                reasons.append(f"system commit at {ratio:.0%} of its limit")
            if reasons:
                add("high_memory", "High memory", f"{w['instance_id']}: " + ", ".join(reasons),
                    w.get("last_heartbeat_at"), instance_id=w["instance_id"])

    rule = on("api_errors")
    if rule and ledger:
        attempts: Counter[str] = Counter()
        for r in ledger:
            attempts.update(r.attempt_errors or {})
        retries = sum(r.retries or 0 for r in ledger)
        failed = sum(1 for r in ledger if r.error)
        checks = [("timeouts", attempts.get("timeout", 0), "attempts timed out"),
                  ("rate_limited", attempts.get("rate_limited", 0),
                   "attempts were rate limited (HTTP 429)"),
                  ("retries", retries, "requests were retried"),
                  ("failed_calls", failed, "model calls failed after all retries")]
        for key, value, text in checks:
            if value >= int(rule.get(key, 10**9)):
                add("api_errors", "Model API problems", f"{value} {text} in the last "
                    f"{config.get('window_hours', 24)} h", metric=key, value=value)

    if on("observability_backend"):
        for b in backends if backends is not None else health.backends(settings):
            if b.get("enabled") and b.get("status") == "DOWN":
                add("observability_backend", f"{b['backend']} unreachable",
                    f"{b.get('detail')} (research continues; spans for it are dropped)",
                    b.get("checked_at"), backend=b["backend"])
        for w in ws:
            t = w.get("telemetry") or {}
            if w["status"] == "ALIVE" and t.get("exports_failed") and (
                    t.get("last_success_at") is None
                    or (t.get("last_failure_at") or "") > t["last_success_at"]):
                add("observability_backend", "Span export failing",
                    f"{w['instance_id']}: {t['exports_failed']} failed export(s) to "
                    f"{t.get('exporter')} (research continues)", t.get("last_failure_at"),
                    instance_id=w["instance_id"])

    alerts.sort(key=lambda a: (SEVERITY_ORDER.get(a["severity"], 9), a["since"] or ""))
    return {"evaluated_at": now.isoformat(), "window_hours": config.get("window_hours", 24),
            "alerts": alerts,
            "rules": {k: bool((v or {}).get("enabled")) for k, v in rules.items()}}
