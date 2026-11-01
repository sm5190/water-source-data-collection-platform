"""Discovery audit: rounds from the event sequence, site-restricted targets, the pre-Milestone-3
utility-host rule re-derived per round, and extraction calls linked to the documents they were
shown (through the stored bundle's sources)."""

import pytest
from procedure_helpers import make_engine
from sqlalchemy.orm import Session
from water_source_research.db.evidence_models import DiscoveryResultRecord, SourceDocumentRecord
from water_source_research.db.models import ContextBundleRecord
from water_source_research.db.research_models import ResearchJobRecord
from water_source_research.evaluation.discovery_audit import (
    audit_run, domain_signals, render_markdown, search_calls, site_target,
)

def search(query, provider="searxng"):
    return {"kind": "TOOL_CALL", "node": "discover", "provider": provider,
            "strategy": "web_search", "result": "SUCCESS",
            "attrs": {"query": query, "sources_found": 5, "sources_retained": 3}}

def fetch():
    return {"kind": "TOOL_CALL", "node": "acquire", "strategy": "direct_http", "attrs": {}}

def extract(field, rnd, digest, tokens=(100, 10), claims=0):
    return {"kind": "NODE", "node": "extract", "field_key": field, "strategy": "READY",
            "attrs": {"bundle_status": "READY", "discovery_round": rnd, "claims": claims,
                      "llm_requests": 1, "cache_hits": 0, "prompt_tokens": tokens[0],
                      "completion_tokens": tokens[1], "context_digest": digest}}

EVENTS = [
    search("Santiago Chile drinking water utility"), search("Santiago Chile water sources"),
    fetch(), fetch(),
    extract("utility_name", 1, "aaaaaaaaaaaaaaaa", claims=1),
    search("site:almarwater.com water sources"), search("Santiago producción de agua"),
    fetch(),
    extract("water_source", 2, "bbbbbbbbbbbbbbbb", tokens=(300, 30)),
]

def doc(i, rnd, url, source_type, query, status="OK"):
    return SourceDocumentRecord(id=i, run_id="run-1", round=rnd, requested_url=url,
                                canonical_url=url, final_url=url, title=f"t{i}",
                                source_type=source_type, status=status, chunk_count=2,
                                discovery_query=query, discovery_provider="searxng")

def bundle(fp, urls):
    sources = {f"S{n}": {"source_id": f"S{n}", "url": u, "title": None, "page_number": None,
                         "retrieved_at": None, "passages": []} for n, u in enumerate(urls, 1)}
    items = [{"kind": "evidence", "text": "x", "source_id": s, "field_keys": ["f"]}
             for s in sources]
    return ContextBundleRecord(id=fp[:8], cache_key=fp, policy_version="5", city_name="Santiago",
                               purpose="EXTRACT", target_fields=["f"], fingerprint=fp,
                               token_estimate=10,
                               payload={"items": items, "sources": sources})

ALMAR = "https://almarwater.com/news-santiago-event/"
GOV_CO = "https://www.minvivienda.gov.co/informe.pdf"
NEWS = "https://www.riotimesonline.com/aguas-andinas/"

@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path, "disc.db")
    with Session(engine) as s, s.begin():
        s.add(ResearchJobRecord(run_id="run-1", city_name="Santiago", country="Chile",
                                city_key="santiago|chile", status="AWAITING_REVIEW",
                                max_attempts=3, mode="RESEARCH", result={"events": EVENTS}))
        s.add_all([doc(1, 1, ALMAR, "UTILITY", "Santiago Chile drinking water utility"),
                   doc(2, 1, NEWS, "OTHER", "Santiago Chile water sources"),
                   doc(3, 2, GOV_CO, "GOVERNMENT", "Santiago producción de agua")])
        s.add(DiscoveryResultRecord(run_id="run-1", round=1, call_index=0, result_index=0,
                                    provider="searxng", strategy="web_search",
                                    query="Santiago Chile drinking water utility", url=ALMAR,
                                    canonical_url=ALMAR, retained=True))
        s.add_all([bundle("a" * 64, [ALMAR, NEWS]), bundle("b" * 64, [GOV_CO])])
    return engine

