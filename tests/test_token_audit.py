"""Token audit: slots per field and round, ledger tokens matched in order, claim outcomes by
extraction round, skipped slots kept apart, Unknown never counted as 0."""

import pytest
from procedure_helpers import make_engine
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import FieldClaimRecord
from water_source_research.db.observability_models import ProviderUsageRecord
from water_source_research.db.research_models import (
    ResearchJobRecord, ResearchRunRecord, RunEventRecord,
)
from water_source_research.observability.token_audit import audit_run, render_markdown

def node(seq, field, rnd, status="READY", claims=0, requests=1, tokens=(100, 10), reex=False,
         digest=None):
    attrs = {"bundle_status": status, "claims": claims, "discovery_round": rnd}
    if status == "READY":
        attrs.update(llm_requests=requests, cache_hits=0 if requests else 1,
                     prompt_tokens=tokens[0], completion_tokens=tokens[1])
        if digest:
            attrs["context_digest"] = digest
    return {"seq": seq, "kind": "NODE", "node": "extract", "field_key": field,
            "strategy": ("reextract:" if reex else "") + status, "attrs": attrs}

EVENTS = [
    node(1, "water_source", 1, claims=2, tokens=(1000, 200), digest="aaaa"),
    node(2, "utility_name", 1, claims=0, tokens=(800, 150), digest="bbbb"),
    node(3, "city_latitude", 1, status="NO_SUFFICIENT_EVIDENCE"),
    node(4, "utility_name", 2, claims=0, tokens=(900, 120), digest="bbbb"),
    node(5, "water_source", 2, claims=1, tokens=(1200, 220), reex=True, digest="cccc"),
]

def ledger(field, i, o, cached, hit=False):
    return ProviderUsageRecord(run_id="run-1", category="research", kind="LLM", provider="vt",
                               model="m", task="extract", field_key=field,
                               requests=0 if hit else 1, retries=0, cache_hit=hit,
                               input_tokens=i, output_tokens=o, cached_tokens=cached)

def claim(field, rnd, outcome="VALID", selected=False, review=None, uid="u"):
    return FieldClaimRecord(run_id="run-1", field_key=field, value="x", value_status="KNOWN",
                            value_origin="REPORTED", confidence=0.8, is_selected=selected,
                            claim_uid=uid, qualifiers={"extraction_round": rnd},
                            validation_outcome=outcome, review_status=review)

@pytest.fixture
def engine(tmp_path):
    return make_engine(tmp_path, "audit.db")

def add_job(engine, status="AWAITING_REVIEW", result=None):
    with Session(engine) as s, s.begin():
        s.add(ResearchJobRecord(run_id="run-1", city_name="Nairobi", country="Kenya",
                                city_key="nairobi|kenya", status=status, max_attempts=3,
                                mode="RESEARCH", result=result or {}))

def test_waiting_run_uses_job_result_events_and_ledger_tokens(engine):
    add_job(engine, result={"events": EVENTS})
    with Session(engine) as s, s.begin():
        s.add_all([ledger("water_source", 1010, 210, 500), ledger("utility_name", 810, 160, 400),
                   ledger("utility_name", 910, 130, 0), ledger("water_source", 1210, 230, 600)])
        s.add_all([claim("water_source", 1, uid="a"), claim("water_source", 1, "INVALID", uid="b"),
                   claim("water_source", 2, selected=True, uid="c"),
                   claim("water_source", 2, review="CORRECTED", uid="d")])
    r = audit_run(engine, "run-1")
    assert r["events_source"] == "job result" and r["ledger_matched"]
    t = r["total"]
    assert (t["llm_calls"], t["zero_claim_calls"], t["productive_calls"]) == (4, 2, 2)
    # ledger numbers win over the event's own
    assert (t["input_tokens"], t["output_tokens"], t["provider_cached_tokens"]) == (
        3940, 730, 1500)
    assert t["skipped"] == {"NO_SUFFICIENT_EVIDENCE": 1}
    assert (t["valid_claims"], t["selected_claims"]) == (2, 1)  # CORRECTED excluded
    assert t["duplicate_contexts"] == 1  # utility_name round 2 resent the same context
    stages = {g["stage"]: g for g in r["by_stage"]}
    assert stages["reextraction"]["llm_calls"] == 1
    assert stages["reextraction"]["input_tokens"] == 1210
    rounds = {g["round"]: g for g in r["by_round"]}
    assert rounds[2]["zero_claim_calls"] == 1
    md = render_markdown({"runs": [r]})
    assert "Calls that returned no claims" in md and "| 2 | utility_name | initial |" in md

