import asyncio
from discovery_helpers import SETTINGS, FakeCrossref, FakeProvider, paper, web
from water_source_research.agents.graph import build_graph
from water_source_research.core.capabilities import Capability
from water_source_research.discovery.queries import build_queries, previous_queries
from water_source_research.discovery.service import DiscoveryService
from water_source_research.research_queue.steps import summarize_step
from water_source_research.schemas.events import WorkflowEvent
from water_source_research.services.field_state import initialize_city_state, load_field_definitions

FIELDS = load_field_definitions()
DOI = "10.1016/j.watres.2019.01.001"
TARGETS = ["utility_name", "water_source"]

def initial_state(fields=TARGETS):
    research = initialize_city_state("Toronto", "Canada")
    return {"run_id": "run-1", "city_name": "Toronto", "country": "Canada",
            "research_state": research.model_dump(mode="json"), "unresolved_fields": fields}

def make_service(searx=None, oa=None):
    searx = searx or FakeProvider("searxng", [web("Toronto Water", "https://www.toronto.ca/water")])
    oa = oa or FakeProvider("openalex", [paper("Toronto drinking water sources", DOI)])
    return DiscoveryService(SETTINGS, web=[searx], scholarly=[oa], crossref=FakeCrossref({}))

def run(graph, state):
    return asyncio.run(graph.ainvoke(state))

# --- query planning -------------------------------------------------------------------

def test_default_queries_per_field_type_and_kind():
    qs = build_queries(city="Toronto", country="Canada", utility=None,
                       field_keys=["utility_name", "water_source", "reservoir_names",
                                   "city_name", "percentage_total_population"], fields=FIELDS)
    by_kind = {k: [(q.field_type, q.field_key, q.query) for q in qs if q.kind == k]
               for k in ("web", "academic")}
    assert by_kind["web"] == [
        ("utility", "utility_name", "Toronto Canada drinking water utility"),
        ("water_source", "water_source", "Toronto Canada drinking water sources reservoirs"),
    ]  # city_identity is skipped; derived fields never drive discovery
    assert [q[2] for q in by_kind["academic"]] == ["Toronto municipal drinking water supply",
                                                   "Toronto drinking water supply sources"]

def test_planned_queries_come_first_and_are_bounded_and_deduped():
    qs = build_queries(city="Toronto", country="Canada", utility="Toronto Water",
                       field_keys=["water_source"], fields=FIELDS, max_per_kind=2,
                       planned_queries={"water_source": ["Toronto Water source  report",
                                                         "toronto water SOURCE report"]},
                       already_run={"toronto drinking water supply sources"})
    assert [(q.kind, q.query) for q in qs] == [
        ("web", "Toronto Water source report"),  # whitespace normalized, case-dup dropped
        ("web", "Toronto Water drinking water sources reservoirs"),  # utility replaces place
    ]

def test_previous_queries_read_from_trusted_events():
    events = [{"kind": "TOOL_CALL", "node": "discover", "attrs": {"query": "Toronto Water"}},
              {"kind": "TOOL_CALL", "node": "acquire", "attrs": {"query": "ignored"}},
              {"kind": "TOOL_CALL", "node": "discover", "attrs": {}}]
    assert previous_queries(events) == {"toronto water"}

# --- graph integration ----------------------------------------------------------------

def test_discover_node_produces_ranked_candidates_and_trusted_events():
    result = run(build_graph(discovery=make_service(), max_discovery_rounds=1), initial_state())
    candidates = result["discovery_candidates"]
    assert [c["source_type"] for c in candidates] == ["UTILITY", "ACADEMIC"]
    assert result["candidate_urls"] == [c["url"] for c in candidates]
    assert candidates[1]["doi"] == DOI and candidates[1]["providers"] == ["openalex"]
    # Crossref doesn't know the (fake) DOI: verification ran, found nothing.
    assert result["discovery_provider_status"] == {"searxng": "ok", "openalex": "ok",
                                                   "crossref": "no_results"}
    assert candidates[1]["doi_verified"] is False
    calls = [e for e in result["events"] if e["kind"] == "TOOL_CALL"]
    assert calls and all(WorkflowEvent.model_validate(e) for e in calls)
    assert {e["field_key"] for e in calls} <= set(TARGETS)
    assert {(e["provider"], e["capability"]) for e in calls} == {
        ("searxng", Capability.SOURCE_DISCOVERY), ("openalex", Capability.SCHOLARLY_DISCOVERY),
        ("crossref", Capability.SCHOLARLY_DISCOVERY)}
    search = next(e for e in calls if e["provider"] == "searxng")
    assert search["attrs"]["query"] and search["attrs"]["sources_found"] == 1
    assert "duration_ms" in search["attrs"]
    assert result["finalized"]

