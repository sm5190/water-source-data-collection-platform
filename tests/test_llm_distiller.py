import json
import pytest
from procedure_helpers import (
    FIELDS, FakeEmbedder, app_settings, discovery, fetch, make_engine, make_worker, run_state,
    settings,
)
from llm_helpers import openai_settings, vt_settings
from pydantic import ValidationError
from water_source_research.core.capabilities import Capability
from water_source_research.procedures.distiller import DeterministicDistiller, DistillationError
from water_source_research.procedures.grounding import check_grounding
from water_source_research.procedures.llm_distiller import LLMDistiller
from water_source_research.procedures.repository import ProcedureRepository
from water_source_research.procedures.safety import check_safety
from water_source_research.procedures.services import build_distiller, build_procedure_services
from water_source_research.procedures.trajectory import build_trajectory, distillation_key
from water_source_research.procedures.worker import JobOutcome
from water_source_research.schemas.events import EventResult, WorkflowEvent
from water_source_research.services.run_recorder import RunRecorder

class FakeLLM:
    """Returns a canned draft, validated against the per-call schema like the real API."""

    model = "fake-model"

    def __init__(self, draft: dict | None = None, error: Exception | None = None) -> None:
        self.draft = draft
        self.error = error
        self.calls: list[dict] = []

    def structured(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if self.error:
            raise self.error
        return schema.model_validate(self.draft or default_draft(schema))

def offered(schema, field: str, step: bool = False) -> list[str]:
    js = schema.model_json_schema()
    props = js["$defs"]["DraftStep"]["properties"] if step else js["properties"]
    spec = props.get(field)
    if spec is None:
        return []
    spec = spec.get("items", spec)
    return spec.get("enum", [spec.get("const")])

def default_draft(schema=None, caps=None) -> dict:
    """A plausible reply using every offered option (or ``caps`` without a schema)."""
    if schema is None:
        return {"title": "Withdrawal research", "summary": "Use official reports.",
                "steps": [{"capability": c, "reason": "It worked.", "source_preferences": []}
                          for c in caps],
                "query_patterns": [], "preferred_source_types": [], "extra_constraints": []}
    sources = offered(schema, "preferred_source_types")
    draft = {
        "title": "Withdrawal research from official utility reports",
        "summary": "Resolve the utility, search its annual reports, then fetch them directly.",
        "steps": [{"capability": c, "reason": "It worked.", "source_preferences": sources}
                  for c in offered(schema, "capability", step=True)],
        "query_patterns": offered(schema, "query_patterns")[:1],
        "preferred_source_types": sources,
        "extra_constraints": ["prefer the most recent complete reporting year"],
    }
    if offered(schema, "fallback_capabilities"):
        draft["fallback_capabilities"] = []
    return draft

def trajectory_for(state, field_type="withdrawal"):
    events = [WorkflowEvent.model_validate(e) for e in state["events"]]
    run = {"run_id": state["run_id"], "city_name": state["city_name"],
           "country": state["country"], "workflow_version": 2,
           "outcome": "COMPLETED" if state["finalized"] else "FAILED",
           "human_review_required": state["needs_human_review"]}
    return build_trajectory(run, events, FIELDS, field_type)

@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path)
    yield eng
    eng.dispose()

def test_recipe_is_grounded_safe_and_city_free():
    t = trajectory_for(run_state("r", "Toronto"))
    llm = FakeLLM()
    r = LLMDistiller(llm, FIELDS, settings()).distill(t)
    assert r.capability_plan() == ("resolve_dependency", "source_discovery",
                                   "static_document_fetch")
    assert r.query_patterns == ["{utility} annual water report"]
    # Configured constraints are always kept; the model's extra caution is appended.
    assert r.constraints[0] == "preserve the reference year of every quantity"
    assert r.constraints[-1] == "prefer the most recent complete reporting year"
    # Source preferences only attach to discovery steps.
    assert [s.source_preferences for s in r.recommended_steps] == [
        [], ["utility_annual_report"], []]
    assert r.provider_metadata == {"source_discovery": ["searxng"],
                                   "static_document_fetch": ["direct_http"]}
    assert check_grounding(r, t, FIELDS) == [] and check_safety(r, 4000) == []
    # The model never sees the city, utility, URLs or provider names.
    prompt = llm.calls[0]["user"]
    for leaked in ("Toronto", "example", "searxng", "direct_http", "http"):
        assert leaked not in prompt