def test_rounds_follow_the_event_sequence():
    calls = search_calls(EVENTS)
    assert [c["round"] for c in calls] == [1, 1, 2, 2]
    assert calls[2]["site_target"] == "almarwater.com"

def test_site_target_and_domain_signals():
    assert site_target("site:www.Example.org water quality") == "example.org"
    assert site_target("Santiago water") is None
    assert domain_signals("minvivienda.gov.co") == {"tld": "co", "cctld": "co",
                                                    "government_domain": True}
    assert domain_signals("almarwater.com")["cctld"] is None

def test_utility_hosts_re_derived_per_round_and_calls_linked(engine):
    r = audit_run(engine, "run-1")
    assert r["rounds"] == 2 and r["searches"] == 4
    assert r["site_queries"] == {"almarwater.com": 1}
    by = {b["round"]: b for b in r["by_round"]}
    assert by[1]["utility_hosts_at_start"] == []
    assert by[2]["utility_hosts_at_start"] == ["almarwater.com"]  # classification alone
    assert by[2]["cctlds"] == {"co": 1}
    docs = {d["host"]: d for d in r["documents"]}
    assert docs["almarwater.com"]["calls_fed"] == 1
    assert docs["almarwater.com"]["sole_source_calls"] == 0  # shared with the news page
    assert docs["minvivienda.gov.co"]["sole_source_calls"] == 1
    assert docs["minvivienda.gov.co"]["tokens_in_calls_fed"] == 330
    assert r["calls_linked_to_documents"] == 2 and r["calls_without_stored_bundle"] == 0
    md = render_markdown({"runs": [r]})
    assert "almarwater.com ×1" in md and "| 2 |" in md

def test_unknown_run(engine):
    with pytest.raises(LookupError):
        audit_run(engine, "nope")

def test_runs_without_digests_link_by_time_window_when_counts_match(tmp_path):
    from datetime import UTC, datetime, timedelta
    t0 = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)
    events = [search("q1"), fetch(),
              {**extract("water_source", 1, None), "attrs": {
                  **extract("water_source", 1, None)["attrs"], "context_digest": None}},
              {**extract("utility_name", 1, None), "attrs": {
                  **extract("utility_name", 1, None)["attrs"], "context_digest": None}}]
    engine = make_engine(tmp_path, "w.db")
    with Session(engine) as s, s.begin():
        s.add(ResearchJobRecord(run_id="run-2", city_name="Santiago", country="Chile",
                                city_key="santiago|chile", status="SUCCEEDED", max_attempts=3,
                                mode="RESEARCH", result={"events": events}, created_at=t0,
                                updated_at=t0 + timedelta(minutes=5),
                                finished_at=t0 + timedelta(minutes=5)))
        s.add(doc(1, 1, ALMAR, "UTILITY", "q1"))

    with Session(engine) as s, s.begin():
        d = s.get(SourceDocumentRecord, 1)
        d.run_id = "run-2"
        b1 = bundle("c" * 64, [ALMAR])
        b1.target_fields, b1.created_at = ["water_source"], t0 + timedelta(minutes=1)
        b2 = bundle("d" * 64, [ALMAR])  # two bundles for one utility_name slot: not linked
        b2.target_fields, b2.created_at = ["utility_name"], t0 + timedelta(minutes=2)
        b3 = bundle("e" * 64, [ALMAR])
        b3.target_fields, b3.created_at = ["utility_name"], t0 + timedelta(minutes=3)
        late = bundle("f" * 64, [ALMAR])  # outside the run's window
        late.target_fields, late.created_at = ["water_source"], t0 + timedelta(hours=1)
        s.add_all([b1, b2, b3, late])
    r = audit_run(engine, "run-2")
    assert r["linkage"] == "time_window"
    assert r["calls_linked_to_documents"] == 1 and r["calls_without_stored_bundle"] == 1
    assert r["documents"][0]["fields_fed"] == ["water_source"]
