"""Context Manager: build the smallest evidence context for one research task.

- ``manager``: pure, in-memory bundle construction (ranking, dedupe, budgeting, provenance).
- ``store``: durable, concurrency-safe bundle reuse (``context_bundles`` table).
- ``service``: graph-facing entry point, (run_id, field_key, operation) -> ContextBundle.

Never calls a model; LLM response caching belongs in the LLM layer.
"""

from .manager import (
    DEFAULT_TOKEN_BUDGETS,
    RETRIEVAL_POLICY_VERSION,
    ContextBundle,
    ContextItem,
    ContextManager,
    ContextPurpose,
    ContextStatus,
    ContextTask,
    ProcedureHint,
    SourceRef,
    estimate_tokens,
)
from .service import ContextService, load_run_state
from .store import ContextBundleStore

__all__ = [
    "DEFAULT_TOKEN_BUDGETS", "RETRIEVAL_POLICY_VERSION", "ContextBundle", "ContextBundleStore",
    "ContextItem", "ContextManager", "ContextPurpose", "ContextService", "ContextStatus",
    "ContextTask", "ProcedureHint", "SourceRef", "estimate_tokens", "load_run_state",
]
