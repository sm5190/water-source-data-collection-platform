"""Context-bundle persistence on Postgres (fixtures and skip logic in conftest.py)."""

import threading
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from water_source_research.db.models import ContextBundleRecord
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.context_manager import (
    ContextBundleStore, ContextManager, ContextPurpose, ContextTask,
)
from water_source_research.services.field_state import initialize_city_state

TASK = ContextTask(ContextPurpose.EXTRACT, ["utility_name", "water_source"])
CHUNKS = [EvidenceChunk(
    chunk_id="util", url="https://toronto.ca/water", source_type="UTILITY",
    text="Toronto Water is the municipal utility supplying drinking water from Lake Ontario.")]

def rows(engine):
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(ContextBundleRecord))

def test_repeated_call_reuses_jsonb_bundle(pg_engine):
    store, state = ContextBundleStore(pg_engine), initialize_city_state("Toronto", "Canada")
    first = store.get_or_build(ContextManager(), TASK, state, CHUNKS)
    second = store.get_or_build(ContextManager(), TASK, state, CHUNKS)
    assert not first.cache_hit and second.cache_hit
    assert first.bundle_id == second.bundle_id and first.render() == second.render()
    assert second.evidence_ref("S1").url == "https://toronto.ca/water"
    assert rows(pg_engine) == 1

def test_concurrent_creation_one_store_builds_once(pg_engine):
    store, state = ContextBundleStore(pg_engine), initialize_city_state("Toronto", "Canada")
    mgr, calls = ContextManager(), []
    original = mgr.build
    mgr.build = lambda *a, **k: calls.append(1) or original(*a, **k)
    barrier = threading.Barrier(16)

    def work(_):
        barrier.wait()
        return store.get_or_build(mgr, TASK, state, CHUNKS)

    with ThreadPoolExecutor(16) as pool:
        bundles = list(pool.map(work, range(16)))
    assert len({b.bundle_id for b in bundles}) == 1
    assert len(calls) == 1 and rows(pg_engine) == 1

def test_cross_process_race_converges_on_unique_key(pg_engine):
    state = initialize_city_state("Toronto", "Canada")
    stores = [ContextBundleStore(pg_engine) for _ in range(10)]  # no shared in-process lock
    barrier = threading.Barrier(len(stores))

    def work(i):
        barrier.wait()
        return stores[i].get_or_build(ContextManager(), TASK, state, CHUNKS)

    with ThreadPoolExecutor(len(stores)) as pool:
        bundles = list(pool.map(work, range(len(stores))))
    assert len({b.bundle_id for b in bundles}) == 1
    assert rows(pg_engine) == 1

def test_lost_insert_race_returns_winner_on_postgres(pg_engine):
    state = initialize_city_state("Toronto", "Canada")
    store_a, store_b = ContextBundleStore(pg_engine), ContextBundleStore(pg_engine)
    mgr_a, winner = ContextManager(), {}
    original = mgr_a.build

    def build_while_b_wins(*args, **kwargs):
        winner["b"] = store_b.get_or_build(ContextManager(), TASK, state, CHUNKS)
        return original(*args, **kwargs)

    mgr_a.build = build_while_b_wins
    result = store_a.get_or_build(mgr_a, TASK, state, CHUNKS)
    assert result.bundle_id == winner["b"].bundle_id and result.cache_hit
    assert rows(pg_engine) == 1
