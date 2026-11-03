"""Discovery v2 (DISCOVERY_POLICY_VERSION discovery-2): identity place terms, searches steered
by the utility registry (verified hosts and names, verification of candidates, predecessors),
a government/regulator fallback, upstream features, dropping ineffective topics, conservative
host suppression, and acquisition/ranking that put same-named places last."""

import pytest
from discovery_helpers import web
from water_source_research.acquisition.config import AcquisitionSettings
from water_source_research.acquisition.service import select_candidates
from water_source_research.agents.nodes import _record_suppression, suppressed_hosts
from water_source_research.discovery.queries import (
    DiscoveryPlan, build_queries, ineffective_topics, place_terms, place_variants,
)
from water_source_research.discovery.ranking import merge_and_rank
from water_source_research.services.city_identity import load_city_identities
from water_source_research.services.field_state import initialize_city_state, load_field_definitions

FIELDS = load_field_definitions()
BY = {(i.city, i.country): i for i in load_city_identities()}

def queries(plan, keys=("utility_name", "water_source", "total_city_water_withdrawals_m3_day"),
            city="Santiago", country="Chile", **kw):
    return [q.query for q in build_queries(city=city, country=country, utility=None,
                                           field_keys=list(keys), fields=FIELDS, plan=plan,
                                           max_per_kind=12, **kw) if q.kind == "web"]

# --- place terms --------------------------------------------------------------------------

def test_place_variants_are_natural_and_always_disambiguate():
    """discovery-3 (F7): no "Santiago Santiago ..."; a same-country homonym always gets the
    region; foreign homonyms only need the country, with region / own qualified name as
    variants; small homonyms only: city + country."""
    santiago = place_variants(BY[("Santiago", "Chile")])
    assert santiago == ("Santiago Chile", "Santiago Metropolitan Region Chile",
                        "Santiago de Chile")
    assert not any("Santiago Santiago" in v for v in santiago)
    assert place_variants(BY[("Hyderabad", "India")]) == (
        "Hyderabad India", "Hyderabad Telangana India")
    houston = place_variants(BY[("Houston", "United States of America")])
    assert houston == ("Houston Texas", "Houston Texas United States of America")
    assert all("Texas" in v for v in houston)  # Houston, Mississippi shares the country
    assert place_variants(BY[("Vienna", "Austria")]) == ("Vienna Austria",)
    assert place_terms(BY[("Santiago", "Chile")]) == "Santiago Chile"

def test_local_language_variants_use_local_names():
    assert place_variants(BY[("Santiago", "Chile")], "es") == ("Santiago de Chile",)
    assert place_variants(BY[("Vienna", "Austria")], "de") == ("Wien Austria",)

def test_field_types_rotate_through_variants():
    plan = DiscoveryPlan(place="Santiago Chile", places=(
        "Santiago Chile", "Santiago Metropolitan Region Chile", "Santiago de Chile"),
        local_place="Santiago de Chile")
    qs = queries(plan)
    assert "Santiago Chile drinking water utility" in qs
    assert any(q.startswith("Santiago Metropolitan Region Chile ") for q in qs)
    assert any(q.startswith("Santiago de Chile ") for q in qs)

def test_local_language_queries_name_the_place():
    qs = queries(DiscoveryPlan(), keys=("water_source",))
    local = [q for q in qs if "abastecimiento" in q]
    assert local and all(q.startswith("Santiago Chile ") for q in local)

# --- searches from the registry ----------------------------------------------------------

def test_site_queries_only_for_verified_hosts_without_place_terms():
    plan = DiscoveryPlan(place="Santiago Santiago Metropolitan Region Chile",
                         verified_hosts=("aguasandinas.cl",), verified_names=("Aguas Andinas",))
    qs = queries(plan)
    site = [q for q in qs if q.startswith("site:")]
    assert site and all(q.startswith("site:aguasandinas.cl ") and "Santiago" not in q
                        for q in site)
    assert '"Aguas Andinas" water sources' in qs
    assert not any(q.startswith("site:") for q in queries(DiscoveryPlan()))

def test_candidates_get_at_most_two_verification_queries():
    plan = DiscoveryPlan(place="Santiago Chile", candidate_names=("A Water", "B Aguas", "C"))
    qs = [q for q in queries(plan) if q.endswith("drinking water supply")]
    assert qs == ['"A Water" Santiago Chile drinking water supply',
                  '"B Aguas" Santiago Chile drinking water supply']

