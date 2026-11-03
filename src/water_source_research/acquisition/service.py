"""Acquisition: ranked discovery candidates -> fetched documents -> evidence chunks.

Discovery candidates are pointers, never evidence: only fetched document text becomes
``EvidenceChunk``s (snippets are ignored). Per round, a bounded set of not-yet-attempted URLs
is chosen with official sources first; each fetch is isolated so one failure never fails the
run, and documents already seen (same canonical URL, same final URL after redirects, or same
content hash) are not processed again.
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from water_source_research.discovery.normalize import canonical_url, municipal_hint
from water_source_research.observability.otel import get_tracer
from water_source_research.observability.tracing import safe_url
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.evidence import chunk_document
from .artifacts import ArtifactStore
from .base import AcquiredDocument
from .config import AcquisitionSettings
from .http import parse_html
from .pdf import is_pdf, parse_pdf
from .safe_http import FetchedResponse, FetchError, FetchErrorKind, SafeFetcher

log = logging.getLogger(__name__)

# Official/primary sources first; academic and other web results only fill remaining slots.
TIER_ORDER = {"UTILITY": 0, "GOVERNMENT": 1, "REGULATORY": 2, "TECHNICAL": 3, "ACADEMIC": 4}
OTHER_TIER = 5
# Selection groups (Milestone 3.1): a UTILITY classification is not a serving relationship, so
# it no longer outranks government pages; unverified UTILITY-classified pages keep a small
# quota so a real utility not yet verified still gets fetched.
G_VERIFIED, G_OFFICIAL, G_CANDIDATE, G_UTILITY_UNVERIFIED, G_SECONDARY, G_OTHER, G_ELSEWHERE = (
    range(7))
UNVERIFIED_UTILITY_PER_ROUND = 2
SKIP_EXTENSIONS = (".xls", ".xlsx", ".csv", ".doc", ".docx", ".ppt", ".pptx", ".zip", ".gz",
                   ".jpg", ".jpeg", ".png", ".gif", ".svg", ".mp3", ".mp4", ".kmz", ".kml",
                   ".shp", ".exe", ".dwg")

# Document fetches in flight in this process (one event loop per worker; diagnostics only).
_FETCHES_IN_FLIGHT = 0

def fetches_in_flight() -> int:
    return _FETCHES_IN_FLIGHT

class DocStatus:
    OK = "OK"
    EMPTY = "EMPTY"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"

@dataclass(slots=True)
class AcquisitionOutcome:
    records: list[dict[str, Any]] = field(default_factory=list)  # one per attempted URL
    chunks: list[EvidenceChunk] = field(default_factory=list)

def doc_key(url: str | None) -> str:
    return canonical_url(url) or (url or "")

ELSEWHERE_PER_ROUND = 1

def _elsewhere(c: Mapping[str, Any], features: Sequence[str]) -> bool:
    """About another place sharing the city's name (approved identity; discovery ranking),
    unless its title/snippet names one of the city's known water features."""
    if not (c.get("geo_status") in ("HOMONYM", "OTHER_PLACE")
            or c.get("geo_scope") in ("OTHER_COUNTRY", "OTHER_REGION")):
        return False
    text = f"{c.get('title') or ''} {c.get('snippet') or ''}".casefold()
    return not any(f.casefold() in text for f in features)

def selection_group(c: Mapping[str, Any], host: str, preferred: set[str],
                    candidate_hosts: set[str], features: Sequence[str],
                    city: str | None) -> int:
    """Where a candidate stands in acquisition order (lower first):
    verified utility host; government/regulatory pages and city-department host hints
    (MUNICIPAL_CANDIDATE: priority only); hosts of registry candidates (unverified utilities
    the evidence names); other UTILITY-classified pages (quota); technical and academic;
    everything else; pages about another place sharing the name (last, 1 per round)."""
    if _elsewhere(c, features):
        return G_ELSEWHERE
    if host in preferred:
        return G_VERIFIED
    tier = (c.get("source_type") or "").upper()
    if tier in ("GOVERNMENT", "REGULATORY") or municipal_hint(host, city):
        return G_OFFICIAL
    if host in candidate_hosts:
        return G_CANDIDATE
    if tier == "UTILITY":
        return G_UTILITY_UNVERIFIED
    if tier in ("TECHNICAL", "ACADEMIC"):
        return G_SECONDARY
    return G_OTHER

