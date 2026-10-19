"""Evaluation API: reference datasets, evaluation runs of saved research runs, field results,
evidence checks, diagnoses, metrics and adjudication. Reads research data, never writes it."""

from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from water_source_research.core.paths import REPO_ROOT
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.reference import import_reference
from water_source_research.evaluation.store import EvaluationStore

class ImportRequest(BaseModel):
    path: str  # file under the repository (e.g. resources/<workbook>.xlsx)
    cities: list[str] | None = None
    sheet: str = "Standardization"

class EvaluateRequest(BaseModel):
    research_run_id: str
    dataset_id: str | None = None

class AdjudicationRequest(BaseModel):
    target: Literal["FIELD_RESULT", "DIAGNOSIS"]
    target_id: int
    decision: Literal["CONFIRM_MATCH", "CONFIRM_MISMATCH", "AMBIGUOUS", "REFERENCE_ERROR",
                      "OVERRIDE_STATUS", "CORRECT_DIAGNOSIS", "CONFIRM_DIAGNOSIS"]
    reviewer: str = Field(min_length=1, max_length=128)
    status: str | None = None  # new comparison status (OVERRIDE_STATUS)
    root_cause: str | None = None  # corrected root cause (CORRECT_DIAGNOSIS)
    note: str | None = Field(default=None, max_length=4000)

def evaluation_router(engine: Engine, store: EvaluationStore | None = None) -> APIRouter:
    store = store or EvaluationStore(engine)
    router = APIRouter(prefix="/evaluation", tags=["evaluation"])

    @router.get("/references")
    async def references():
        return store.datasets()

    @router.post("/references", status_code=201)
    async def import_references(body: ImportRequest):
        path = (REPO_ROOT / body.path).resolve()
        if REPO_ROOT.resolve() not in path.parents or not path.exists():
            raise HTTPException(422, "path must name an existing file inside the repository")
        ds = import_reference(Path(path), body.cities, body.sheet)
        created = store.save_dataset(ds)
        return {"id": ds.version, "created": created,
                "cities": [c.city for c in ds.cities],
                "issues": [i.model_dump(mode="json") for i in ds.issues]}

    @router.post("/runs", status_code=201)
    async def evaluate(body: EvaluateRequest):
        try:
            eval_id = run_evaluation(engine, body.research_run_id, body.dataset_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return store.run(eval_id)

    @router.get("/runs")
    async def runs(research_run_id: str | None = None):
        return store.runs(research_run_id)

    def require(eval_id: str) -> dict:
        run = store.run(eval_id)
        if run is None:
            raise HTTPException(404, "unknown evaluation run")
        return run

    @router.get("/runs/{eval_id}")
    async def run(eval_id: str):
        return {**require(eval_id), "metrics": store.metrics(eval_id)}

    @router.get("/runs/{eval_id}/fields")
    async def fields(eval_id: str, stage: str | None = None):
        require(eval_id)
        return store.field_results(eval_id, stage)

    @router.get("/runs/{eval_id}/fields/{field_key}")
    async def investigate(eval_id: str, field_key: str):
        """Everything about one field: reference, every stage's result, candidates with their
        evidence checks, diagnoses and adjudications."""
        require(eval_id)
        results = store.field_results(eval_id, field_key=field_key)
        if not results:
            raise HTTPException(404, f"no results for {field_key}")
        ids = {r["id"] for r in results}
        diagnoses = [d for d in store.diagnoses(eval_id) if d["field_key"] == field_key]
        dids = {d["id"] for d in diagnoses}
        return {"field_key": field_key, "results": results, "diagnoses": diagnoses,
                "evidence": [e for e in store.evidence_checks(eval_id)
                             if e["field_key"] == field_key],
                "adjudications": [a for a in store.adjudications(eval_id)
                                  if (a["target"] == "FIELD_RESULT" and a["target_id"] in ids)
                                  or (a["target"] == "DIAGNOSIS" and a["target_id"] in dids)]}

    @router.get("/runs/{eval_id}/evidence")
    async def evidence(eval_id: str):
        require(eval_id)
        return store.evidence_checks(eval_id)

    @router.get("/runs/{eval_id}/diagnoses")
    async def diagnoses(eval_id: str):
        require(eval_id)
        return store.diagnoses(eval_id)

    @router.get("/runs/{eval_id}/adjudications")
    async def adjudications(eval_id: str):
        require(eval_id)
        return store.adjudications(eval_id)

    @router.post("/runs/{eval_id}/adjudications", status_code=201)
    async def adjudicate(eval_id: str, body: AdjudicationRequest):
        require(eval_id)
        targets = {r["id"] for r in store.field_results(eval_id)} \
            if body.target == "FIELD_RESULT" else {d["id"] for d in store.diagnoses(eval_id)}
        if body.target_id not in targets:
            raise HTTPException(404, f"no {body.target} {body.target_id} in this evaluation")
        if body.decision == "OVERRIDE_STATUS" and not body.status:
            raise HTTPException(422, "OVERRIDE_STATUS needs a status")
        if body.decision == "CORRECT_DIAGNOSIS" and not body.root_cause:
            raise HTTPException(422, "CORRECT_DIAGNOSIS needs a root_cause")
        return store.add_adjudication(eval_id, body.target, body.target_id, body.decision,
                                      body.reviewer, body.status, body.root_cause, body.note)

    return router
