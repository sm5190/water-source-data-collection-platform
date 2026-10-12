"""Deduplicate discovery candidates across providers and rank them.

Duplicates are linked by normalized DOI, canonical URL, or normalized title (transitively,
so a Crossref record sharing a DOI with an OpenAlex work that shares a title with a Semantic
Scholar paper collapses into one candidate). Ranking reuses the context manager's source-type
prior so official utility/government sources stay ahead of academic and secondary sources:
the score is ``tier + relevance`` where ``relevance`` (< 0.03) only orders candidates within
a tier and never lets one cross into a higher tier.
"""

import re
from collections.abc import Iterable
from dataclasses import replace
from urllib.parse import urlsplit
from water_source_research.context_manager.manager import (
    CITY_NOT_MENTIONED_FACTOR, SOURCE_TYPE_WEIGHTS, UNKNOWN_SOURCE_WEIGHT,
)
from .base import SearchResult
from .normalize import (
    SCHOLARLY_PROVIDERS, canonical_url, classify_source_type, fold, normalize_doi,
    normalize_title,
)

# Candidates that never mention water at all are almost always off-topic search noise
# ("Toronto housing governance"); demote them like the context manager demotes chunks that
# don't mention the city.
WATER_TERMS = re.compile(
    r"(water|wasser|agua|eau\b|acqua|aqua|aquifer|reservoir|hydro|drinking|potable|"
    r"watershed|desalinat|sewer|sanita|utilit|river|lake|dam\b|basin|abstraction|withdrawal)")
NO_WATER_FACTOR = 0.7
RRF_K = 10
MULTI_PROVIDER_BONUS = 0.15
RELEVANCE_SCALE = 0.1  # keeps relevance below the smallest gap between tier values

def dedupe_keys(r: SearchResult) -> set[str]:
    keys: set[str] = set()
    if doi := normalize_doi(r.doi):
        keys.add(f"doi:{doi}")
    if url := canonical_url(r.url):
        keys.add(url if url.startswith("doi:") else f"url:{url}")
    if title := normalize_title(r.title):
        keys.add(f"title:{title}")
    return keys

def source_weight(r: SearchResult) -> float:
    return SOURCE_TYPE_WEIGHTS.get((r.source_type or "").upper(), UNKNOWN_SOURCE_WEIGHT)

def merge_and_rank(results: Iterable[SearchResult], city: str | None = None, *,
                   max_per_host: int | None = None,
                   limit: int | None = None) -> list[SearchResult]:
    results = list(results)
    for r in results:
        # Adapters classify without the city; with it, the city's own site is municipal.
        if r.source_type in (None, "OTHER") and r.provider not in SCHOLARLY_PROVIDERS:
            r.source_type = classify_source_type(r.url, r.title, r.provider, r.doi, city)
    groups = _group(results)
    merged = [_merge(g) for g in groups]
    city_key = fold(city or "").strip()
    for r in merged:
        r.score = _score(r, city_key)
    merged.sort(key=lambda r: (-(r.score or 0), r.rank or 10**6, r.title))
    out: list[SearchResult] = []
    per_host: dict[str, int] = {}
    for r in merged:
        host = (urlsplit(r.url).hostname or "").lower().removeprefix("www.")
        if max_per_host is not None and host and per_host.get(host, 0) >= max_per_host:
            continue
        per_host[host] = per_host.get(host, 0) + 1
        out.append(r)
        if limit is not None and len(out) >= limit:
            break
    return out

def _group(results: list[SearchResult]) -> list[list[SearchResult]]:
    parent = list(range(len(results)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, r in enumerate(results):
        for key in dedupe_keys(r):
            if key in owner:
                parent[find(i)] = find(owner[key])
            else:
                owner[key] = i
    groups: dict[int, list[SearchResult]] = {}
    for i, r in enumerate(results):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values())

def _merge(group: list[SearchResult]) -> SearchResult:
    for r in group:
        if not r.source_type:
            r.source_type = classify_source_type(r.url, r.title, r.provider, r.doi)
    # The representative is the most official, best-ranked record (keeps its original URL);
    # a Crossref record only fills in metadata unless it is all we have.
    primary = min(group, key=lambda r: (-source_weight(r), r.provider == "crossref",
                                        r.rank or 10**6))
    merged = replace(primary, providers=list(primary.providers),
                     provider_ids=dict(primary.provider_ids), authors=list(primary.authors))
    crossref = next((r for r in group if r.provider == "crossref"), None)
    for r in group:
        for p in r.providers or [r.provider]:
            if p and p not in merged.providers:
                merged.providers.append(p)
        for p, pid in r.provider_ids.items():
            merged.provider_ids.setdefault(p, pid)
        merged.doi = merged.doi or normalize_doi(r.doi)
        merged.venue = merged.venue or r.venue
        merged.snippet = merged.snippet or r.snippet
        if len(r.authors) > len(merged.authors):
            merged.authors = list(r.authors)
        if r.published_date and len(r.published_date) > len(merged.published_date or ""):
            merged.published_date = r.published_date
        if r.rank is not None and (merged.rank is None or r.rank < merged.rank):
            merged.rank = r.rank
    if crossref:  # Crossref is the authority for citation metadata
        merged.doi = normalize_doi(crossref.doi) or merged.doi
        merged.published_date = crossref.published_date or merged.published_date
        merged.venue = crossref.venue or merged.venue
    verified = [r.doi_verified for r in group if r.doi_verified is not None]
    if verified:
        merged.doi_verified = any(verified)
    merged.doi = normalize_doi(merged.doi)
    return merged

def _score(r: SearchResult, city_key: str) -> float:
    tier = source_weight(r)
    haystack = fold(" ".join(filter(None, (r.title, r.snippet, r.url, r.venue))))
    if city_key and city_key not in haystack and city_key.replace(" ", "") not in haystack:
        tier *= CITY_NOT_MENTIONED_FACTOR
    if not WATER_TERMS.search(haystack):
        tier *= NO_WATER_FACTOR
    providers = max(1, len(set(r.providers)))
    relevance = (1 / (RRF_K + (r.rank or 50))) * (1 + MULTI_PROVIDER_BONUS * (providers - 1))
    return round(tier + RELEVANCE_SCALE * relevance, 6)