def test_failing_provider_does_not_fail_the_run():
    from water_source_research.discovery.http import ProviderError, ProviderErrorKind
    oa = FakeProvider("openalex", error=ProviderError("openalex", ProviderErrorKind.TIMEOUT))
    result = run(build_graph(discovery=make_service(oa=oa), max_discovery_rounds=1),
                 initial_state())
    assert result["finalized"]
    assert result["discovery_provider_status"]["openalex"] == "timeout"
    assert [c["provider"] for c in result["discovery_candidates"]] == ["searxng"]
    failed = [e for e in result["events"] if e.get("result") == "FAILURE"]
    assert failed and failed[0]["strategy"] == "timeout"

def test_service_crash_is_contained():
    class Broken(DiscoveryService):
        async def discover(self, *a, **kw):
            raise RuntimeError("bug")

    result = run(build_graph(discovery=Broken(SETTINGS), max_discovery_rounds=1),
                 initial_state())
    assert result["finalized"] and result["discovery_provider_status"] == {"discovery": "error"}
    assert any(e["strategy"] == "discovery:error" and e["result"] == "FAILURE"
               for e in result["events"])

def test_later_rounds_do_not_repeat_queries_and_keep_candidates():
    searx = FakeProvider("searxng", [web("Toronto Water", "https://www.toronto.ca/water")])
    oa = FakeProvider("openalex", [paper("Toronto drinking water sources", DOI)])
    result = run(build_graph(discovery=make_service(searx, oa), max_discovery_rounds=3),
                 initial_state())
    assert result["discovery_rounds"] == 3
    assert len(searx.queries) == len(set(searx.queries)) == 2  # round 1 only
    assert len(result["discovery_candidates"]) == 2

def test_candidates_accumulate_and_dedupe_across_rounds():
    oa = FakeProvider("openalex", [paper("Toronto drinking water sources", DOI)])
    graph = build_graph(discovery=make_service(oa=oa), max_discovery_rounds=1)
    first = run(graph, initial_state())
    # A later round for another field type finds the same paper again via another provider:
    # it is merged, not duplicated.
    s2 = FakeProvider("semantic_scholar", [paper("Toronto Drinking Water Sources", DOI,
                                                 provider="semantic_scholar", url="https://s2/p")])
    svc = DiscoveryService(SETTINGS, scholarly=[s2])
    state = {**first, "fields_needing_evidence": ["total_city_water_withdrawals_m3_day"]}
    state.pop("finalized")
    second = run(build_graph(discovery=svc, max_discovery_rounds=2), state)
    [p] = [c for c in second["discovery_candidates"] if c["doi"] == DOI]
    assert p["providers"] == ["openalex", "semantic_scholar"]
    assert len(second["discovery_candidates"]) == 2

def test_without_discovery_service_discover_is_unchanged():
    result = run(build_graph(max_discovery_rounds=1), initial_state())
    assert result["candidate_urls"] == []
    assert "discovery_candidates" not in result
    assert not [e for e in result.get("events", []) if e["kind"] == "TOOL_CALL"]

def test_discover_step_summary_lists_top_candidates():
    result = run(build_graph(discovery=make_service(), max_discovery_rounds=1), initial_state())
    summary = summarize_step("discover", result, {})
    assert summary["provider_status"]["searxng"] == "ok"
    assert summary["new_candidates"] == 2
    top = summary["top_candidates"][0]
    assert set(top) == {"title", "url", "providers", "source_type", "doi", "doi_verified",
                        "published_date", "score"}
    assert top["url"] == "https://www.toronto.ca/water"
