"""Evaluation API: reference datasets, evaluation runs of saved research runs, field results,
evidence checks, diagnoses, metrics, adjudication and reports. Reads research data, never
writes it. Results, diagnoses and metrics are served with adjudications applied
(`evaluation/report.py::effective`); the as-evaluated values stay alongside."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, get_args
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from water_source_research.core.paths import REPO_ROOT
from water_source_research.evaluation.compare import Status
from water_source_research.evaluation.diagnosis import ROOT_CAUSES
from water_source_research.evaluation import judge as judge_module
from water_source_research.evaluation.evaluate import run_evaluation
from water_source_research.evaluation.judge import SemanticJudge
from water_source_research.evaluation.reference import import_reference
from water_source_research.evaluation.report import REPORT_KINDS, effective, overview, render
from water_source_research.evaluation.store import EvaluationStore, is_revalidation
from water_source_research.evaluation.views import city_comparison, performance

class ImportRequest(BaseModel):
    path: str  # file under the repository (e.g. resources/<workbook>.xlsx)
    cities: list[str] | None = None
    sheet: str = "Standardization"

class EvaluateRequest(BaseModel):
    research_run_id: str
    dataset_id: str | None = None
    semantic: bool = True  # use the semantic judge for undecided cells

class AdjudicationRequest(BaseModel):
    target: Literal["FIELD_RESULT", "DIAGNOSIS"]
    target_id: int
    decision: Literal["CONFIRM_MATCH", "CONFIRM_MISMATCH", "AMBIGUOUS", "REFERENCE_ERROR",
                      "OVERRIDE_STATUS", "CORRECT_DIAGNOSIS", "CONFIRM_DIAGNOSIS"]
    reviewer: str = Field(min_length=1, max_length=128)
    status: str | None = None  # new comparison status (OVERRIDE_STATUS)
    root_cause: str | None = None  # corrected root cause (CORRECT_DIAGNOSIS)
    note: str | None = Field(default=None, max_length=4000)

FIELD_DECISIONS = {"CONFIRM_MATCH", "CONFIRM_MISMATCH", "AMBIGUOUS", "REFERENCE_ERROR",
                   "OVERRIDE_STATUS"}
DIAGNOSIS_DECISIONS = {"CORRECT_DIAGNOSIS", "CONFIRM_DIAGNOSIS"}
STATUSES = set(get_args(Status))

def evaluation_router(engine: Engine, store: EvaluationStore | None = None,
                      judge_factory: Callable[[], SemanticJudge | None] | None = None
                      ) -> APIRouter:
    """``judge_factory``: builds the semantic judge per evaluation (default: the configured
    LLM provider; ``lambda: None`` = deterministic only)."""
    store = store or EvaluationStore(engine)
    if judge_factory is None:
        judge_factory = lambda: judge_module.build_judge(engine)
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

    @router.get("/overview")
    async def evaluation_overview(include_revalidation: bool = False):
        """Latest completed evaluation per city, with headline metrics."""
        return overview(store, include_revalidation)

    @router.post("/runs", status_code=201)
    async def evaluate(body: EvaluateRequest):
        try:
            judge = judge_factory() if body.semantic else None
            eval_id = run_evaluation(engine, body.research_run_id, body.dataset_id,
                                     judge=judge)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return store.run(eval_id)

    @router.get("/runs")
    async def runs(research_run_id: str | None = None):
        return store.runs(research_run_id)

    def view(eval_id: str) -> dict:
        v = effective(store, eval_id)
        if v is None:
            raise HTTPException(404, "unknown evaluation run")
        return v

    @router.get("/runs/{eval_id}")
    async def run(eval_id: str):
        v = view(eval_id)
        return {**v["run"], "metrics": v["metrics"], "metrics_basis": v["metrics_basis"],
                "adjudicated_results": v["adjudicated_results"],
                "adjudicated_diagnoses": v["adjudicated_diagnoses"]}

    @router.get("/runs/{eval_id}/fields")
    async def fields(eval_id: str, stage: str | None = None):
        return [r for r in view(eval_id)["results"] if stage is None or r["stage"] == stage]

    @router.get("/runs/{eval_id}/fields/{field_key}")
    async def investigate(eval_id: str, field_key: str):
        """Everything about one field: reference, every stage's result, candidates with their
        evidence checks, diagnoses and adjudications."""
        v = view(eval_id)
        results = [r for r in v["results"] if r["field_key"] == field_key]
        if not results:
            raise HTTPException(404, f"no results for {field_key}")
        ids = {r["id"] for r in results}
        diagnoses = [d for d in v["diagnoses"] if d["field_key"] == field_key]
        dids = {d["id"] for d in diagnoses}
        return {"field_key": field_key, "results": results, "diagnoses": diagnoses,
                "evidence": [e for e in v["evidence"] if e["field_key"] == field_key],
                "adjudications": [a for a in v["adjudications"]
                                  if (a["target"] == "FIELD_RESULT" and a["target_id"] in ids)
                                  or (a["target"] == "DIAGNOSIS" and a["target_id"] in dids)]}

    @router.get("/runs/{eval_id}/evidence")
    async def evidence(eval_id: str):
        return view(eval_id)["evidence"]

    @router.get("/runs/{eval_id}/diagnoses")
    async def diagnoses(eval_id: str):
        return view(eval_id)["diagnoses"]

    @router.get("/runs/{eval_id}/adjudications")
    async def adjudications(eval_id: str):
        return view(eval_id)["adjudications"]

    @router.post("/runs/{eval_id}/adjudications", status_code=201)
    async def adjudicate(eval_id: str, body: AdjudicationRequest):
        if store.run(eval_id) is None:
            raise HTTPException(404, "unknown evaluation run")
        targets = {r["id"] for r in store.field_results(eval_id)} \
            if body.target == "FIELD_RESULT" else {d["id"] for d in store.diagnoses(eval_id)}
        if body.target_id not in targets:
            raise HTTPException(404, f"no {body.target} {body.target_id} in this evaluation")
        allowed = FIELD_DECISIONS if body.target == "FIELD_RESULT" else DIAGNOSIS_DECISIONS
        if body.decision not in allowed:
            raise HTTPException(422, f"{body.decision} does not apply to a {body.target}")
        if body.decision == "OVERRIDE_STATUS" and body.status not in STATUSES:
            raise HTTPException(422, f"OVERRIDE_STATUS needs a status: {sorted(STATUSES)}")
        if body.decision == "CORRECT_DIAGNOSIS" and body.root_cause not in ROOT_CAUSES:
            raise HTTPException(422, f"CORRECT_DIAGNOSIS needs a root_cause: {ROOT_CAUSES}")
        return store.add_adjudication(eval_id, body.target, body.target_id, body.decision,
                                      body.reviewer, body.status, body.root_cause, body.note)

    @router.get("/cities")
    async def evaluated_cities(include_revalidation: bool = False):
        """Cities with a completed evaluation: the latest one per city, and all of them
        (evaluations of REVALIDATE runs only with ``include_revalidation``)."""
        out: dict[str, dict] = {}
        for r in store.runs():  # newest first
            if r["status"] != "COMPLETED" or (is_revalidation(r) and not include_revalidation):
                continue
            entry = out.setdefault(r["city_key"], {"city_key": r["city_key"],
                                                   "latest_eval_run_id": r["id"],
                                                   "evaluations": []})
            entry["evaluations"].append({"id": r["id"], "stages": r["stages"],
                                         "created_at": r["created_at"],
                                         "research_run_id": r["research_run_id"]})
        return list(out.values())

    @router.get("/runs/{eval_id}/comparison")
    async def comparison(eval_id: str, stage: Literal["PROPOSED", "AUTO", "FINAL"] = "PROPOSED"):
        """Field | Manual | Automated | Status for one stage, with every stage's summary."""
        v = view(eval_id)
        if stage not in v["run"]["stages"]:
            raise HTTPException(409, f"this evaluation has no {stage} stage "
                                     f"(stages: {', '.join(v['run']['stages'])})")
        return city_comparison(v, stage)

    @router.get("/performance")
    async def research_performance(include_revalidation: bool = False):
        """Accuracy and coverage by city, by field and overall (latest evaluation per city)."""
        return performance(store, include_revalidation)

    @router.get("/runs/{eval_id}/report")
    async def report(eval_id: str, kind: str = "summary", format: str = "md"):
        if kind not in REPORT_KINDS:
            raise HTTPException(422, f"kind must be one of {REPORT_KINDS}")
        try:
            content, filename, media = render(view(eval_id), kind, format)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(content, media_type=media,
                        headers={"content-disposition": f'attachment; filename="{filename}"'})

    return router
