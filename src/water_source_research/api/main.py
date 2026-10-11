from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from water_source_research.core.config import get_settings
from water_source_research.procedures.services import ProcedureServices, build_procedure_services
from water_source_research.reporting.run_views import field_provenance, human_review_log
from water_source_research.research_queue.queue import JobStatus, ResearchQueue
from water_source_research.schemas.procedures import ProcedureStatus
from water_source_research.research_queue.steps import NODE_ORDER
from water_source_research.services.city_catalog import load_city_catalog
from water_source_research.services.field_state import load_field_definitions

class RunCityRequest(BaseModel):
    city_name: str
    country: str | None = None
    priority: int = 0

class ReviewRequest(BaseModel):
    reviewer: str

def create_app(queue: ResearchQueue | None = None,
               procedures: ProcedureServices | None = None) -> FastAPI:
    """City runs are asynchronous: the API enqueues, workers execute
    (``scripts/run_research_worker.py``), clients poll ``/research/runs/{run_id}``."""
    settings = get_settings()  # validates all configuration at startup
    if queue is None or (procedures is None and settings.procedure_memory.enabled):
        from water_source_research.db.session import get_engine
        if queue is None:
            queue = ResearchQueue(get_engine(), settings.research_queue)
        if procedures is None and settings.procedure_memory.enabled:
            procedures = build_procedure_services(settings, get_engine())
    app = FastAPI(title="Water Source Research Platform", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- city runs ----------------------------------------------------------------------

    @app.post("/research/city", status_code=202)
    async def submit_city(request: RunCityRequest):
        submitted = queue.submit(request.city_name, request.country, request.priority)
        body = {**submitted.job.public(), "created": submitted.created,
                "status_url": f"/research/runs/{submitted.job.run_id}"}
        # 202 for a new run; 200 when an active run for this city already existed.
        return JSONResponse(body, status_code=202 if submitted.created else 200)

    @app.get("/research/cities")
    async def cities():
        return [{"label": c.label, **c.model_dump()} for c in load_city_catalog()]

    @app.get("/research/runs")
    async def list_runs(status: JobStatus | None = None, limit: int = 100,
                        city: str | None = None, country: str | None = None):
        return [j.public() for j in queue.list(status, min(limit, 500), city, country)]

    @app.get("/research/runs/{run_id}")
    async def get_run(run_id: str):
        job = queue.get(run_id)
        if job is None:
            raise HTTPException(404, "unknown run")
        return job.public()

    @app.get("/research/runs/{run_id}/result")
    async def get_result(run_id: str):
        job = queue.get(run_id)
        if job is None:
            raise HTTPException(404, "unknown run")
        if job.status is not JobStatus.SUCCEEDED:
            raise HTTPException(409, f"run is {job.status.value}")
        return {"run_id": run_id, **(queue.result(run_id) or {})}

    @app.get("/research/runs/{run_id}/steps")
    async def get_steps(run_id: str):
        job = require_run(run_id)
        steps = queue.steps(run_id)
        return {"run": job.public(), "expected_nodes": list(NODE_ORDER), "steps": steps}

    @app.get("/research/runs/{run_id}/review")
    async def get_review_log(run_id: str):
        require_run(run_id)
        result = queue.result(run_id)
        if result is None:
            return {"available": False, "items": []}
        return {"available": True, "items": human_review_log(result, load_field_definitions())}

    @app.get("/research/runs/{run_id}/fields")
    async def get_field_provenance(run_id: str):
        require_run(run_id)
        result = queue.result(run_id)
        if result is None:
            return {"available": False, "fields": []}
        return {"available": True, "fields": field_provenance(result, load_field_definitions())}

    def require_run(run_id: str):
        job = queue.get(run_id)
        if job is None:
            raise HTTPException(404, "unknown run")
        return job

    @app.post("/research/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        job = queue.cancel(run_id)
        if job is None:
            raise HTTPException(404, "unknown run")
        return job.public()

    @app.get("/research/queue/stats")
    async def queue_stats():
        return queue.stats()

    # --- procedural memory --------------------------------------------------------------

    def require() -> ProcedureServices:
        if procedures is None:
            raise HTTPException(404, "procedure memory is disabled")
        return procedures

    @app.get("/procedures")
    async def list_procedures(status: ProcedureStatus | None = None, limit: int = 100):
        return require().repo.list_versions(status, min(limit, 500))

    @app.get("/procedures/stats")
    async def procedure_stats():
        return require().repo.stats_summary()

    @app.get("/procedures/versions/{version_id}/origins")
    async def procedure_origins(version_id: str):
        services = require()
        if services.repo.get_version(version_id) is None:
            raise HTTPException(404, "unknown procedure version")
        return services.repo.origins_for_version(version_id)

    @app.post("/procedures/versions/{version_id}/approve")
    async def approve(version_id: str, body: ReviewRequest):
        return _transition(require().lifecycle.approve(version_id, body.reviewer))

    @app.post("/procedures/versions/{version_id}/reject")
    async def reject(version_id: str):
        return _transition(require().lifecycle.reject(version_id))

    @app.post("/procedures/versions/{version_id}/deprecate")
    async def deprecate(version_id: str):
        return _transition(require().lifecycle.deprecate(version_id))

    return app

def _transition(ok: bool) -> dict:
    if not ok:
        raise HTTPException(409, "version not in a state that allows this transition")
    return {"ok": True}

app = create_app()
