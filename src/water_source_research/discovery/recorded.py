"""Search snapshots for controlled discovery experiments (Milestone 3 evaluation).

Live search results change over time, so a fresh-discovery comparison records what each
provider returned: ``RecordingProvider`` wraps a provider and appends every response to a
JSON-lines snapshot (provider, query, limit, timestamp, results, sha256 of the results).
``RecordedProvider`` serves a snapshot back: the same (provider, query) returns the recorded
results; a query the snapshot doesn't have is a *miss*: it raises ``ProviderError`` of kind
``NOT_RECORDED`` (the call is a failure whose outcome is unknown, never a search that found
nothing), or, with ``live``, is searched live and listed in ``live_misses``. Nothing here is evidence: results are discovery
candidates as before.

Enabled by ``DISCOVERY__RECORD_PATH`` / ``DISCOVERY__REPLAY_PATH``
(``discovery/service.py::build_discovery_service``).
"""

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from .base import DiscoveryProvider, SearchResult
from .http import ProviderError, ProviderErrorKind

_LOCK = threading.Lock()

def _key(provider: str, query: str) -> str:
    return f"{provider}|{' '.join(query.split()).casefold()}"

def results_digest(results: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()

class RecordingProvider:
    """A provider that appends every response to ``path`` (JSON lines)."""

    def __init__(self, inner: DiscoveryProvider, path: str | Path, run_label: str = ""):
        self.inner, self.path, self.run_label = inner, Path(path), run_label
        self.name = inner.name

    def __getattr__(self, item: str) -> Any:  # related(), settings, ... of the wrapped one
        return getattr(self.inner, item)

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        results = await self.inner.search(query, limit)
        rows = [r.to_dict() for r in results]
        line = {"provider": self.name, "query": query, "limit": limit,
                "searched_at": datetime.now(UTC).isoformat(), "run_label": self.run_label,
                "results": rows, "sha256": results_digest(rows)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return results

def load_snapshots(*paths: str | Path) -> dict[str, list[dict[str, Any]]]:
    """(provider|query) -> recorded results (the first recording of each query wins)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out.setdefault(_key(row["provider"], row["query"]), row["results"])
    return out

class RecordedProvider:
    """Serves recorded results; ``misses`` lists queries the snapshot doesn't have."""

    def __init__(self, name: str, snapshots: dict[str, list[dict[str, Any]]],
                 live: DiscoveryProvider | None = None):
        self.name, self.snapshots, self.live = name, snapshots, live
        self.misses: list[str] = []
        self.live_misses: list[str] = []
        self.hits = 0

    def __getattr__(self, item: str) -> Any:
        if self.live is not None:
            return getattr(self.live, item)
        raise AttributeError(item)

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        rows = self.snapshots.get(_key(self.name, query))
        if rows is not None:
            self.hits += 1
            return [SearchResult.from_dict(r) for r in rows[:limit]]
        self.misses.append(query)
        if self.live is not None:
            self.live_misses.append(query)
            return await self.live.search(query, limit)
        raise ProviderError(self.name, ProviderErrorKind.NOT_RECORDED, "query not in snapshot")
