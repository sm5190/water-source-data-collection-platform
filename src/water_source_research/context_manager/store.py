"""Durable, concurrency-safe reuse of ContextBundles.

A bundle is identified by ``ContextManager.cache_key`` (policy + exactly the inputs the build
reads). Creation is safe at two levels:

- in-process: a per-key lock (single-flight) so concurrent callers build a bundle once;
- across processes: the unique constraint on ``cache_key``. A worker that loses the insert
  race discards its own build and returns the stored winner, so every caller sees the same
  ``bundle_id``.

This module never calls a model. LLM response caching belongs in the LLM layer, keyed by
``bundle.fingerprint`` (or ``bundle_id``).
"""

import asyncio
import threading
import weakref
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from water_source_research.db.models import ContextBundleRecord, utcnow
from water_source_research.observability.otel import get_tracer
from water_source_research.schemas.research import CityResearchState, EvidenceChunk
from .manager import ContextBundle, ContextManager, ContextTask

class _KeyLock:
    __slots__ = ("__weakref__", "lock")

    def __init__(self) -> None:
        self.lock = threading.Lock()

class ContextBundleStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._locks: weakref.WeakValueDictionary[str, _KeyLock] = weakref.WeakValueDictionary()
        self._locks_guard = threading.Lock()

    def get_or_build(
        self,
        manager: ContextManager,
        task: ContextTask,
        state: CityResearchState,
        chunks: Sequence[EvidenceChunk | Mapping[str, Any]] = (),
        seen: Iterable[tuple[str, str]] | None = None,
    ) -> ContextBundle:
        chunks = list(chunks)
        seen_set = set(seen or ())
        key = manager.cache_key(task, state, chunks, seen_set)
        with get_tracer().start_as_current_span("context.bundle_create") as span:
            span.set_attribute("context.cache_key", key)
            bundle = self._hit(key)
            if bundle is None:
                key_lock = self._lock_for(key)
                with key_lock.lock:
                    bundle = self._hit(key)  # another thread may have finished meanwhile
                    if bundle is None:
                        built = manager.build(task, state, chunks, seen_set)
                        bundle = self._insert(key, manager.policy_version, state.city_name,
                                              built)
            span.set_attribute("context.cache_hit", bundle.cache_hit)
            span.set_attribute("context.bundle_id", bundle.bundle_id or "")
            span.set_attribute("context.status", bundle.status.value)
            return bundle

    async def aget_or_build(
        self,
        manager: ContextManager,
        task: ContextTask,
        state: CityResearchState,
        chunks: Sequence[EvidenceChunk | Mapping[str, Any]] = (),
        seen: Iterable[tuple[str, str]] | None = None,
    ) -> ContextBundle:
        """For async graph nodes: runs the blocking DB work off the event loop."""
        return await asyncio.to_thread(self.get_or_build, manager, task, state, chunks, seen)

    def get(self, cache_key: str) -> ContextBundle | None:
        with Session(self.engine) as session:
            rec = session.scalar(
                select(ContextBundleRecord).where(ContextBundleRecord.cache_key == cache_key)
            )
            return _to_bundle(rec, cache_hit=True) if rec else None

    def get_by_id(self, bundle_id: str) -> ContextBundle | None:
        with Session(self.engine) as session:
            rec = session.get(ContextBundleRecord, bundle_id)
            return _to_bundle(rec, cache_hit=True) if rec else None

    def delete_unused_since(self, cutoff: datetime) -> int:
        """Retention: drop bundles not reused since ``cutoff``. Returns the number deleted."""
        with Session(self.engine) as session, session.begin():
            result = session.execute(
                delete(ContextBundleRecord).where(ContextBundleRecord.last_used_at < cutoff)
            )
            return result.rowcount or 0

    def _lock_for(self, key: str) -> _KeyLock:
        with self._locks_guard:
            key_lock = self._locks.get(key)
            if key_lock is None:
                key_lock = _KeyLock()
                self._locks[key] = key_lock
            return key_lock

    def _hit(self, key: str) -> ContextBundle | None:
        # Write first, then read: taking the write lock up front avoids lock-upgrade
        # deadlocks on SQLite and keeps hit accounting atomic on every backend.
        with Session(self.engine) as session, session.begin():
            result = session.execute(
                update(ContextBundleRecord)
                .where(ContextBundleRecord.cache_key == key)
                .values(hit_count=ContextBundleRecord.hit_count + 1, last_used_at=utcnow())
            )
            if not result.rowcount:
                return None
            rec = session.scalar(
                select(ContextBundleRecord).where(ContextBundleRecord.cache_key == key)
            )
            return _to_bundle(rec, cache_hit=True) if rec else None

    def _insert(
        self, key: str, policy_version: str, city_name: str, built: ContextBundle
    ) -> ContextBundle:
        now = utcnow()
        bundle_id = str(uuid4())
        rec = ContextBundleRecord(
            id=bundle_id,
            cache_key=key,
            policy_version=policy_version,
            city_name=city_name,
            purpose=built.purpose,
            target_fields=built.target_fields,
            fingerprint=built.fingerprint,
            token_estimate=built.stats.get("tokens_used", 0),
            payload=built.to_dict(),
            hit_count=0,
            created_at=now,
            last_used_at=now,
        )
        try:
            with Session(self.engine) as session, session.begin():
                session.add(rec)
        except IntegrityError:
            # Lost the race to another process: reuse its bundle so all callers agree.
            winner = self._hit(key)
            if winner is None:
                raise
            return winner
        return replace(built, bundle_id=bundle_id, cache_key=key, cache_hit=False)

def _to_bundle(rec: ContextBundleRecord, cache_hit: bool) -> ContextBundle:
    return ContextBundle.from_dict(
        rec.payload, bundle_id=rec.id, cache_key=rec.cache_key, cache_hit=cache_hit
    )