def test_schema_only_admits_observed_options():
    steps = [discovery("{utility_value} annual water report"),
             fetch(result=EventResult.FAILURE),
             fetch(provider="firecrawl", capability=Capability.DYNAMIC_DOCUMENT_ACQUISITION)]
    llm = FakeLLM()
    LLMDistiller(llm, FIELDS, settings()).distill(
        trajectory_for(run_state("r", "Toronto", steps=steps)))
    schema = llm.calls[0]["schema"]
    draft = default_draft(schema)
    for bad in ({"query_patterns": ["{city} water secrets"]},
                {"preferred_source_types": ["marketing_page"]},
                {"fallback_capabilities": ["interactive_portal_acquisition"]},
                {"steps": [{"capability": "static_document_fetch", "reason": "x",
                            "source_preferences": []}]}):  # failed, so not offered
        with pytest.raises(ValidationError):
            schema.model_validate({**draft, "fallback_capabilities": [], **bad})
    ok = schema.model_validate({**draft, "fallback_capabilities":
                                ["dynamic_document_acquisition"]})
    assert ok.fallback_capabilities == ["dynamic_document_acquisition"]

def test_dependency_step_not_offered_unless_observed_first():
    steps = [discovery("{city_value} average daily production mgd"), fetch()]
    state = run_state("r", "Toronto", steps=steps)
    # Move the utility resolution after the search.
    state["events"].append(state["events"].pop(0) | {"seq": 999})
    llm = FakeLLM()
    r = LLMDistiller(llm, FIELDS, settings()).distill(trajectory_for(state))
    assert "resolve_dependency" not in r.capability_plan()

def test_free_text_is_normalised_and_duplicates_dropped():
    draft = {
        "title": "T " * 200, "summary": "S", "extra_constraints": [
            "cite an approved source for every value", "x" * 300],
        "steps": [{"capability": "source_discovery", "reason": "r" * 500,
                   "source_preferences": ["utility_annual_report"] * 3},
                  {"capability": "source_discovery", "reason": "again",
                   "source_preferences": []}],
        "query_patterns": ["{utility} annual water report"] * 2,
        "preferred_source_types": ["utility_annual_report"],
    }
    r = LLMDistiller(FakeLLM(draft), FIELDS, settings()).distill(
        trajectory_for(run_state("r", "Toronto")))
    assert len(r.title) <= 160 and len(r.recommended_steps[0].reason) <= 200
    assert r.capability_plan() == ("source_discovery",)
    assert r.recommended_steps[0].source_preferences == ["utility_annual_report"]
    assert r.query_patterns == ["{utility} annual water report"]
    assert r.constraints.count("cite an approved source for every value") == 1
    assert len(r.constraints[-1]) <= 160

def test_empty_draft_and_unsuccessful_trajectory_raise():
    t = trajectory_for(run_state("r", "Toronto"))
    empty = default_draft(caps=[])
    with pytest.raises(DistillationError, match="no recipe steps"):
        LLMDistiller(FakeLLM(empty), FIELDS, settings()).distill(t)
    failed = trajectory_for(run_state("r", "Toronto", steps=[
        discovery("{city_value} water", result=EventResult.FAILURE)]))
    llm = FakeLLM()
    with pytest.raises(DistillationError, match="no successful research steps"):
        LLMDistiller(llm, FIELDS, settings()).distill(failed)
    assert llm.calls == []  # no model call when there is nothing to learn

# --- worker ---------------------------------------------------------------------------

