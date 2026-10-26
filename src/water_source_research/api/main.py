from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, field_validator
from water_source_research.api.evaluation import evaluation_router
from water_source_research.api.monitoring import monitoring_router
from water_source_research.core.config import get_settings
from water_source_research.procedures.services import ProcedureServices, build_procedure_services
from water_source_research.reporting.run_views import field_provenance, human_review_log
from water_source_research.research_queue.queue import JobStatus, ResearchQueue
from water_source_research.schemas.procedures import ProcedureStatus
from water_source_research.research_queue.steps import NODE_ORDER
from water_source_research.services.city_catalog import load_city_catalog
from water_source_research.services.evidence_store import EvidenceRepository
from water_source_research.services.field_state import load_field_definitions, select_fields
from water_source_research.schemas.records import QualityCheck, RecordSet
from water_source_research.schemas.review import ResumeRequest, ReviewSubmission
from water_source_research.schemas.standardized import HEADERS
from water_source_research.services.export import (
    ExportBlocked, build_export, check_exportable, standardized_rows,
)
from water_source_research.services.record_store import RecordRepository
from water_source_research.services.review import (
    DecisionConflict, ReviewRepository, validate_decision,
)

class RunCityRequest(BaseModel):
    city_name: str
    country: str | None = None
    priority: int = 0
    # Optional subset of field keys to research (e.g. a test run); omitted = all fields.
    field_keys: list[str] | None = None
    # Select this run for a full trace (whatever OBSERVABILITY__SAMPLE_RATIO says).
    trace: bool = False

    @field_validator("field_keys")
    @classmethod
    def _known_fields(cls, keys: list[str] | None) -> list[str] | None:
        return select_fields(keys)

class ReviewRequest(BaseModel):
    reviewer: str

