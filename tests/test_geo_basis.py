"""geo_basis per run: a name-only run is never reported as geographically verified."""

import pytest
from water_source_research.agents.nodes import make_plan_node
from water_source_research.services import geo_match
from water_source_research.services.field_state import load_field_definitions

def test_no_identity_is_name_only(monkeypatch):
    monkeypatch.delenv("CITY_IDENTITY_ALLOW_DRAFT", raising=False)
    geo_match._matcher.cache_clear()
    assert geo_match.geo_basis("Nowhere-on-earth", "Atlantis")["basis"] == "NAME_ONLY"
    # a drafted (not approved) city is name-only too unless drafts are explicitly allowed
    # a drafted (not approved) city is name-only too unless drafts are explicitly allowed
    assert geo_match.geo_basis("Madrid", "Spain")["basis"] == "NAME_ONLY"
    # an approved pilot identity is used in normal runs
    assert geo_match.geo_basis("Houston", "United States of America")["basis"] == "APPROVED"

def test_draft_identity_is_labelled_as_evaluation(monkeypatch):
    monkeypatch.setenv("CITY_IDENTITY_ALLOW_DRAFT", "1")
    geo_match._matcher.cache_clear()
    basis = geo_match.geo_basis("Madrid", "Spain")
    assert basis["basis"] == "DRAFT_EVALUATION"
    assert basis["identity_signature"] != "catalog-only"

@pytest.mark.asyncio
async def test_plan_records_the_basis_once(monkeypatch):
    monkeypatch.delenv("CITY_IDENTITY_ALLOW_DRAFT", raising=False)
    node = make_plan_node(load_field_definitions())
    geo_match._matcher.cache_clear()
    out = await node({"city_name": "Madrid", "country": "Spain"})
    assert out["geo_basis"]["basis"] == "NAME_ONLY"
    events = [e for e in out["events"] if e.get("strategy") == "geo_basis"]
    assert len(events) == 1 and events[0]["attrs"]["geo_basis"] == "NAME_ONLY"
    approved = await node({"city_name": "Houston", "country": "United States of America"})
    assert approved["geo_basis"]["basis"] == "APPROVED"
    again = await node(out)  # a resume keeps the first record
    assert len([e for e in again["events"] if e.get("strategy") == "geo_basis"]) == 1