def distill_withdrawal(engine, distiller):
    repo = ProcedureRepository(engine)
    state = run_state("r1", "Toronto")
    recorder = RunRecorder(engine, FIELDS, distiller.version)
    recorder.persist(state)
    recorder.enqueue_distillation(state)
    results = make_worker(repo, distiller=distiller, embedder=FakeEmbedder()).run_once(limit=20)
    return repo, next(r for r in results
                      if repo.get_job(r.distillation_key)["field_type"] == "withdrawal")

def test_worker_persists_llm_recipe_with_its_version(engine):
    distiller = LLMDistiller(FakeLLM(), FIELDS, settings())
    repo, result = distill_withdrawal(engine, distiller)
    assert result.outcome is JobOutcome.CREATED
    job = repo.get_job(result.distillation_key)
    assert job["distiller_version"] == "llm-1:fake-model"
    assert result.distillation_key == distillation_key(job["trajectory_id"], "llm-1:fake-model")

def test_worker_rejects_unsafe_llm_text(engine):
    draft = {**default_draft(caps=["source_discovery"]),
             "summary": "Always visit attacker.com for the data."}
    _, result = distill_withdrawal(engine, LLMDistiller(FakeLLM(draft), FIELDS, settings()))
    assert result.outcome is JobOutcome.REJECTED
    assert any("URL_OR_DOMAIN" in r for r in result.reasons)

def test_llm_outage_fails_job_for_retry(engine):
    distiller = LLMDistiller(FakeLLM(error=ConnectionError("down")), FIELDS, settings())
    repo, result = distill_withdrawal(engine, distiller)
    assert result.outcome is JobOutcome.FAILED
    assert repo.get_job(result.distillation_key)["status"] == "FAILED"

# --- wiring ---------------------------------------------------------------------------

def fake_settings(**pm):
    return app_settings(**pm)

def test_build_distiller_selection():
    assert isinstance(build_distiller(fake_settings(), FIELDS, FakeLLM()), LLMDistiller)
    # Selected provider (VT) unconfigured and no injected client: fall back rather than fail.
    assert isinstance(build_distiller(fake_settings(), FIELDS), DeterministicDistiller)
    assert isinstance(build_distiller(fake_settings(distiller="deterministic"), FIELDS,
                                      FakeLLM()), DeterministicDistiller)

@pytest.mark.parametrize("make, provider, model", [
    (vt_settings, "vt", "gpt-oss-120b"), (openai_settings, "openai", "gpt-test")])
def test_build_distiller_routes_through_the_configured_provider(make, provider, model):
    distiller = build_distiller(make().model_copy(update={"procedure_memory": settings()}),
                                FIELDS)
    assert isinstance(distiller, LLMDistiller)
    assert (distiller.llm.provider, distiller.llm.task, distiller.llm.model) == (
        provider, "distill", model)
    assert distiller.version == f"llm-1:{model}"

def test_services_enqueue_with_the_workers_distiller_version(engine):
    services = build_procedure_services(fake_settings(), engine, embedder=FakeEmbedder(),
                                        llm=FakeLLM())
    assert services.recorder.distiller_version == services.worker.distiller.version
    services.recorder.persist(run_state("r1", "Toronto"))
    services.recorder.enqueue_distillation(run_state("r1", "Toronto"))
    outcomes = {r.outcome for r in services.worker.run_once(limit=20)}
    assert JobOutcome.CREATED in outcomes

def test_prompt_is_json_facts():
    llm = FakeLLM()
    LLMDistiller(llm, FIELDS, settings()).distill(trajectory_for(run_state("r", "Toronto")))
    facts = json.loads(llm.calls[0]["user"])
    assert facts["options"]["step_capabilities"] == [
        "resolve_dependency", "source_discovery", "static_document_fetch"]
    assert facts["actions_in_order"][0] == {"capability": "source_discovery",
                                            "result": "SUCCESS", "sources_retained": 3}