def test_no_verified_utility_falls_back_to_public_sources():
    qs = queries(DiscoveryPlan(place="Santiago Chile", fallback=True))
    assert "Santiago Chile drinking water regulator report" in qs
    assert "Santiago Chile government water supply report" in qs
    assert any("regulador servicios sanitarios" in q for q in qs)
    assert not any("regulator report" in q for q in queries(DiscoveryPlan(fallback=False)))

def test_upstream_and_predecessor_queries():
    plan = DiscoveryPlan(features=("Maipo River",), predecessor_names=("EMOS",))
    qs = queries(plan)
    assert "Maipo River water supply" in qs  # the city isn't required upstream
    assert '"EMOS" Santiago water production history' in qs

def test_a_topic_that_twice_retained_nothing_is_dropped():
    events = [{"kind": "TOOL_CALL", "node": "discover",
               "attrs": {"query": f"x{i} drinking water regulator report",
                         "sources_retained": 0}} for i in range(2)]
    skip = ineffective_topics(events, ["drinking water regulator report"])
    assert skip == {"drinking water regulator report"}
    qs = queries(DiscoveryPlan(place="Santiago Chile", fallback=True, skip_topics=skip))
    assert "Santiago Chile drinking water regulator report" not in qs

# --- conservative host suppression ---------------------------------------------------------

def _state(docs, chunks, registry=None):
    return {"research_state": initialize_city_state("Santiago", "Chile").model_dump(mode="json"),
            "acquired_documents": docs, "evidence_chunks": chunks,
            "utility_registry": registry or {"candidates": []}, "events": []}

def _doc(url, kind, source_type="OTHER"):
    return {"status": "OK", "final_url": url, "publisher_kind": kind, "source_type": source_type}

def _chunk(url, text, cid):
    return {"chunk_id": cid, "url": url, "text": text, "title": "", "source_type": "OTHER"}

def test_a_commercial_host_with_two_irrelevant_pages_is_suppressed_with_a_reason():
    a, b = "https://almarwater.com/a", "https://almarwater.com/b"
    state = _state([_doc(a, "COMMERCIAL"), _doc(b, "COMMERCIAL")],
                   [_chunk(a, "Desalination for industry worldwide.", "1"),
                    _chunk(b, "Our ten years of water infrastructure.", "2")])
    assert "almarwater.com" in suppressed_hosts(state)
    out = _record_suppression(state, 2)
    assert out["suppressed_hosts"] == ["almarwater.com"]
    [e] = [e for e in out["events"] if e["kind"] == "HOST_SUPPRESSED"]
    assert e["strategy"] == "suppress" and "none about the city" in e["attrs"]["reason_code"]

def test_government_and_academic_hosts_are_never_suppressed():
    urls = [f"https://www.ins.gov.co/r{i}.pdf" for i in range(3)]
    docs = [_doc(u, "GOVERNMENT", "GOVERNMENT") for u in urls]
    state = _state(docs, [_chunk(u, "Informe nacional, Bogotá.", str(i))
                          for i, u in enumerate(urls)])
    assert suppressed_hosts(state) == {}

def test_a_non_serving_utility_host_is_suppressed_and_relevance_releases_it():
    a = "https://almarwater.com/a"
    registry = {"candidates": [{"name": "Almar Water Solutions", "status": "NON_SERVING",
                                "bound_hosts": ["almarwater.com"]}]}
    state = _state([_doc(a, "COMMERCIAL")], [_chunk(a, "We build plants.", "1")], registry)
    assert "almarwater.com" in suppressed_hosts(state)
    later = _state([_doc(a, "COMMERCIAL")],
                   [_chunk(a, "Santiago, Chile: we build plants.", "1")], registry)
    later["suppressed_hosts"] = ["almarwater.com"]
    out = _record_suppression(later, 3)
    assert out["suppressed_hosts"] == []
    assert [e["strategy"] for e in out["events"] if e["kind"] == "HOST_SUPPRESSED"] == [
        "release"]

# --- acquisition and ranking ----------------------------------------------------------------

def test_suppressed_hosts_are_skipped_and_other_places_come_last_one_per_round():
    cands = [{"url": "https://almarwater.com/x", "source_type": "UTILITY"},
             {"url": "https://www.minvivienda.gov.co/a.pdf", "source_type": "GOVERNMENT",
              "geo_scope": "OTHER_COUNTRY"},
             {"url": "https://www.ins.gov.co/b.pdf", "source_type": "GOVERNMENT",
              "geo_scope": "OTHER_COUNTRY"},
             {"url": "https://www.dga.mop.gob.cl/c", "source_type": "GOVERNMENT"},
             {"url": "https://example.org/maipo", "source_type": "OTHER",
              "geo_status": "HOMONYM", "title": "Maipo River flows"}]
    chosen = [c["url"] for c in select_candidates(cands, set(), AcquisitionSettings(), 6,
                                                   skip_hosts=["almarwater.com"],
                                                   features=["Maipo River"])]
    assert "https://almarwater.com/x" not in chosen
    assert chosen[0] == "https://www.dga.mop.gob.cl/c"
    assert "https://example.org/maipo" in chosen  # names a known water feature
    assert sum(u.endswith(".pdf") for u in chosen) == 1  # one other-country page per round