def select_candidates(candidates: list[Mapping[str, Any]],
                      attempted: set[str], settings: AcquisitionSettings,
                      budget: int, priority_hosts: Sequence[str] = (),
                      skip_hosts: Sequence[str] = (),
                      features: Sequence[str] = (), *,
                      candidate_hosts: Sequence[str] = (),
                      city: str | None = None) -> list[Mapping[str, Any]]:
    """Up to ``budget`` (<= per-round cap) unattempted candidates by ``selection_group``,
    keeping discovery's ranking within a group (tier order breaks ties), at most
    ``max_per_host`` per host and UNVERIFIED_UTILITY_PER_ROUND unverified UTILITY pages;
    ``skip_hosts`` (hosts suppressed or failing for this run) are never fetched."""
    preferred = {h.removeprefix("www.") for h in priority_hosts}
    skipped = {h.removeprefix("www.") for h in skip_hosts}
    named = {h.removeprefix("www.") for h in candidate_hosts}

    def host_of(c: Mapping[str, Any]) -> str:
        return (urlsplit(c.get("url") or "").hostname or "").removeprefix("www.")

    groups = {i: selection_group(c, host_of(c), preferred, named, features, city)
              for i, c in enumerate(candidates)}
    ordered = sorted(enumerate(candidates), key=lambda ic: (
        groups[ic[0]], TIER_ORDER.get((ic[1].get("source_type") or "").upper(), OTHER_TIER)
        if groups[ic[0]] == G_SECONDARY else 0, ic[0]))
    elsewhere_taken = 0
    unverified_taken = 0
    chosen: list[Mapping[str, Any]] = []
    per_host: dict[str, int] = {}
    keys: set[str] = set()
    limit = min(budget, settings.max_documents_per_round)
    for index, c in ordered:
        if len(chosen) >= limit:
            break
        url = c.get("url") or ""
        key = doc_key(url)
        if not key or key in attempted or key in keys:
            continue
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        if parts.path.lower().endswith(SKIP_EXTENSIONS):
            continue
        host = (parts.hostname or "").removeprefix("www.")
        if host in skipped or per_host.get(host, 0) >= settings.max_per_host:
            continue
        if groups[index] == G_ELSEWHERE:
            if elsewhere_taken >= ELSEWHERE_PER_ROUND:
                continue
            elsewhere_taken += 1
        if groups[index] == G_UTILITY_UNVERIFIED:
            if unverified_taken >= UNVERIFIED_UTILITY_PER_ROUND:
                continue
            unverified_taken += 1
        per_host[host] = per_host.get(host, 0) + 1
        keys.add(key)
        chosen.append(c)
    return chosen

