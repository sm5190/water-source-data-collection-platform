"""Monitoring endpoints (read-only): runs, workers, crashes, recovery, API usage and cost,
observability backends. Computed on read by ``observability/monitoring.py``."""

from typing import Any
from fastapi import APIRouter, HTTPException
from sqlalchemy import Engine
from water_source_research.observability import alerts, health, monitoring

USAGE_GROUPS = ("provider_model", "city", "run", "category")

def monitoring_router(engine: Engine, settings: Any) -> APIRouter:
    r = APIRouter(prefix="/monitoring", tags=["monitoring"])

    @r.get("/overview")
    def overview(hours: float = 24):
        return {**monitoring.overview(engine, hours=hours, settings=settings),
                "backends": health.backends(settings)}

    @r.get("/runs")
    def runs(limit: int = 200):
        return monitoring.runs(engine, limit=min(limit, 1000))

    @r.get("/runs/{run_id}")
    def run(run_id: str):
        detail = monitoring.run_detail(engine, run_id, settings)
        if detail is None:
            raise HTTPException(404, "run not found")
        return detail

    @r.get("/workers")
    def workers():
        return monitoring.workers(engine, settings)

    @r.get("/crashes")
    def crashes(hours: float = 24 * 7):
        return monitoring.crashes(engine, hours=hours)

    @r.get("/recovery")
    def recovery(hours: float = 24 * 7):
        return monitoring.recovery_timeline(engine, hours=hours)

    @r.get("/usage")
    def usage(hours: float | None = 24, group_by: str = "provider_model"):
        if group_by not in USAGE_GROUPS:
            raise HTTPException(422, f"group_by must be one of {USAGE_GROUPS}")
        return monitoring.usage(engine, hours=hours or None, group_by=group_by)

    @r.get("/alerts")
    def get_alerts():
        return alerts.evaluate(engine, settings)

    @r.get("/backends")
    def backends():
        return health.backends(settings)

    return r