@pytest.mark.usefixtures("identity_approved")
def test_ranking_demotes_a_homonym_within_its_tier():
    cali = web("Agua potable en Santiago de Cali", "https://www.example.org/cali", 1,
               snippet="Santiago de Cali, Colombia", source_type="TECHNICAL")
    chile = web("Agua potable en Santiago", "https://www.example.org/chile", 2,
                snippet="Santiago de Chile", source_type="TECHNICAL")
    ranked = merge_and_rank([cali, chile], "Santiago", country="Chile")
    assert [r.url for r in ranked] == ["https://www.example.org/chile",
                                       "https://www.example.org/cali"]
    assert ranked[1].geo_status == "HOMONYM"

def test_ranking_is_unchanged_without_an_identity():
    cali = web("Agua potable en Santiago de Cali", "https://www.example.org/cali", 1,
               source_type="TECHNICAL")
    chile = web("Agua potable en Santiago", "https://www.example.org/chile", 2,
                source_type="TECHNICAL")
    ranked = merge_and_rank([cali, chile], "Santiago", country="Chile")
    assert ranked[0].url == "https://www.example.org/cali" and ranked[0].geo_status is None

# --- Milestone 3.1 (discovery-3): verification independent of the utility field ------------

def test_verification_has_a_reserved_slot_when_utility_name_is_not_planned():
    """F6, Santiago run b844390c: a round-1 utility_name claim took the field out of the plan,
    so "Aguas Andinas" was never verified."""
    plan = DiscoveryPlan(place="Santiago Chile", candidate_names=("Aguas Andinas",))
    planned = build_queries(city="Santiago", country="Chile", utility=None,
                            field_keys=["water_source", "total_city_water_withdrawals_m3_day"],
                            fields=FIELDS, plan=plan, max_per_kind=2)
    web_q = [q for q in planned if q.kind == "web"]
    assert len(web_q) == 2  # the reserved slot counts toward the budget
    assert web_q[0].query == '"Aguas Andinas" Santiago Chile drinking water supply'
    assert web_q[0].field_key == "utility_name"

def test_verification_moves_to_the_local_language_once_the_english_query_ran():
    plan = DiscoveryPlan(place="Santiago Chile", candidate_names=("Aguas Andinas",))
    ran = {'"Aguas Andinas" Santiago Chile drinking water supply'}
    qs = queries(plan, keys=("water_source",), already_run=ran)
    assert '"Aguas Andinas" Santiago Chile empresa sanitaria concesionaria agua potable ' \
           'área de concesión' in qs

def test_no_candidates_no_verification_queries():
    qs = queries(DiscoveryPlan(place="Santiago Chile"), keys=("water_source",))
    assert not any(q.startswith('"') for q in qs)

def test_ineffective_topics_count_each_search_once_and_match_the_topic_suffix():
    """discovery-3: one query sent to two academic providers is one miss, not two; a miss on
    "... drinking water quality report" isn't charged to "drinking water quality"; failed
    calls say nothing about the topic."""
    def ev(query, retained=0, result="NO_RESULT", provider="openalex"):
        return {"kind": "TOOL_CALL", "node": "discover", "provider": provider,
                "result": result, "attrs": {"query": query, "sources_retained": retained}}
    topics = ["drinking water quality", "drinking water quality report"]
    both = [ev("Santiago Chile drinking water quality"),
            ev("Santiago Chile drinking water quality", provider="semantic_scholar")]
    assert ineffective_topics(both, topics) == set()
    longer = [ev(f"{p} drinking water quality report") for p in ("Santiago", "Maipú")]
    assert ineffective_topics(longer, topics) == {"drinking water quality report"}
    failed = [ev(f"{p} drinking water quality", result="FAILURE") for p in ("A", "B")]
    assert ineffective_topics(failed, topics) == set()
    one_kept = [ev("A drinking water quality"),
                ev("A drinking water quality", retained=2, provider="semantic_scholar"),
                ev("B drinking water quality")]
    assert ineffective_topics(one_kept, topics) == set()
