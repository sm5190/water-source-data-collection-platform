"""Discovery service: run providers concurrently, isolate failures, merge, rank, verify.

Web queries go to SearXNG and academic queries to OpenAlex + Semantic Scholar, all at once.
Every (provider, query) call is isolated: a timeout, 429 or bad payload marks that call as
failed and the rest carry on. Crossref then verifies/enriches DOIs of the top candidates.
``discover`` never raises for provider problems.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from water_source_research.core.capabilities import PROVIDER_CAPABILITIES, Capability
from water_source_research.observability.otel import get_tracer
from .base import DiscoveryProvider, SearchResult
from .config import DiscoverySettings
from .crossref import CrossrefDiscovery
from .http import ProviderError
from .queries import PlannedQuery
from .ranking import dedupe_keys, merge_and_rank
from .semantic_scholar import SemanticScholarDiscovery

log = logging.getLogger(__name__)

OK, NO_RESULTS = "ok", "no_results"
SUCCESS, NO_RESULT, FAILURE = "SUCCESS", "NO_RESULT", "FAILURE"

@dataclass(slots=True)
class ProviderCall:
    """One provider request, reported as a trusted TOOL_CALL event by the discover node."""

    provider: str
    capability: Capability
    strategy: str  # web_search | scholarly_search | doi_verification | related_papers | <error>
    query: str | None
    field_key: str | None
    result: str  # SUCCESS | NO_RESULT | FAILURE
    sources_found: int = 0
    sources_retained: int = 0
    duration_ms: int = 0
    error: str | None = None
    results: list[SearchResult] = field(default_factory=list, repr=False)

@dataclass(slots=True)
class DiscoveryOutcome:
    candidates: list[SearchResult]
    calls: list[ProviderCall]
    provider_status: dict[str, str]

class DiscoveryService:
    def __init__(
        self,
        settings: DiscoverySettings,
        *,
        web: Sequence[DiscoveryProvider] = (),
        scholarly: Sequence[DiscoveryProvider] = (),
        crossref: CrossrefDiscovery | None = None,
    ) -> None:
        self.settings = settings
        self.web = list(web)
        self.scholarly = list(scholarly)
        self.crossref = crossref
        self.related = next((p for p in self.scholarly
                             if isinstance(p, SemanticScholarDiscovery)), None)

    @property
    def provider_names(self) -> list[str]:
        names = [p.name for p in (*self.web, *self.scholarly)]
        return names + (["crossref"] if self.crossref else [])

    async def discover(self, queries: Sequence[PlannedQuery], *, city: str,
                       country: str | None = None) -> DiscoveryOutcome:
        s = self.settings
        with get_tracer().start_as_current_span("discovery.discover") as span:
            span.set_attribute("discovery.queries", len(queries))
            jobs = [(p, q) for q in queries
                    for p in (self.web if q.kind == "web" else self.scholarly)]
            calls = list(await asyncio.gather(*(self._search(p, q) for p, q in jobs)))

            if self.related and s.semantic_scholar_related_per_round > 0:
                seeds = merge_and_rank([r for c in calls for r in c.results], city)
                calls.extend(await self._related(seeds, queries))

            found = [r for c in calls for r in c.results]
            ranked = merge_and_rank(found, city, max_per_host=s.max_results_per_host,
                                    limit=s.max_candidates_per_round)
            if self.crossref and s.crossref_verify_top_n > 0:
                verify = await self._verify(ranked, queries)
                if verify:
                    calls.append(verify)
                    ranked = merge_and_rank(ranked + verify.results, city,
                                            max_per_host=s.max_results_per_host,
                                            limit=s.max_candidates_per_round)
            _count_retained(calls, ranked)
            status = _provider_status(calls)
            span.set_attribute("discovery.candidates", len(ranked))
            for name, value in status.items():
                span.set_attribute(f"discovery.status.{name}", value)
        return DiscoveryOutcome(candidates=ranked, calls=calls, provider_status=status)

    async def _search(self, provider: DiscoveryProvider, q: PlannedQuery) -> ProviderCall:
        strategy = "web_search" if q.kind == "web" else "scholarly_search"
        return await _timed(
            provider.name, strategy, q.query, q.field_key,
            provider.search(q.query, self.settings.results_per_query))

    async def _related(self, seeds: list[SearchResult],
                       queries: Sequence[PlannedQuery]) -> list[ProviderCall]:
        assert self.related is not None
        ids = [r.provider_ids["semantic_scholar"] for r in seeds
               if "semantic_scholar" in r.provider_ids]
        ids = ids[:self.settings.semantic_scholar_related_per_round]
        field_key = _academic_field_key(queries)
        return list(await asyncio.gather(*(
            _timed(self.related.name, "related_papers", None, field_key,
                   self.related.related(pid, self.settings.results_per_query))
            for pid in ids)))

    async def _verify(self, ranked: list[SearchResult],
                      queries: Sequence[PlannedQuery]) -> ProviderCall | None:
        """Crossref lookups for the top DOI candidates, reported as one aggregate call."""
        assert self.crossref is not None
        todo = [r for r in ranked if r.doi and r.doi_verified is None]
        todo = todo[:self.settings.crossref_verify_top_n]
        if not todo:
            return None
        start = time.perf_counter()
        lookups = await asyncio.gather(*(self.crossref.lookup_doi(r.doi or "") for r in todo),
                                       return_exceptions=True)
        verified: list[SearchResult] = []
        errors: list[str] = []
        for candidate, found in zip(todo, lookups):
            if isinstance(found, BaseException):
                errors.append(_error_kind(found))
                if not isinstance(found, ProviderError):
                    log.warning("crossref lookup failed unexpectedly", exc_info=found)
            elif found is None:
                candidate.doi_verified = False  # Crossref doesn't know this DOI
            else:
                verified.append(found)  # merged into the candidate by DOI
        duration = int((time.perf_counter() - start) * 1000)
        failed_all = len(errors) == len(todo)
        return ProviderCall(
            provider="crossref", capability=PROVIDER_CAPABILITIES["crossref"],
            strategy=errors[0] if failed_all else "doi_verification", query=None,
            field_key=_academic_field_key(queries),
            result=FAILURE if failed_all else (SUCCESS if verified else NO_RESULT),
            sources_found=len(todo), sources_retained=len(verified), duration_ms=duration,
            error=errors[0] if errors else None, results=verified)

async def _timed(provider: str, strategy: str, query: str | None, field_key: str | None,
                 work) -> ProviderCall:
    capability = PROVIDER_CAPABILITIES.get(provider, Capability.SOURCE_DISCOVERY)
    start = time.perf_counter()
    results: list[SearchResult] = []
    error: str | None = None
    try:
        results = list(await work)
    except Exception as exc:  # one provider must never fail the round
        error = _error_kind(exc)
        if not isinstance(exc, ProviderError):
            log.warning("discovery provider %s failed unexpectedly", provider, exc_info=True)
        else:
            log.info("discovery provider failed: %s", exc)
    duration = int((time.perf_counter() - start) * 1000)
    result = FAILURE if error else (SUCCESS if results else NO_RESULT)
    return ProviderCall(provider=provider, capability=capability,
                        strategy=error or strategy, query=query, field_key=field_key,
                        result=result, sources_found=len(results), duration_ms=duration,
                        error=error, results=results)

def _error_kind(exc: BaseException) -> str:
    return exc.kind.value if isinstance(exc, ProviderError) else "error"

def _academic_field_key(queries: Sequence[PlannedQuery]) -> str | None:
    q = next((q for q in queries if q.kind == "academic"), None) or next(iter(queries), None)
    return q.field_key if q else None

def _count_retained(calls: list[ProviderCall], ranked: list[SearchResult]) -> None:
    kept: set[str] = set()
    for r in ranked:
        kept |= dedupe_keys(r)
    for c in calls:
        if c.strategy != "doi_verification":
            c.sources_retained = sum(1 for r in c.results if dedupe_keys(r) & kept)

def _provider_status(calls: list[ProviderCall]) -> dict[str, str]:
    by_provider: dict[str, list[ProviderCall]] = {}
    for c in calls:
        by_provider.setdefault(c.provider, []).append(c)
    status: dict[str, str] = {}
    for name, cs in by_provider.items():
        failures = [c.error for c in cs if c.result == FAILURE]
        if len(failures) == len(cs):
            status[name] = failures[0] or "error"
        elif failures:
            status[name] = f"degraded:{failures[0]}"
        else:
            status[name] = OK if any(c.result == SUCCESS for c in cs) else NO_RESULTS
    return status

def build_discovery_service(settings: Any = None, **http_kwargs: Any) -> DiscoveryService | None:
    """Providers from ``Settings``; ``None`` when discovery is disabled."""
    if settings is None:
        from water_source_research.core.config import get_settings
        settings = get_settings()
    d: DiscoverySettings = settings.discovery
    if not d.enabled:
        return None
    from .openalex import OpenAlexDiscovery
    from .searxng import SearXNGDiscovery
    enabled = set(d.providers)
    web: list[DiscoveryProvider] = []
    scholarly: list[DiscoveryProvider] = []
    if "searxng" in enabled and settings.searxng_url:
        web.append(SearXNGDiscovery(settings.searxng_url, d, **http_kwargs))
    if "openalex" in enabled:
        scholarly.append(OpenAlexDiscovery(settings.openalex_api_key, d, **http_kwargs))
    if "semantic_scholar" in enabled:
        scholarly.append(SemanticScholarDiscovery(settings.semantic_scholar_api_key, d,
                                                  **http_kwargs))
    crossref = (CrossrefDiscovery(settings.crossref_mailto, d, **http_kwargs)
                if "crossref" in enabled else None)
    return DiscoveryService(d, web=web, scholarly=scholarly, crossref=crossref)