def create_app(queue: ResearchQueue | None = None,
               procedures: ProcedureServices | None = None,
               evidence: EvidenceRepository | None = None,
               reviews: ReviewRepository | None = None,
               records: RecordRepository | None = None) -> FastAPI:
    """City runs are asynchronous: the API enqueues, workers execute
    (``scripts/run_research_worker.py``), clients poll ``/research/runs/{run_id}``."""
    settings = get_settings()  # validates all configuration at startup
    if queue is None or (procedures is None and settings.procedure_memory.enabled):
        from water_source_research.db.session import get_engine
        if queue is None:
            queue = ResearchQueue(get_engine(), settings.research_queue)
        if procedures is None and settings.procedure_memory.enabled:
            procedures = build_procedure_services(settings, get_engine())
    if evidence is None:
        evidence = EvidenceRepository(queue.engine)
    if reviews is None:
        reviews = ReviewRepository(queue.engine)
    if records is None:
        records = RecordRepository(queue.engine)
    app = FastAPI(title="Water Source Research Platform", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- city runs ----------------------------------------------------------------------

    @app.post("/research/city", status_code=202)
    async def submit_city(request: RunCityRequest):
        submitted = queue.submit(request.city_name, request.country, request.priority,
                                 request.field_keys, trace=request.trace)
        body = {**submitted.job.public(), "created": submitted.created,
                "status_url": f"/research/runs/{submitted.job.run_id}"}
        # 202 for a new run; 200 when an active run for this city already existed.
        return JSONResponse(body, status_code=202 if submitted.created else 200)

    @app.post("/research/runs/{run_id}/reprocess", status_code=202)
    async def reprocess_run(run_id: str, trace: bool = False):
        """Re-run validation, resolution, review and assembly on a finished run's stored
        claims and evidence (a new run; no discovery, fetching or model calls). 200 with the
        city's active run if it already has one."""
        try:
            submitted = queue.submit_reprocess(run_id, trace=trace)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        body = {**submitted.job.public(), "created": submitted.created,
                "status_url": f"/research/runs/{submitted.job.run_id}"}
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
        if job.status not in (JobStatus.SUCCEEDED, JobStatus.AWAITING_REVIEW):
            raise HTTPException(409, f"run is {job.status.value}")
        # While awaiting review this is the state the run paused in (partial).
        return {"run_id": run_id, "partial": job.status is JobStatus.AWAITING_REVIEW,
                **(queue.result(run_id) or {})}

    def _trace_review_resume(job) -> None:
        """The continuation after a review is its own trace (a review can take days),
        linked to the run's earlier trace; the worker continues it."""
        from opentelemetry.context import Context
        from water_source_research.observability.otel import TRACE_REQUESTED, get_tracer
        from water_source_research.observability.tracing import (
            TraceStore, carrier, link_from,
        )
        try:
            earlier = link_from(job.trace_context, **{"link.kind": "before_review"})
            attrs = {"research.run_id": job.run_id, "review.round": job.review_round}
            if job.trace_requested:
                attrs[TRACE_REQUESTED] = True
            with get_tracer().start_as_current_span(
                    "research.review_resume", context=Context(), attributes=attrs,
                    links=[earlier] if earlier else None) as span:
                stored = carrier(span)
                if stored:
                    queue.set_trace_context(job.run_id, {**stored, "kind": "review_resume"})
                TraceStore(queue.engine).record(job.run_id, "REVIEW_RESUME", span)
        except Exception as exc:  # noqa: BLE001 - tracing never blocks a review
            import logging
            logging.getLogger(__name__).warning("review resume trace not recorded (%s)",
                                                type(exc).__name__)

    @app.get("/research/runs/{run_id}/traces")
    async def get_traces(run_id: str):
        """Trace ids of this run's submission, executions, recoveries and review resumes
        (the run_id is the stable key; a resumed run may continue under a new trace)."""
        from water_source_research.observability.tracing import TraceStore
        job = queue.get(run_id)
        if job is None:
            raise HTTPException(404, "run not found")
        ui = settings.observability.trace_ui_url.rstrip("/")
        rows = TraceStore(queue.engine).list(run_id)
        submit = (job.trace_context or {}).get("traceparent", "")
        return {"run": job.public(), "trace_requested": job.trace_requested,
                "submit_traceparent": submit or None,
                "traces": [{**r, "created_at": r["created_at"].isoformat(),
                            "url": f"{ui}/trace/{r['trace_id']}" if r["sampled"] else None}
                           for r in rows]}

    @app.get("/research/runs/{run_id}/lifecycle")
    async def get_lifecycle(run_id: str):
        """Worker events that touched this run: interrupted by a worker exit, lease reclaimed,
        checkpoint recovery and its outcome (observed facts and inferred cause kept apart)."""
        from water_source_research.observability.worker_events import EventStore
        job = queue.get(run_id)
        if job is None:
            raise HTTPException(404, "run not found")
        events = EventStore(queue.engine).events(run_id=run_id)
        return {"run": job.public(), "events": [
            {**e, "occurred_at": e["occurred_at"].isoformat(),
             "recorded_at": e["recorded_at"].isoformat()} for e in events]}

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

    @app.get("/research/runs/{run_id}/discovery")
    async def get_discovery(run_id: str):
        """Every ranked discovery candidate (pointers to sources, never evidence)."""
        require_run(run_id)
        result = queue.result(run_id)
        if result is None:
            return {"available": False, "candidates": []}
        return {"available": True,
                "provider_status": result.get("discovery_provider_status") or {},
                "discovery_rounds": result.get("discovery_rounds"),
                "candidates": result.get("discovery_candidates") or []}

    @app.get("/research/runs/{run_id}/evidence")
    async def get_evidence(run_id: str):
        """Every URL the run tried to acquire, with status, provenance and raw object key."""
        require_run(run_id)
        return {"documents": evidence.documents(run_id), "counts": evidence.counts(run_id)}

    @app.get("/research/runs/{run_id}/evidence/chunks")
    async def get_evidence_chunks(run_id: str, document_id: int | None = None,
                                  limit: int = 200):
        require_run(run_id)
        return {"chunks": evidence.chunks(run_id, document_id, min(limit, 1000))}

    @app.get("/research/runs/{run_id}/claims")
    async def get_claims(run_id: str):
        """Stored candidate/selected claims with their exact quotes and source documents."""
        require_run(run_id)
        return {"field_states": evidence.field_states(run_id),
                "claims": evidence.claims(run_id)}

    def require_run(run_id: str):
        job = queue.get(run_id)
        if job is None:
            raise HTTPException(404, "unknown run")
        return job

    # --- SupplyRecords, provenance trace and export -------------------------------------

    def record_set(run_id: str) -> tuple[RecordSet, list[QualityCheck]]:
        stored = (queue.result(run_id) or {}).get("record_set") or {}
        quality = [QualityCheck.model_validate(c) for c in stored.get("quality", [])]
        return records.load(run_id), quality

    @app.get("/research/runs/{run_id}/records")
    async def get_records(run_id: str):
        """SupplyRecords with their export rows, city values (once), quality and completion."""
        job = require_run(run_id)
        rs, quality = record_set(run_id)
        rows = standardized_rows(rs)
        return {"run": job.public(), "completion": job.completion,
                "quality": [c.model_dump(mode="json") for c in quality],
                "records": [{**r.model_dump(mode="json"), "row": row}
                            for r, row in zip(sorted(rs.records, key=lambda r: r.position), rows,
                                              strict=True)],
                "city_values": {k: v.model_dump(mode="json")
                                for k, v in rs.city_values.items()},
                "exports": records.exports(run_id)}

    @app.get("/research/runs/{run_id}/records/{record_key}/trace")
    async def trace_value(run_id: str, record_key: str, field_key: str):
        """One exported value back to its evidence: value -> resolution (and reviewer
        decisions) -> claims -> citations -> chunk text -> source document -> URL. Calculated
        values list their formula and the same chain for each input."""
        require_run(run_id)
        rs, _ = record_set(run_id)
        if record_key not in rs.record_values:
            raise HTTPException(404, "unknown record")
        rv = rs.value(field_key, record_key)
        if rv is None:
            raise HTTPException(404, f"no value for {field_key}")
        claims = {c["claim_uid"]: c for c in evidence.claims(run_id) if c["claim_uid"]}
        docs = {d["document_id"]: d for d in evidence.documents(run_id)}
        decisions = {d["id"]: d for d in reviews.decisions(run_id)}
        resolutions = [r for r in evidence.resolutions(run_id) if r["field_key"] == field_key]

        def chain(uids: list[str]) -> list[dict]:
            out = []
            for uid in uids:
                claim = claims.get(uid)
                if claim is None:
                    continue
                cites = []
                for c in claim["citations"]:
                    chunk = evidence.chunk(run_id, c["chunk_id"]) if c.get("chunk_id") else None
                    cites.append({**c, "chunk_text": chunk["text"] if chunk else None,
                                  "document": docs.get(c.get("document_id"))})
                out.append({**claim, "citations": cites})
            return out

        calc = rv.calculation
        return {
            "record_key": record_key, "field_key": field_key, "column": HEADERS[field_key],
            "value": rv.model_dump(mode="json"),
            "claims": chain(rv.claim_uids),
            "inputs": [{**i.model_dump(mode="json"), "claims": chain(i.claim_uids)}
                       for i in (calc.inputs if calc else [])],
            "resolution_history": resolutions,
            "review_decisions": [decisions[i] for i in rv.review_decision_ids if i in decisions],
        }

    @app.get("/research/runs/{run_id}/export")
    async def export_run(run_id: str, format: str = "csv", kind: str = "standardized",
                         requested_by: str | None = None):
        """Standardized CSV/XLSX (one row per SupplyRecord; XLSX adds provenance, quality and
        review-log sheets) or, with kind=provenance, the audit export. 409 unless the run
        SUCCEEDED and isn't INCOMPLETE."""
        job = require_run(run_id)
        if kind not in ("standardized", "provenance") or format not in ("csv", "xlsx"):
            raise HTTPException(422, "format is csv|xlsx and kind is standardized|provenance")
        try:
            check_exportable(job.status.value, job.completion)
        except ExportBlocked as exc:
            raise HTTPException(409, str(exc)) from exc
        rs, quality = record_set(run_id)
        claims = {c["claim_uid"]: c for c in evidence.claims(run_id) if c["claim_uid"]}
        file = build_export(rs, quality, claims, reviews.decisions(run_id), job.city_name,
                            run_id, format, kind)
        audit = records.record_export(run_id, kind, format, file.sha256, file.rows,
                                      len(file.content), job.completion, requested_by)
        return Response(file.content, media_type=file.media_type, headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "X-Export-Id": str(audit["id"]), "X-Export-Sha256": file.sha256})

    # --- human review -------------------------------------------------------------------

    def require_waiting(run_id: str):
        job = require_run(run_id)
        if job.status is not JobStatus.AWAITING_REVIEW:
            raise HTTPException(409, f"run is {job.status.value}, not AWAITING_REVIEW")
        return job

    @app.get("/research/runs/{run_id}/review/pending")
    async def review_pending(run_id: str):
        """Open review items of the round the run is waiting on, with candidates and evidence."""
        job = require_run(run_id)
        if job.status is not JobStatus.AWAITING_REVIEW:
            return {"run": job.public(), "round": job.review_round, "items": []}
        items = reviews.items(run_id, job.review_round, status="OPEN")
        latest = _latest_by_item(reviews.decisions(run_id, job.review_round))
        return {"run": job.public(), "round": job.review_round,
                "items": [{**i, "decision": latest.get(i["id"])} for i in items]}

    @app.get("/research/runs/{run_id}/review/progress")
    async def review_progress(run_id: str):
        """How far the reviewer is in the round the run waits on: decided vs open items."""
        job = require_run(run_id)
        if job.status is not JobStatus.AWAITING_REVIEW:
            return {"round": job.review_round, "total": 0, "decided": 0, "remaining_items": []}
        items = reviews.items(run_id, job.review_round, status="OPEN")
        decided = _latest_by_item(reviews.decisions(run_id, job.review_round))
        remaining = [{"id": i["id"], "field_key": i["field_key"], "entity_key": i["entity_key"]}
                     for i in items if i["id"] not in decided]
        return {"round": job.review_round, "total": len(items),
                "decided": len(items) - len(remaining), "remaining_items": remaining}

    @app.get("/research/runs/{run_id}/review/items/{item_id}")
    async def review_item(run_id: str, item_id: str):
        """One item with the full text of each cited chunk, for checking quotes in context."""
        require_run(run_id)
        item = reviews.item(run_id, item_id)
        if item is None:
            raise HTTPException(404, "unknown review item")
        contexts = {}
        for c in item["payload"].get("candidates", []):
            for cite in c.get("citations", []):
                if cite.get("chunk_id") and cite["chunk_id"] not in contexts:
                    contexts[cite["chunk_id"]] = evidence.chunk(run_id, cite["chunk_id"])
        decisions = [d for d in reviews.decisions(run_id) if d["item_id"] == item_id]
        return {**item, "chunks": contexts, "decisions": decisions}

    @app.post("/research/runs/{run_id}/review/decisions")
    async def submit_decisions(run_id: str, body: ReviewSubmission):
        """Record decisions (append-only; idempotent per decision_key). 409 if the run isn't
        waiting for this round or the item is closed; 422 if a decision is invalid."""
        job = require_waiting(run_id)
        fields = load_field_definitions()
        checked = []
        problems: dict[str, list[str]] = {}
        for dec in body.decisions:
            item = reviews.item(run_id, dec.item_id)
            if item is None:
                raise HTTPException(404, f"unknown review item {dec.item_id}")
            if dec.round != job.review_round or item["round"] != job.review_round \
                    or item["status"] != "OPEN":
                raise HTTPException(409, f"item {dec.item_id} is not open in review round "
                                         f"{job.review_round} (stale decision)")
            errs = validate_decision(dec, item, fields[item["field_key"]],
                                     lambda chunk_id: evidence.chunk(run_id, chunk_id))
            if errs:
                problems[dec.decision_key] = errs
            checked.append((dec, item))
        if problems:
            raise HTTPException(422, {"invalid_decisions": problems})
        stored = []
        for dec, item in checked:
            try:
                row, created = reviews.add_decision(run_id, body.reviewer, dec, item)
            except DecisionConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            stored.append({**row, "created": created})
        return {"round": job.review_round, "decisions": stored}

    @app.post("/research/runs/{run_id}/review/resume", status_code=202)
    async def resume_review(run_id: str, body: ResumeRequest):
        """Put the run back in the queue once every open item of the round has a decision."""
        job = require_waiting(run_id)
        items = reviews.items(run_id, job.review_round, status="OPEN")
        decided = _latest_by_item(reviews.decisions(run_id, job.review_round))
        undecided = [i["id"] for i in items if i["id"] not in decided]
        if undecided:
            raise HTTPException(409, {"undecided_items": undecided})
        if not queue.resume_review(run_id, job.review_round):
            raise HTTPException(409, "run is no longer waiting on this review round")
        _trace_review_resume(job)
        resumed = queue.get(run_id)
        return {**resumed.public(), "resumed_by": body.reviewer} if resumed else {}

    @app.get("/research/runs/{run_id}/review/history")
    async def review_history(run_id: str):
        """Every review item and decision of the run (append-only), plus applied outcomes."""
        require_run(run_id)
        return {"items": reviews.items(run_id), "decisions": reviews.decisions(run_id),
                "applied": [r for r in evidence.resolutions(run_id)
                            if r["decided_by"] == "REVIEWER"]}

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

    # Evaluation of saved research runs (reads research data only).
    app.include_router(evaluation_router(queue.engine))
    app.include_router(monitoring_router(queue.engine, settings))
    return app

def _latest_by_item(decisions: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for d in decisions:  # in submission order: the last one per item wins
        latest[d["item_id"]] = d
    return latest

def _transition(ok: bool) -> dict:
    if not ok:
        raise HTTPException(409, "version not in a state that allows this transition")
    return {"ok": True}

def _instrument(application: FastAPI) -> FastAPI:
    """Tracing for the API process when enabled (OBSERVABILITY__OTEL_ENABLED)."""
    from water_source_research.observability.otel import configure_telemetry

    def start_usage_ledger() -> None:
        """Only when the app is actually served (not when the module is imported, e.g. by
        tests): judge calls of API-triggered evaluations go to the usage ledger."""
        try:
            from water_source_research.db.session import get_engine
            from water_source_research.observability.usage import configure_usage
            configure_usage(get_engine())
        except Exception as exc:  # noqa: BLE001 - monitoring never stops the API
            import logging
            logging.getLogger(__name__).warning("usage ledger not started (%s)",
                                                type(exc).__name__)

    application.router.on_startup.append(start_usage_ledger)
    if configure_telemetry("api") is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(application, excluded_urls="health")
    return application

app = _instrument(create_app())