def test_unmatched_ledger_keeps_event_tokens_and_unknown_stays_unknown(engine):
    events = EVENTS[:2] + [node(9, "water_type", 1, claims=1, tokens=(None, None))]
    add_job(engine, result={"events": events})
    with Session(engine) as s, s.begin():
        s.add(ledger("water_source", 1, 1, None))
        s.add(ledger("water_source", 2, 2, None))  # two rows for one call: not matched
    r = audit_run(engine, "run-1")
    assert not r["ledger_matched"]
    assert r["total"]["input_tokens"] is None  # water_type's usage was never reported
    ws = next(g for g in r["by_field"] if g["field"] == "water_source")
    assert ws["input_tokens"] == 1000

def test_finished_run_reads_run_events(engine):
    add_job(engine, status="SUCCEEDED", result={"events": [node(1, "x", 1)]})
    with Session(engine) as s, s.begin():
        s.add(ResearchRunRecord(run_id="run-1", city_name="Nairobi", country="Kenya",
                                workflow_version=3, outcome="COMPLETED"))
        for e in EVENTS:
            s.add(RunEventRecord(run_id="run-1", seq=e["seq"], kind=e["kind"], node=e["node"],
                                 field_key=e["field_key"], strategy=e["strategy"],
                                 attrs=e["attrs"]))
    r = audit_run(engine, "run-1")
    assert r["events_source"] == "run_events"
    assert r["total"]["llm_calls"] == 4
    assert r["total"]["input_tokens"] == 3900  # no ledger rows: event tokens

def test_cache_hit_spends_nothing(engine):
    add_job(engine, result={"events": [node(1, "water_type", 1, claims=1, requests=0,
                                            tokens=(0, 0))]})
    with Session(engine) as s, s.begin():
        s.add(ledger("water_type", None, None, None, hit=True))
    t = audit_run(engine, "run-1")["total"]
    assert (t["llm_calls"], t["model_calls"], t["app_cache_hits"]) == (1, 0, 1)
    assert t["input_tokens"] == 0 and t["output_tokens"] == 0  # nothing spent

def test_unknown_run(engine):
    with pytest.raises(LookupError):
        audit_run(engine, "nope")

def test_persistent_hit_reports_tokens_avoided_from_the_cache_entry(engine):
    from water_source_research.db.observability_models import LLMResponseCacheRecord
    add_job(engine, result={"events": [node(1, "water_type", 1, claims=1, requests=0,
                                            tokens=(0, 0)),
                                       node(2, "water_source", 1, claims=1, requests=0,
                                            tokens=(0, 0))]})
    with Session(engine) as s, s.begin():
        s.add(LLMResponseCacheRecord(
            cache_key="k1", provider="vt", generation_hash="g", task="extract",
            prompt_version="p", schema_hash="s", context={}, prompt_hash="h", response={},
            requests=1, input_tokens=900, output_tokens=120, hit_count=1))
        hit = ledger("water_type", None, None, None, hit=True)
        hit.cache_layer, hit.cache_source = "persistent", "k1"
        memory = ledger("water_source", None, None, None, hit=True)
        memory.cache_layer = "memory"
        s.add_all([hit, memory])
    t = audit_run(engine, "run-1")["total"]
    assert (t["app_cache_hits"], t["input_tokens"], t["output_tokens"]) == (2, 0, 0)
    assert (t["tokens_avoided"], t["avoided_unknown_hits"]) == (1020, 1)
