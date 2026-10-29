"""Persistent application cache of validated model responses (table llm_response_cache).

The second cache tier behind the router's in-process cache, so identical extractions are not
paid for again after a crash and retry, on a replay or repeated run, or by another worker.
Configured once per process like the usage ledger (``configure_response_store(engine)``);
without it, or with ``LLM__PERSISTENT_CACHE=off``, nothing is read or written.

Only callers that pass a ``cache_context`` (the inputs the prompt was built from that the
router can't see: field definition, research scope, evidence content hashes, retrieval
policy) use it; today that is extraction. A stored response is the validated raw model
output only; the caller re-grounds it against its own run's evidence. Failures to read or
write are logged and never fail a call (the model is asked instead).
"""

import logging
from datetime import UTC, datetime
from typing import Any
from sqlalchemy import Engine, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from water_source_research.db.observability_models import LLMResponseCacheRecord

log = logging.getLogger(__name__)

class ResponseStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, key: str) -> dict[str, Any] | None:
        """The stored entry (response plus the original call's usage), counting the hit."""
        try:
            with Session(self.engine) as session, session.begin():
                row = session.get(LLMResponseCacheRecord, key)
                if row is None:
                    return None
                session.execute(update(LLMResponseCacheRecord)
                                .where(LLMResponseCacheRecord.cache_key == key)
                                .values(hit_count=LLMResponseCacheRecord.hit_count + 1,
                                        last_hit_at=datetime.now(UTC)))
                return {"response": row.response, "input_tokens": row.input_tokens,
                        "output_tokens": row.output_tokens, "requests": row.requests}
        except Exception:  # noqa: BLE001 - a cache read never fails the call
            log.warning("persistent LLM cache read failed", exc_info=True)
            return None

    def put(self, key: str, components: dict[str, Any], response: dict[str, Any],
            usage: dict[str, Any]) -> None:
        """Insert-or-skip (two processes may answer the same prompt; the first one stays)."""
        try:
            with Session(self.engine) as session, session.begin():
                if session.get(LLMResponseCacheRecord, key) is not None:
                    return
                session.add(LLMResponseCacheRecord(
                    cache_key=key, response=response, hit_count=0, **components, **usage))
        except IntegrityError:
            pass
        except Exception:  # noqa: BLE001
            log.warning("persistent LLM cache write failed", exc_info=True)

_store: ResponseStore | None = None

def configure_response_store(engine: Engine | None) -> None:
    """Use ``engine`` for the persistent tier in this process (None turns it off)."""
    global _store
    _store = ResponseStore(engine) if engine is not None else None

def response_store() -> ResponseStore | None:
    return _store
