import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from water_source_research.db.models import ContextBundleRecord, utcnow
from water_source_research.db.session import init_db
from water_source_research.schemas.common import FieldResolutionStatus
from water_source_research.schemas.research import EvidenceChunk, EvidenceRef, FieldClaim
from water_source_research.context_manager import (
    ContextBundleStore, ContextManager, ContextPurpose, ContextTask,
)
from water_source_research.services.field_state import (
    initialize_city_state, load_field_definitions,
)

UTILITY_TEXT = (
    "Toronto Water is the municipal utility responsible for drinking water in Toronto. "
    "It operates four treatment plants drawing from Lake Ontario."
)
EXTRACT = ContextTask(ContextPurpose.EXTRACT, ["utility_name", "water_source"])

@pytest.fixture
def engine(tmp_path):
    # File-backed so separate connections (threads, simulated processes) share one database.
    eng = create_engine(
        f"sqlite:///{tmp_path / 'ctx.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    init_db(eng)
    yield eng
    eng.dispose()

@pytest.fixture
def store(engine):
    return ContextBundleStore(engine)

@pytest.fixture
def state():
    return initialize_city_state("Toronto", "Canada")

@pytest.fixture
def chunks():
    return [
        EvidenceChunk(chunk_id="util", url="https://toronto.ca/water", text=UTILITY_TEXT,
                      title="Toronto Water", source_type="UTILITY"),
        EvidenceChunk(chunk_id="film", url="https://news.example/f",
                      text="The film festival is popular."),
    ]

def counting(manager):
    """Wrap manager.build so tests can assert how many times a bundle was actually built."""
    calls = []
    original = manager.build

    def build(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    manager.build = build
    return calls

def row_count(engine):
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(ContextBundleRecord))

def hit_count(engine, bundle_id):
    with Session(engine) as s:
        return s.get(ContextBundleRecord, bundle_id).hit_count

# --- repeated calls -------------------------------------------------------------------

def test_repeated_call_reuses_stored_bundle(store, engine, state, chunks):
    mgr = ContextManager()
    calls = counting(mgr)
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    second = store.get_or_build(mgr, EXTRACT, state, chunks)
    assert not first.cache_hit and second.cache_hit
    assert first.bundle_id == second.bundle_id
    assert first.render() == second.render()
    assert first.fingerprint == second.fingerprint
    assert len(calls) == 1
    assert row_count(engine) == 1
    assert hit_count(engine, first.bundle_id) == 1

def test_reuse_survives_restart(engine, state, chunks):
    first = ContextBundleStore(engine).get_or_build(ContextManager(), EXTRACT, state, chunks)
    # Fresh store + manager, as a new worker process would have.
    mgr = ContextManager()
    calls = counting(mgr)
    again = ContextBundleStore(engine).get_or_build(mgr, EXTRACT, state, chunks)
    assert again.cache_hit and again.bundle_id == first.bundle_id
    assert calls == []

def test_reloaded_bundle_keeps_provenance(store, state, chunks):
    built = store.get_or_build(ContextManager(), EXTRACT, state, chunks)
    loaded = store.get_by_id(built.bundle_id)
    assert loaded.consumed == built.consumed == {("util", "utility_name"),
                                                 ("util", "water_source")}
    assert loaded.evidence_ref("S1") == built.evidence_ref("S1")
    assert loaded.skipped_fields == built.skipped_fields
    assert loaded.stats == built.stats

def test_chunk_order_and_dict_input_do_not_change_key(store, state, chunks):
    mgr = ContextManager()
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    as_dicts = [c.model_dump() for c in reversed(chunks)]
    assert store.get_or_build(mgr, EXTRACT, state, as_dicts).bundle_id == first.bundle_id

# --- concurrent creation --------------------------------------------------------------

def test_concurrent_creation_in_one_process_builds_once(store, engine, state, chunks):
    mgr = ContextManager()
    calls = counting(mgr)
    n = 16
    barrier = threading.Barrier(n)

    def worker(_):
        barrier.wait()
        return store.get_or_build(mgr, EXTRACT, state, chunks)

    with ThreadPoolExecutor(n) as pool:
        bundles = list(pool.map(worker, range(n)))
    assert len({b.bundle_id for b in bundles}) == 1
    assert sum(not b.cache_hit for b in bundles) == 1
    assert len(calls) == 1
    assert row_count(engine) == 1

def test_concurrent_creation_across_processes_converges(engine, state, chunks):
    # Separate stores share no in-process lock, so only the DB unique constraint protects
    # creation. Several may build; exactly one row must win and everyone must return it.
    n = 8
    stores = [ContextBundleStore(engine) for _ in range(n)]
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        return stores[i].get_or_build(ContextManager(), EXTRACT, state, chunks)

    with ThreadPoolExecutor(n) as pool:
        bundles = list(pool.map(worker, range(n)))
    assert len({b.bundle_id for b in bundles}) == 1
    assert row_count(engine) == 1
    assert len({b.render() for b in bundles}) == 1

def test_lost_insert_race_returns_winner(engine, state, chunks):
    # Deterministic version of the cross-process race: while worker A is building, worker B
    # (another process, so a different store and lock) creates the same bundle first.
    store_a, store_b = ContextBundleStore(engine), ContextBundleStore(engine)
    mgr_a = ContextManager()
    original = mgr_a.build
    winner = {}

    def build_while_b_wins(*args, **kwargs):
        winner["bundle"] = store_b.get_or_build(ContextManager(), EXTRACT, state, chunks)
        return original(*args, **kwargs)

    mgr_a.build = build_while_b_wins
    result = store_a.get_or_build(mgr_a, EXTRACT, state, chunks)
    assert result.bundle_id == winner["bundle"].bundle_id
    assert result.cache_hit
    assert row_count(engine) == 1

def test_concurrent_async_callers(store, engine, state, chunks):
    mgr = ContextManager()
    calls = counting(mgr)

    async def run():
        return await asyncio.gather(*[
            store.aget_or_build(mgr, EXTRACT, state, chunks) for _ in range(10)
        ])

    bundles = asyncio.run(run())
    assert len({b.bundle_id for b in bundles}) == 1
    assert len(calls) == 1
    assert row_count(engine) == 1

def test_concurrent_different_keys_do_not_block_each_other(store, engine, state, chunks):
    tasks = [ContextTask(ContextPurpose.EXTRACT, [k]) for k in
             ("utility_name", "water_source", "water_system", "reservoir_names")]
    with ThreadPoolExecutor(len(tasks)) as pool:
        bundles = list(pool.map(
            lambda t: store.get_or_build(ContextManager(), t, state, chunks), tasks,
        ))
    assert len({b.bundle_id for b in bundles}) == len(tasks)
    assert row_count(engine) == len(tasks)

# --- evidence changes -----------------------------------------------------------------

def test_changed_evidence_text_builds_new_bundle(store, engine, state, chunks):
    mgr = ContextManager()
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    edited = [chunks[0].model_copy(update={"text": UTILITY_TEXT + " Updated 2026."}), chunks[1]]
    second = store.get_or_build(mgr, EXTRACT, state, edited)
    assert not second.cache_hit
    assert second.bundle_id != first.bundle_id
    assert "Updated 2026." in second.render()
    assert row_count(engine) == 2
    # The original evidence still maps to the original bundle.
    assert store.get_or_build(mgr, EXTRACT, state, chunks).bundle_id == first.bundle_id

def test_added_or_removed_chunk_builds_new_bundle(store, state, chunks):
    mgr = ContextManager()
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    extra = EvidenceChunk(chunk_id="new", url="https://x.org", text="Lake Ontario intake.")
    assert store.get_or_build(mgr, EXTRACT, state, [*chunks, extra]).bundle_id != first.bundle_id
    assert store.get_or_build(mgr, EXTRACT, state, chunks[:1]).bundle_id != first.bundle_id

def test_evidence_metadata_change_builds_new_bundle(store, state, chunks):
    mgr = ContextManager()
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    reclassified = [chunks[0].model_copy(update={"source_type": "OTHER"}), chunks[1]]
    assert store.get_or_build(mgr, EXTRACT, state, reclassified).bundle_id != first.bundle_id

def test_evidence_change_does_not_invalidate_non_evidence_bundles(store, state, chunks):
    mgr = ContextManager()
    plan = ContextTask(ContextPurpose.PLAN, ["utility_name"])
    first = store.get_or_build(mgr, plan, state, chunks)
    edited = [chunks[0].model_copy(update={"text": "different"})]
    again = store.get_or_build(mgr, plan, state, edited)
    assert again.cache_hit and again.bundle_id == first.bundle_id

def test_seen_changes_key_only_for_target_fields(store, state, chunks):
    mgr = ContextManager()
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    unrelated = {("util", "water_quality_notes")}
    assert store.get_or_build(mgr, EXTRACT, state, chunks, unrelated).bundle_id == first.bundle_id
    after = store.get_or_build(mgr, EXTRACT, state, chunks, first.consumed)
    assert after.bundle_id != first.bundle_id
    assert not [i for i in after.items if i.kind == "evidence"]

# --- state changes --------------------------------------------------------------------

def test_unrelated_field_resolution_keeps_bundle(store, state, chunks):
    mgr = ContextManager()
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    state.fields["water_quality_year"].status = FieldResolutionStatus.RESOLVED
    assert store.get_or_build(mgr, EXTRACT, state, chunks).bundle_id == first.bundle_id

def test_relevant_state_change_builds_new_bundle(store, state, chunks):
    mgr = ContextManager()
    first = store.get_or_build(mgr, EXTRACT, state, chunks)
    fs = state.fields["jurisdiction"]  # an anchor fact for every task
    fs.status = FieldResolutionStatus.RESOLVED
    fs.selected_claim = FieldClaim(
        field_key="jurisdiction", value="Ontario", confidence=0.95,
        evidence=[EvidenceRef(url="https://ontario.ca", passage="Toronto, Ontario")],
    )
    second = store.get_or_build(mgr, EXTRACT, state, chunks)
    assert second.bundle_id != first.bundle_id
    assert "- jurisdiction = Ontario [S1]" in second.render()

# --- retrieval-policy version changes -------------------------------------------------

def test_policy_version_change_builds_new_bundle(store, engine, state, chunks):
    v1 = store.get_or_build(ContextManager(policy_version="1"), EXTRACT, state, chunks)
    v2_mgr = ContextManager(policy_version="2")
    calls = counting(v2_mgr)
    v2 = store.get_or_build(v2_mgr, EXTRACT, state, chunks)
    assert not v2.cache_hit and v2.bundle_id != v1.bundle_id
    assert len(calls) == 1
    with Session(engine) as s:
        versions = set(s.scalars(select(ContextBundleRecord.policy_version)))
    assert versions == {"1", "2"}
    # Rolling back to v1 reuses the v1 bundle.
    assert store.get_or_build(ContextManager(policy_version="1"), EXTRACT, state,
                              chunks).bundle_id == v1.bundle_id

def test_policy_config_change_builds_new_bundle(store, state, chunks):
    base = store.get_or_build(ContextManager(), EXTRACT, state, chunks)
    capped = store.get_or_build(ContextManager(max_chunks_per_source=1), EXTRACT, state, chunks)
    dedupe = store.get_or_build(ContextManager(dedupe_threshold=0.5), EXTRACT, state, chunks)
    assert len({base.bundle_id, capped.bundle_id, dedupe.bundle_id}) == 3

def test_field_definition_change_builds_new_bundle(store, state, chunks):
    base = store.get_or_build(ContextManager(), EXTRACT, state, chunks)
    defs = load_field_definitions()
    defs["utility_name"] = defs["utility_name"].model_copy(
        update={"keywords": [*defs["utility_name"].keywords, "operator"]}
    )
    changed = store.get_or_build(ContextManager(field_definitions=defs), EXTRACT, state, chunks)
    assert changed.bundle_id != base.bundle_id

# --- retention ------------------------------------------------------------------------

def test_delete_unused_since(store, engine, state, chunks):
    store.get_or_build(ContextManager(), EXTRACT, state, chunks)
    assert store.delete_unused_since(utcnow() - timedelta(days=1)) == 0
    assert store.delete_unused_since(utcnow() + timedelta(seconds=1)) == 1
    assert row_count(engine) == 0