class AcquisitionService:
    def __init__(self, settings: AcquisitionSettings | None = None, *,
                 fetcher: SafeFetcher | None = None,
                 artifact_store: ArtifactStore | None = None) -> None:
        self.s = settings or AcquisitionSettings()
        self.fetcher = fetcher or SafeFetcher(self.s)
        self.artifacts = artifact_store

    async def acquire_round(self, candidates: list[Mapping[str, Any]],
                            previous: list[Mapping[str, Any]],
                            round_no: int = 1,
                            priority_hosts: Sequence[str] = (),
                            skip_hosts: Sequence[str] = (),
                            features: Sequence[str] = (), *,
                            candidate_hosts: Sequence[str] = (),
                            city: str | None = None) -> AcquisitionOutcome:
        """Acquire new documents for one round. ``previous`` are earlier document records
        (any status), used for de-duplication and the per-run budget. Never raises."""
        attempted = set()
        hashes = set()
        for r in previous:
            attempted.add(r.get("canonical_url") or doc_key(r.get("requested_url")))
            if r.get("final_url"):
                attempted.add(doc_key(r["final_url"]))
            if r.get("content_sha256") and r.get("status") == DocStatus.OK:
                hashes.add(r["content_sha256"])
        budget = self.s.max_documents_total - sum(
            1 for r in previous if r.get("status") != DocStatus.SKIPPED_DUPLICATE)
        outcome = AcquisitionOutcome()
        if budget <= 0:
            return outcome
        chosen = select_candidates(candidates, attempted, self.s, budget, priority_hosts,
                                   skip_hosts, features, candidate_hosts=candidate_hosts,
                                   city=city)
        results = await asyncio.gather(*(self._fetch_one(c) for c in chosen))

        # Sequential, in selection order: deterministic de-duplication and chunking.
        for candidate, (record, doc, response) in zip(chosen, results, strict=True):
            record["round"] = round_no
            final_key = doc_key(record.get("final_url"))
            if record["status"] == DocStatus.OK and doc is not None and (
                    final_key in attempted or doc.content_sha256 in hashes):
                record["status"] = DocStatus.SKIPPED_DUPLICATE
                record["error_message"] = "same document already acquired"
                doc = None
            attempted.add(record["canonical_url"])
            if final_key:
                attempted.add(final_key)
            if doc is not None:
                chunks = chunk_document(doc, source_type=record["source_type"],
                                        max_chars=self.s.chunk_max_chars,
                                        overlap_chars=self.s.chunk_overlap_chars)
                if not chunks:
                    record["status"] = DocStatus.EMPTY
                else:
                    hashes.add(doc.content_sha256)
                    outcome.chunks.extend(chunks)
                record["chunks"] = len(chunks)
                if response is not None and self.artifacts is not None:
                    await self._store_raw(record, response)
            outcome.records.append(record)
        return outcome

    async def _fetch_one(self, c: Mapping[str, Any]
                         ) -> tuple[dict[str, Any], AcquiredDocument | None,
                                    FetchedResponse | None]:
        global _FETCHES_IN_FLIGHT
        _FETCHES_IN_FLIGHT += 1
        try:
            with get_tracer().start_as_current_span("acquisition.fetch_document") as span:
                span.set_attribute("acquisition.url", safe_url(c.get("url")))
                span.set_attribute("acquisition.source_type", str(c.get("source_type") or ""))
                record, doc, response = await self._fetch_one_counted(c)
                span.set_attribute("acquisition.status", record["status"])
                if record.get("error_kind"):
                    span.set_attribute("acquisition.error_kind", record["error_kind"])
                if record.get("byte_size") is not None:
                    span.set_attribute("acquisition.bytes", record["byte_size"])
                if record.get("acquisition_method"):
                    span.set_attribute("acquisition.method", record["acquisition_method"])
                return record, doc, response
        finally:
            _FETCHES_IN_FLIGHT -= 1

    async def _fetch_one_counted(self, c: Mapping[str, Any]
                                 ) -> tuple[dict[str, Any], AcquiredDocument | None,
                                            FetchedResponse | None]:
        url = c.get("url") or ""
        record: dict[str, Any] = {
            "requested_url": url, "canonical_url": doc_key(url), "final_url": None,
            "title": c.get("title"), "source_type": c.get("source_type"),
            "publisher_kind": c.get("publisher_kind"),
            "classification_version": c.get("classification_version"),
            "classification_signals": ({"signals": list(c.get("classification_signals") or [])}
                                       if c.get("classification_version") else None),
            "acquisition_method": None, "media_type": None, "retrieved_at": None,
            "source_date": c.get("published_date"), "content_sha256": None, "byte_size": None,
            "page_count": None, "status": DocStatus.FAILED, "error_kind": None,
            "error_message": None, "raw_object_key": None, "chunks": 0,
            "discovery_provider": c.get("provider"), "discovery_query": c.get("query"),
            "duration_ms": 0.0,
        }
        started = time.perf_counter()
        response = doc = None
        try:
            response = await self.fetcher.fetch(url, max(self.s.max_html_bytes,
                                                         self.s.max_pdf_bytes))
            record.update(final_url=response.final_url, retrieved_at=response.fetched_at,
                          byte_size=len(response.body), media_type=response.content_type)
            doc = await asyncio.to_thread(self._parse, response)
            doc.retrieved_at = response.fetched_at
            record.update(title=doc.title or record["title"],
                          acquisition_method=doc.acquisition_method,
                          content_sha256=doc.content_sha256,
                          source_date=doc.source_date or record["source_date"],
                          page_count=len(doc.pages) if doc.pages is not None else None)
            record["status"] = DocStatus.OK if doc.text.strip() else DocStatus.EMPTY
            if record["status"] == DocStatus.EMPTY:
                doc = None
        except FetchError as exc:
            record.update(error_kind=exc.kind.value, error_message=exc.message[:500])
            doc = None
        except Exception as exc:  # parser bugs/corrupt files: isolate per document
            log.warning("acquisition of %s failed", url, exc_info=True)
            record.update(error_kind=FetchErrorKind.PARSE_ERROR.value,
                          error_message=f"{type(exc).__name__}: {exc}"[:500])
            doc = None
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return record, doc, response

    def _parse(self, r: FetchedResponse) -> AcquiredDocument:
        with get_tracer().start_as_current_span("acquisition.parse_document") as span:
            span.set_attribute("acquisition.media_type", (r.content_type or "")[:80])
            span.set_attribute("acquisition.bytes", len(r.body))
            doc = self._parse_body(r)
            span.set_attribute("acquisition.method", doc.acquisition_method or "")
            if doc.pages is not None:
                span.set_attribute("acquisition.pages", len(doc.pages))
            return doc

    def _parse_body(self, r: FetchedResponse) -> AcquiredDocument:
        ctype = r.content_type.lower()
        if is_pdf(r.body, ctype):
            if len(r.body) > self.s.max_pdf_bytes:
                raise FetchError(FetchErrorKind.TOO_LARGE, f"PDF > {self.s.max_pdf_bytes}")
            return parse_pdf(r.final_url, r.body, self.s.max_pdf_pages)
        head = r.body[:512].lstrip().lower()
        if "html" in ctype or head.startswith((b"<!doctype html", b"<html")):
            if len(r.body) > self.s.max_html_bytes:
                raise FetchError(FetchErrorKind.TOO_LARGE, f"HTML > {self.s.max_html_bytes}")
            return parse_html(r.final_url, r.body, r.content_type or "text/html")
        if ctype.startswith("text/plain"):
            return AcquiredDocument(url=r.final_url, title=None, media_type="text/plain",
                                    text=r.body.decode("utf-8", errors="replace"),
                                    acquisition_method="DIRECT_HTTP",
                                    content_sha256=hashlib.sha256(r.body).hexdigest())
        raise FetchError(FetchErrorKind.UNSUPPORTED_TYPE, r.content_type or "unknown")

    async def _store_raw(self, record: dict[str, Any], r: FetchedResponse) -> None:
        try:
            media = "application/pdf" if record["acquisition_method"] == "DIRECT_HTTP_PDF" \
                else (r.content_type or "text/html")
            record["raw_object_key"] = await asyncio.to_thread(
                self.artifacts.put, r.body, record["content_sha256"], media)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - evidence is kept even if the raw copy can't be stored
            log.warning("could not store raw artifact for %s: %s", record["requested_url"], exc)
            record["artifact_error"] = f"{type(exc).__name__}: {exc}"[:300]

def build_acquisition_service(settings, artifact_store: ArtifactStore | None = None
                              ) -> AcquisitionService | None:
    if not settings.acquisition.enabled:
        return None
    return AcquisitionService(settings.acquisition, artifact_store=artifact_store)
