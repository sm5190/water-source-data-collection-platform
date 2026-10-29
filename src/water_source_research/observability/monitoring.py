"""Monitoring numbers, computed on read from what the system already stores (no metrics
database). Every function only reads.

Sources: ``research_jobs`` (status, created/started/finished), ``research_run_steps`` (stage
durations and timestamps), ``discovery_results`` / ``source_documents`` / ``evidence_chunks``
/ ``field_claims`` (pipeline), the run's trusted events (extraction), ``worker_instances`` /
``worker_events`` (workers, crashes, recovery), ``run_traces`` (trace links) and
``provider_usage`` (API usage and cost).

Unknown is ``None``, never 0: tokens a provider didn't report, cost without a configured
price, durations of runs without step records. API usage comes from the ledger only; runs
from before the ledger fall back to their extraction events, labelled
``source="events (extraction only)"`` — the two sources are never added together.

Time split of a run: active = sum of step durations; queue wait = first attempt start −
submission; review wait = gaps after an attempt that paused on human review; other wait =
the remaining gaps between attempts (retry backoff, crash and lease expiry).
"""

import statistics
from itertools import pairwise
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import (
    DiscoveryResultRecord, EvidenceChunkRecord, FieldClaimRecord, SourceDocumentRecord,
)
from water_source_research.db.observability_models import (
    ProviderUsageRecord, RunTraceRecord, WorkerEventRecord, WorkerInstanceRecord,
)
from water_source_research.db.research_models import (
    ResearchJobRecord, ResearchRunStepRecord, RunEventRecord,
)

J, S, U, E, W = (ResearchJobRecord, ResearchRunStepRecord, ProviderUsageRecord,
                 WorkerEventRecord, WorkerInstanceRecord)
NODE_ORDER = ("plan", "discover", "acquire", "extract", "validate", "resolve", "human_review",
              "assemble", "finalize")
ACTIVE = ("QUEUED", "RUNNING")
RECOVERY_KINDS = ("WORKER_EXITED_UNEXPECTEDLY", "JOB_INTERRUPTED", "RESTART_SCHEDULED",
                  "WORKER_RESTARTED", "RESTART_FAILED", "JOB_LEASE_EXPIRED", "JOB_RECLAIMED",
                  "CHECKPOINT_RECOVERY_ATTEMPTED", "JOB_RESUMED", "JOB_RECOVERY_FAILED")
WORKER_TRACK = {"WORKER_EXITED_UNEXPECTEDLY", "RESTART_SCHEDULED", "WORKER_RESTARTED",
                "RESTART_FAILED"}
LABELS = {
    "WORKER_EXITED_UNEXPECTEDLY": "Worker process ended unexpectedly",
    "JOB_INTERRUPTED": "Research run interrupted by the worker's exit",
    "RESTART_SCHEDULED": "Worker restart scheduled",
    "WORKER_RESTARTED": "Worker restarted (a new process; research not yet resumed)",
    "RESTART_FAILED": "Worker restart failed / supervisor gave up",
    "JOB_LEASE_EXPIRED": "The interrupted run's lease expired",
    "JOB_RECLAIMED": "Another worker reclaimed the run",
    "CHECKPOINT_RECOVERY_ATTEMPTED": "Resuming the run from its checkpoint",
    "JOB_RESUMED": "Research successfully resumed and completed its step",
    "JOB_RECOVERY_FAILED": "Research recovery failed",
}

def now_utc() -> datetime:
    return datetime.now(UTC)

def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)

def _iso(dt: datetime | None) -> str | None:
    return _aware(dt).isoformat() if dt else None

def _seconds(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return round((_aware(b) - _aware(a)).total_seconds(), 1)

# --- time split -------------------------------------------------------------------------

def time_split(created: datetime | None, finished: datetime | None, status: str,
               steps: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or now_utc()
    end = _aware(finished) or now
    out: dict[str, Any] = {"wall_s": _seconds(created, end), "active_s": None,
                           "queue_wait_s": None, "review_wait_s": None, "other_wait_s": None,
                           "attempts_with_steps": 0}
    if not steps or created is None:
        return out
    attempts: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for s in steps:
        attempts[s["attempt"]].append(s)
    spans = []
    for n in sorted(attempts):
        rows = sorted(attempts[n], key=lambda r: r["seq"])
        start = min(_aware(r["finished_at"]) - timedelta(milliseconds=r["duration_ms"])
                    for r in rows)
        stop = max(_aware(r["finished_at"]) for r in rows)
        spans.append((start, stop, rows[-1]["node"]))
    out["attempts_with_steps"] = len(spans)
    out["active_s"] = round(sum(s["duration_ms"] for s in steps) / 1000, 1)
    out["queue_wait_s"] = max(0.0, _seconds(created, spans[0][0]) or 0.0)
    review = other = 0.0
    for (_, prev_stop, prev_node), (nxt_start, _, _) in pairwise(spans):
        gap = max(0.0, (nxt_start - prev_stop).total_seconds())
        if prev_node == "human_review":
            review += gap
        else:
            other += gap
    if status == "AWAITING_REVIEW" and spans[-1][2] == "human_review":
        review += max(0.0, (now - spans[-1][1]).total_seconds())
    out["review_wait_s"], out["other_wait_s"] = round(review, 1), round(other, 1)
    return out

def _steps(session: Session, run_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    ids = list(run_ids)
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not ids:
        return out
    rows = session.execute(select(S.run_id, S.attempt, S.seq, S.node, S.duration_ms,
                                  S.finished_at).where(S.run_id.in_(ids))).all()
    for r in rows:
        out[r.run_id].append({"attempt": r.attempt, "seq": r.seq, "node": r.node,
                              "duration_ms": r.duration_ms, "finished_at": r.finished_at})
    return out

# --- usage ------------------------------------------------------------------------------

def _usage_rows(session: Session, since: datetime | None = None,
                run_ids: Iterable[str] | None = None) -> list[Any]:
    q = select(U)
    if since is not None:
        q = q.where(U.created_at >= since)
    if run_ids is not None:
        q = q.where(U.run_id.in_(list(run_ids)))
    return list(session.scalars(q))

def summarize_usage(rows: Iterable[Any],
                    avoided: Mapping[str, int] | None = None) -> dict[str, Any]:
    """``avoided``: persistent cache key -> tokens its original call spent (what a hit on it
    avoided). Avoided tokens are reported apart and never counted as spent."""
    rows = list(rows)
    hits = [r for r in rows if r.cache_hit]
    persistent = [r for r in hits if getattr(r, "cache_layer", None) == "persistent"]
    known_avoided = [avoided[r.cache_source] for r in persistent
                     if avoided and r.cache_source in avoided]
    invocations = [r for r in rows if not r.cache_hit and (r.requests or 0) > 0]
    known_tokens = [r for r in invocations if r.input_tokens is not None
                    and r.output_tokens is not None]
    latencies = sorted(r.latency_ms for r in invocations if r.latency_ms is not None)
    priced = [r for r in invocations if r.cost_usd is not None]
    attempt_errors: Counter[str] = Counter()
    for r in rows:
        attempt_errors.update(r.attempt_errors or {})
    unknown_cost = len(invocations) - len(priced)
    return {
        "calls": len(rows), "invocations": len(invocations),
        "cache_hits": len(hits),
        "persistent_cache_hits": len(persistent),
        "memory_cache_hits": len(hits) - len(persistent),
        "tokens_avoided": sum(known_avoided),
        "avoided_unknown_hits": len(hits) - len(known_avoided),
        "requests": sum(r.requests or 0 for r in rows),
        "retries": sum(r.retries or 0 for r in rows),
        "failed_calls": sum(1 for r in rows if r.error),
        "attempt_errors": dict(attempt_errors),
        "input_tokens": sum(r.input_tokens for r in known_tokens) if known_tokens else None,
        "output_tokens": sum(r.output_tokens for r in known_tokens) if known_tokens else None,
        "cached_tokens": (sum(r.cached_tokens for r in known_tokens if r.cached_tokens)
                          if any(r.cached_tokens is not None for r in known_tokens) else None),
        "unknown_token_calls": len(invocations) - len(known_tokens),
        "latency_p50_ms": _pct(latencies, 50), "latency_p95_ms": _pct(latencies, 95),
        "cost_usd_known": round(sum(r.cost_usd for r in priced), 6) if priced else None,
        "unknown_cost_calls": unknown_cost,
        "cost_status": ("NO_CALLS" if not invocations else "UNKNOWN" if not priced
                        else "PARTIAL" if unknown_cost else "KNOWN"),
        "source": "ledger",
    }

def _pct(values: list[int], p: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    return float(statistics.quantiles(values, n=100, method="inclusive")[p - 1])

def events_usage(events: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Extraction usage from a run's trusted NODE events (runs before the ledger)."""
    extract = [e for e in events if e.get("kind") == "NODE" and e.get("node") == "extract"
               and (e.get("attrs") or {}).get("llm_requests") is not None]
    if not extract:
        return None
    a = [e["attrs"] for e in extract]
    unknown = sum(x.get("usage_unknown_calls", 0) for x in a)
    return {"calls": len(a), "invocations": sum(1 for x in a if x.get("llm_requests")),
            "requests": sum(x.get("llm_requests", 0) for x in a),
            "cache_hits": sum(x.get("cache_hits", 0) for x in a),
            "input_tokens": sum(x.get("prompt_tokens", 0) for x in a),
            "output_tokens": sum(x.get("completion_tokens", 0) for x in a),
            "unknown_token_calls": unknown, "cost_usd_known": None,
            "cost_status": "UNKNOWN", "models": sorted({str(x.get("model")) for x in a}),
            "source": "events (extraction only)"}

def usage(engine: Engine, *, hours: float | None = 24, group_by: str = "provider_model",
          now: datetime | None = None) -> dict[str, Any]:
    """Usage and cost from the ledger, grouped by provider_model | city | run | category."""
    now = now or now_utc()
    since = now - timedelta(hours=hours) if hours else None
    with Session(engine) as session:
        rows = _usage_rows(session, since)
        avoided = _avoided(session, rows)
        cities = dict(session.execute(select(J.run_id, J.city_name)).all()) \
            if group_by in ("city", "run") else {}
    groups: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        if group_by == "city":
            key = cities.get(r.run_id) or "(no run)"
        elif group_by == "run":
            key = r.run_id or "(no run)"
        elif group_by == "category":
            key = r.category
        else:
            key = f"{r.provider}:{r.model or '?'}"
        groups[key].append(r)
    out = [{"group": k, **({"city": cities.get(k)} if group_by == "run" else {}),
            **summarize_usage(v, avoided)} for k, v in sorted(groups.items())]
    return {"window_hours": hours, "group_by": group_by,
            "total": summarize_usage(rows, avoided), "groups": out}

def _avoided(session: Session, rows: list[Any]) -> dict[str, int]:
    """Tokens each reused persistent cache entry's original call spent."""
    from water_source_research.db.observability_models import LLMResponseCacheRecord as C
    keys = {r.cache_source for r in rows if getattr(r, "cache_source", None)}
    if not keys:
        return {}
    return {k: i + (o or 0) for k, i, o in session.execute(
        select(C.cache_key, C.input_tokens, C.output_tokens).where(C.cache_key.in_(keys)))
        if i is not None}

# --- runs -------------------------------------------------------------------------------

def _job_row(r: Any) -> dict[str, Any]:
    return {"run_id": r.run_id, "city_name": r.city_name, "country": r.country,
            "status": r.status, "completion": r.completion, "mode": r.mode,
            "attempts": r.attempts, "last_error": r.last_error,
            "created_at": _iso(r.created_at), "started_at": _iso(r.started_at),
            "finished_at": _iso(r.finished_at), "review_round": r.review_round,
            "trace_requested": bool(r.trace_requested)}

JOB_COLUMNS = (J.run_id, J.city_name, J.country, J.status, J.completion, J.mode, J.attempts,
               J.last_error, J.created_at, J.started_at, J.finished_at, J.review_round,
               J.trace_requested, J.updated_at)

# Revalidation runs (services/revalidate.py) re-check stored claims; they are not research
# runs, so the run lists and counts leave them out unless asked for.
NOT_REVALIDATION = or_(J.mode.is_(None), J.mode != "REVALIDATE")

def runs(engine: Engine, *, limit: int = 200, now: datetime | None = None,
         include_revalidation: bool = False) -> list[dict]:
    with Session(engine) as session:
        q = select(*JOB_COLUMNS).order_by(J.created_at.desc()).limit(limit)
        if not include_revalidation:
            q = q.where(NOT_REVALIDATION)
        jobs = session.execute(q).all()
        steps = _steps(session, (j.run_id for j in jobs))
    return [{**_job_row(j), **time_split(j.created_at, j.finished_at, j.status,
                                         steps.get(j.run_id, []), now)} for j in jobs]

def run_detail(engine: Engine, run_id: str, settings: Any = None,
               now: datetime | None = None) -> dict[str, Any] | None:
    if settings is None:
        from water_source_research.core.config import get_settings
        settings = get_settings()
    obs = settings.observability
    with Session(engine) as session:
        job = session.execute(select(*JOB_COLUMNS, J.result).where(J.run_id == run_id)
                              ).one_or_none()
        if job is None:
            return None
        steps = _steps(session, [run_id])[run_id]
        step_rows = session.execute(select(S.attempt, S.seq, S.node, S.duration_ms,
                                           S.finished_at, S.summary)
                                    .where(S.run_id == run_id)
                                    .order_by(S.attempt, S.seq)).all()
        docs = Counter(s for (s,) in session.execute(
            select(SourceDocumentRecord.status).where(SourceDocumentRecord.run_id == run_id)))
        rounds = Counter(r for (r,) in session.execute(
            select(SourceDocumentRecord.round).where(SourceDocumentRecord.run_id == run_id)))
        chunks = session.scalar(select(func.count()).select_from(EvidenceChunkRecord)
                                .where(EvidenceChunkRecord.run_id == run_id)) or 0
        D = DiscoveryResultRecord
        found = (session.scalar(select(func.count()).select_from(D).where(D.run_id == run_id))
                 or 0,
                 session.scalar(select(func.count()).select_from(D)
                                .where(D.run_id == run_id, D.retained.is_(True))) or 0)
        validation = Counter(v or "UNVALIDATED" for (v,) in session.execute(
            select(FieldClaimRecord.validation_outcome)
            .where(FieldClaimRecord.run_id == run_id)))
        ledger = _usage_rows(session, run_ids=[run_id])
        traces = session.scalars(select(RunTraceRecord).where(RunTraceRecord.run_id == run_id)
                                 .order_by(RunTraceRecord.created_at)).all()
        lifecycle = session.scalars(select(E).where(E.run_id == run_id)
                                    .order_by(E.occurred_at)).all()
        stored_events = [] if (job.result or {}).get("events") else [
            {"kind": e.kind, "node": e.node, "field_key": e.field_key, "attrs": e.attrs}
            for e in session.scalars(select(RunEventRecord)
                                     .where(RunEventRecord.run_id == run_id))]
    events = (job.result or {}).get("events") or stored_events or [
        e for r in step_rows for e in ((r.summary or {}).get("events") or [])]
    extract = [e for e in events if e.get("kind") == "NODE" and e.get("node") == "extract"]
    per_field: dict[str, dict[str, Any]] = {}
    for e in extract:
        a = e.get("attrs") or {}
        f = per_field.setdefault(e.get("field_key") or "?", {
            "attempts": 0, "claims": 0, "reextractions": 0, "statuses": Counter(),
            "rounds": []})
        f["attempts"] += 1
        f["claims"] += a.get("claims", 0) or 0
        f["reextractions"] += 1 if a.get("reextraction") else 0
        f["statuses"][a.get("bundle_status", "?")] += 1
        f["rounds"].append(a.get("discovery_round"))
    stage_totals: dict[str, dict[str, Any]] = {}
    for s in steps:
        t = stage_totals.setdefault(s["node"], {"runs": 0, "seconds": 0.0})
        t["runs"] += 1
        t["seconds"] = round(t["seconds"] + s["duration_ms"] / 1000, 2)
    seen_nodes = {s["node"] for s in steps}
    ui = obs.trace_ui_url.rstrip("/")
    lf_ui = obs.langfuse_ui_url.rstrip("/")
    return {
        "run": _job_row(job),
        "time": time_split(job.created_at, job.finished_at, job.status, steps, now),
        "progress": [{"node": n, "done": n in seen_nodes} for n in NODE_ORDER],
        "stages": [{"attempt": r.attempt, "seq": r.seq, "node": r.node,
                    "seconds": round(r.duration_ms / 1000, 2), "finished_at": _iso(r.finished_at)}
                   for r in step_rows],
        "stage_totals": stage_totals,
        "retries": max(0, (job.attempts or 0) - 1),
        "pipeline": {
            "search_results": found[0], "search_results_kept": found[1],
            "documents": dict(docs), "documents_by_round": dict(sorted(rounds.items())),
            "chunks": chunks,
            "extraction": {k: {**v, "statuses": dict(v["statuses"])}
                           for k, v in sorted(per_field.items())},
            "claims_by_validation": dict(validation),
            "validation_failures": validation.get("INVALID", 0)},
        "llm": summarize_usage(ledger) if ledger else events_usage(events),
        "llm_by_field": _by_field(ledger),
        "errors": [x for x in (
            {"what": "run", "error": job.last_error} if job.last_error else None,
            *({"what": f"llm {r.provider}:{r.model} {r.field_key or ''}".strip(),
               "error": r.error, "at": _iso(r.created_at)} for r in ledger if r.error),
            *({"what": e.kind, "error": e.inferred_cause or (e.facts or {}).get("error"),
               "at": _iso(e.occurred_at)} for e in lifecycle
              if e.kind in ("JOB_INTERRUPTED", "JOB_RECOVERY_FAILED")),
        ) if x],
        "traces": [{"kind": t.kind, "attempt": t.attempt, "trace_id": t.trace_id,
                    "sampled": t.sampled, "created_at": _iso(t.created_at),
                    "jaeger_url": f"{ui}/trace/{t.trace_id}" if t.sampled else None,
                    "langfuse_url": (f"{lf_ui}/traces/{t.trace_id}"
                                     if t.sampled and lf_ui and obs.langfuse_enabled else None)}
                   for t in traces],
        "lifecycle": [_event(e) for e in lifecycle],
    }

def _by_field(rows: list[Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        groups[r.field_key or r.task or "?"].append(r)
    return [{"field": k, **summarize_usage(v)} for k, v in sorted(groups.items())]

# --- workers, crashes, recovery ---------------------------------------------------------

def _event(e: Any) -> dict[str, Any]:
    return {"event_id": e.event_id, "kind": e.kind, "label": LABELS.get(e.kind, e.kind),
            "track": "worker" if e.kind in WORKER_TRACK else "research",
            "occurred_at": _iso(e.occurred_at), "instance_id": e.instance_id,
            "run_id": e.run_id, "attempt": e.attempt, "trace_id": e.trace_id,
            "facts": e.facts or {}, "inferred_cause": e.inferred_cause,
            "cause_confidence": e.cause_confidence, "rationale": e.rationale}

def workers(engine: Engine, settings: Any = None, now: datetime | None = None,
            limit: int = 20) -> list[dict[str, Any]]:
    if settings is None:
        from water_source_research.core.config import get_settings
        settings = get_settings()
    obs = settings.observability
    stale_after = obs.sample_seconds * obs.heartbeat_stale_factor
    now = now or now_utc()
    with Session(engine) as session:
        rows = session.scalars(select(W).order_by(W.updated_at.desc()).limit(limit)).all()
    out = []
    for w in rows:
        age = _seconds(w.last_heartbeat_at, now) if w.last_heartbeat_at else None
        if w.stopped_at or w.termination_class:
            status = "STOPPED" if w.termination_class in (None, "CLEAN_SHUTDOWN") else "EXITED"
        elif age is None or age > stale_after:
            status = "STALE"
        else:
            status = "ALIVE"
        s = w.last_sample or {}
        out.append({"instance_id": w.instance_id, "supervisor_id": w.supervisor_id,
                    "host": w.host, "pid": w.pid, "status": status,
                    "started_at": _iso(w.started_at), "stopped_at": _iso(w.stopped_at),
                    "last_heartbeat_at": _iso(w.last_heartbeat_at), "heartbeat_age_s": age,
                    "exit_code": w.exit_code, "termination_class": w.termination_class,
                    "cpu_percent": s.get("cpu_percent"), "rss_mb": s.get("rss_mb"),
                    "private_mb": s.get("private_mb"), "peak_rss_mb": s.get("peak_rss_mb"),
                    "commit_used_ratio": s.get("commit_used_ratio"),
                    "threads": s.get("threads"), "active_runs": s.get("active_runs") or {},
                    "llm_requests_in_flight": s.get("llm_requests_in_flight"),
                    "checkpoint_peak_bytes": s.get("checkpoint_peak_bytes"),
                    "telemetry": s.get("telemetry"),
                    "usage_rows_dropped": s.get("usage_rows_dropped")})
    return out

def crashes(engine: Engine, *, hours: float | None = 24 * 7,
            now: datetime | None = None) -> list[dict[str, Any]]:
    since = (now or now_utc()) - timedelta(hours=hours) if hours else None
    q = select(E).where(E.kind == "WORKER_EXITED_UNEXPECTEDLY").order_by(E.occurred_at.desc())
    if since:
        q = q.where(E.occurred_at >= since)
    with Session(engine) as session:
        return [_event(e) for e in session.scalars(q)]

def recovery_timeline(engine: Engine, *, hours: float | None = 24 * 7,
                      now: datetime | None = None) -> list[dict[str, Any]]:
    """Incidents: a worker's unexpected exit, the supervisor's restart (worker track), and
    for each interrupted run what happened to the research (research track). A restarted
    worker does not mean a resumed run: each run's outcome is reported on its own."""
    since = (now or now_utc()) - timedelta(hours=hours) if hours else None
    q = select(E).where(E.kind.in_(RECOVERY_KINDS)).order_by(E.occurred_at)
    if since:
        q = q.where(E.occurred_at >= since)
    with Session(engine) as session:
        events = [_event(e) for e in session.scalars(q)]
        statuses = dict(session.execute(select(J.run_id, J.status)).all())
    incidents = []
    for crash in (e for e in events if e["kind"] == "WORKER_EXITED_UNEXPECTEDLY"):
        after = [e for e in events if e["occurred_at"] >= crash["occurred_at"]]
        restart = next((e for e in after if e["kind"] in ("WORKER_RESTARTED", "RESTART_FAILED")
                        and (e["facts"].get("previous_instance") == crash["instance_id"]
                             or e["kind"] == "RESTART_FAILED")), None)
        runs_hit = crash["facts"].get("affected_runs") or []
        per_run = []
        for run_id in runs_hit:
            mine = [e for e in after if e["run_id"] == run_id]
            resumed = next((e for e in mine if e["kind"] == "JOB_RESUMED"), None)
            failed = next((e for e in mine if e["kind"] == "JOB_RECOVERY_FAILED"), None)
            per_run.append({
                "run_id": run_id, "current_status": statuses.get(run_id),
                "research_outcome": ("RESUMED" if resumed else "RECOVERY_FAILED" if failed
                                     else "PENDING"),
                "steps": [e for e in mine if e["kind"] != "JOB_INTERRUPTED"]})
        incidents.append({"crash": crash,
                          "worker_restart": ("RESTARTED" if restart and restart["kind"]
                                             == "WORKER_RESTARTED"
                                             else "GAVE_UP" if restart else "NOT_RESTARTED"),
                          "restart_event": restart, "runs": per_run})
    return incidents[::-1]

# --- overview ---------------------------------------------------------------------------

def overview(engine: Engine, *, hours: float = 24, settings: Any = None,
             now: datetime | None = None) -> dict[str, Any]:
    now = now or now_utc()
    since = now - timedelta(hours=hours)
    with Session(engine) as session:
        by_status = dict(session.execute(select(J.status, func.count())
                                         .where(NOT_REVALIDATION).group_by(J.status)).all())
        recent = session.execute(select(J.status, func.count())
                                 .where(J.finished_at >= since, NOT_REVALIDATION)
                                 .group_by(J.status)).all()
        errors = session.execute(select(J.run_id, J.city_name, J.last_error, J.updated_at)
                                 .where(J.last_error.is_not(None), J.updated_at >= since)
                                 .order_by(J.updated_at.desc()).limit(10)).all()
        finished = session.execute(select(*JOB_COLUMNS).where(
            J.finished_at >= since, NOT_REVALIDATION)).all()
        steps = _steps(session, (j.run_id for j in finished))
        ledger = _usage_rows(session, since)
    splits = [time_split(j.created_at, j.finished_at, j.status, steps.get(j.run_id, []), now)
              for j in finished]
    ws = workers(engine, settings, now)
    return {
        "window_hours": hours,
        "runs": {"active": sum(by_status.get(s, 0) for s in ACTIVE),
                 "queued": by_status.get("QUEUED", 0), "running": by_status.get("RUNNING", 0),
                 "awaiting_review": by_status.get("AWAITING_REVIEW", 0),
                 "succeeded_total": by_status.get("SUCCEEDED", 0),
                 "failed_total": by_status.get("FAILED", 0),
                 "finished_in_window": dict(recent)},
        "durations": {"runs": len(splits),
                      "median_wall_s": _median(s["wall_s"] for s in splits),
                      "median_active_s": _median(s["active_s"] for s in splits),
                      "median_queue_wait_s": _median(s["queue_wait_s"] for s in splits),
                      "median_review_wait_s": _median(s["review_wait_s"] for s in splits)},
        "workers": {"total": len(ws), **Counter(w["status"] for w in ws)},
        "recent_errors": [{"run_id": r.run_id, "city": r.city_name, "error": r.last_error,
                           "at": _iso(r.updated_at)} for r in errors]
        + [{"run_id": None, "city": None, "error": f"{c['inferred_cause']} "
            f"({c['cause_confidence']}): exit {c['facts'].get('exit_code')}",
            "at": c["occurred_at"]} for c in crashes(engine, hours=hours, now=now)[:5]],
        "usage": summarize_usage(ledger),
    }

def _median(values: Iterable[float | None]) -> float | None:
    v = [x for x in values if x is not None]
    return round(statistics.median(v), 1) if v else None
