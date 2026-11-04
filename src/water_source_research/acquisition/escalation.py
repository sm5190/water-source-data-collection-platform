"""Firecrawl escalation: a second, bounded attempt for pages the direct fetch read as empty
or couldn't reach, kept beside the failed direct record (attempt "firecrawl"), never instead
of it.

Eligible only when the direct outcome is EMPTY, TIMEOUT, NETWORK or HTTP 429/5xx **and** the
origin's robots.txt was read and allows the URL (a positive decision). Never for 401/403
(AUTH_REQUIRED), bot checks or robots refusals/unreachable robots. URLs carrying credentials
or credential-like query parameters are never sent, and only public http(s) URLs on 80/443
are; Firecrawl's final URL after redirects must pass the same checks and robots, else its
content is discarded. A challenge page is still a BOT_CHECK failure.

Credits: each request is reserved in ``firecrawl_attempts`` before it is sent (a URL is sent
at most once per run, even across restarts); the run's budget is a conservative estimate.
"""

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from water_source_research.observability.otel import get_tracer
from water_source_research.observability.tracing import safe_url
from water_source_research.observability.usage import record_usage
from .base import AcquiredDocument
from .firecrawl import (
    FirecrawlClient, FirecrawlError, FirecrawlErrorKind, FirecrawlResult, coverage, pdf_pages,
    select_representation, sensitive_url,
)
from .firecrawl_store import FirecrawlAttemptStore
from .http import parse_html
from .safe_http import SafeFetcher

log = logging.getLogger(__name__)

NEVER_RETRIED = frozenset({"AUTH_REQUIRED", "BOT_CHECK", "ROBOTS_DISALLOWED",
                           "ROBOTS_UNREACHABLE"})
_STATUS = re.compile(r"HTTP (\d{3})")

def retry_reason(record: Mapping[str, Any]) -> str | None:
    """Why the direct outcome qualifies for a Firecrawl attempt, else None."""
    kind = record.get("error_kind")
    if kind in NEVER_RETRIED:
        return None
    if record.get("status") == "EMPTY" and not kind:
        return "EMPTY"
    if kind in ("TIMEOUT", "NETWORK"):
        return kind
    if kind == "HTTP_STATUS":
        m = _STATUS.search(record.get("error_message") or "")
        code = int(m.group(1)) if m else 0
        if code == 429:
            return "HTTP_429"
        if 500 <= code <= 599:
            return "HTTP_5XX"
    return None

def _is_pdf(record: Mapping[str, Any], url: str) -> bool:
    media = (record.get("media_type") or "").lower()
    return "pdf" in media or url.lower().split("?", 1)[0].endswith(".pdf")

class FirecrawlEscalator:
    def __init__(self, client: FirecrawlClient, store: FirecrawlAttemptStore,
                 fetcher: SafeFetcher, *, cap: float, pdf_max_pages: int,
                 concurrency: int = 2, artifacts=None) -> None:
        self.client = client
        self.store = store
        self.fetcher = fetcher
        self.cap = float(cap)
        self.pdf_max_pages = pdf_max_pages
        self._sem = asyncio.Semaphore(concurrency)
        self.artifacts = artifacts
        self._disabled: dict[str, str] = {}  # run -> reason (bad key, no credits)

    def disabled_reason(self, run_id: str) -> str | None:
        return self._disabled.get(run_id)

    async def escalate(self, run_id: str, round_no: int,
                       direct: list[dict[str, Any]]) -> list[tuple[dict[str, Any],
                                                                    AcquiredDocument | None]]:
        """Firecrawl attempts for this round's failed direct records. Decisions not to send
        are noted on the direct record (``escalation.firecrawl``), which is otherwise
        unchanged. Never raises."""
        await asyncio.to_thread(self.store.recover_pending, run_id)
        tried = await asyncio.to_thread(self.store.attempted, run_id)
        eligible = []
        for rec in direct:
            reason = retry_reason(rec)
            if reason is None:
                continue
            note = await self._precheck(run_id, rec, tried)
            if note:
                rec["escalation"] = {**(rec.get("escalation") or {}), "firecrawl": note,
                                     "retry_reason": reason}
                continue
            eligible.append((rec, reason))
        if not eligible:
            return []
        before = await self.client.remaining_credits()
        results = await asyncio.gather(*(self._one(run_id, round_no, rec, reason)
                                         for rec, reason in eligible))
        after = await self.client.remaining_credits()
        team = {"team_remaining_before": before, "team_remaining_after": after,
                "observed_team_delta": (None if before is None or after is None
                                        else round(before - after, 3)),
                "note": "team-wide, approximate: other usage in the same window counts too"}
        out = []
        for fc_record, doc in results:
            if fc_record is None:
                continue
            fc_record["escalation"]["round_team_credits"] = team
            out.append((fc_record, doc))
        return out

    async def _precheck(self, run_id: str, rec: Mapping[str, Any], tried: set[str]
                        ) -> str | None:
        url = rec.get("requested_url") or ""
        if run_id in self._disabled:
            return "DISABLED:" + self._disabled[run_id]
        if rec["canonical_url"] in tried:
            return "ALREADY_ATTEMPTED"
        why = sensitive_url(url)
        if why:
            return "NOT_SENT_SENSITIVE_URL"
        if await self.fetcher.check_public(url):
            return "NOT_SENT_NOT_PUBLIC"
        for u in {url, rec.get("final_url") or url}:
            if await self.fetcher.robots_decision(u) != "ALLOWED":
                return "ROBOTS_NOT_ALLOWED"
        return None

    def _fc_record(self, rec: Mapping[str, Any], reason: str) -> dict[str, Any]:
        keep = ("round", "requested_url", "canonical_url", "title", "source_type",
                "publisher_kind", "classification_version", "classification_signals",
                "source_date", "discovery_provider", "discovery_query", "selection",
                "reserved_for", "reserved_reason")
        out = {k: rec.get(k) for k in keep if k in rec}
        out.update(attempt="firecrawl", final_url=None, acquisition_method=None,
                   media_type=None, retrieved_at=None, content_sha256=None, byte_size=None,
                   page_count=None, status="FAILED", error_kind=None, error_message=None,
                   raw_object_key=None, chunks=0, duration_ms=0.0)
        out["escalation"] = {
            "from": {"status": rec.get("status"), "error_kind": rec.get("error_kind"),
                     "error_message": (rec.get("error_message") or "")[:200],
                     "duration_ms": rec.get("duration_ms")},
            "retry_reason": reason, "recovery_status": None, "credits_reserved": None,
            "credits_estimated": None, "credits_actual": None, "attempts": 0}
        return out

    async def _one(self, run_id: str, round_no: int, rec: dict[str, Any], reason: str
                   ) -> tuple[dict[str, Any] | None, AcquiredDocument | None]:
        url = rec["requested_url"]
        pdf = _is_pdf(rec, url)
        used = await asyncio.to_thread(self.store.used, run_id)
        remaining = self.cap - used
        if remaining < 2:
            rec["escalation"] = {**(rec.get("escalation") or {}), "firecrawl": "BUDGET",
                                 "retry_reason": reason}
            return None, None
        # The reservation is the worst case: a PDF costs 1 + its parsed pages, so an expected
        # PDF reserves 1 + maxPages, and any other page sends maxPages=1 and reserves 2 (an
        # unexpected PDF then still fits); answered HTML settles at 1.
        max_pages = max(1, min(self.pdf_max_pages, int(remaining) - 1)) if pdf else 1
        reserve = float(1 + max_pages)
        attempt_id, status, _ = await asyncio.to_thread(
            self.store.reserve, run_id, rec["canonical_url"], round_no, reserve, self.cap)
        if attempt_id is None:
            rec["escalation"] = {**(rec.get("escalation") or {}), "firecrawl": status,
                                 "retry_reason": reason}
            return None, None
        fc = self._fc_record(rec, reason)
        fc["escalation"]["credits_reserved"] = reserve
        fc["escalation"]["pdf_max_pages"] = max_pages
        started = time.perf_counter()
        with get_tracer().start_as_current_span("acquisition.firecrawl") as span:
            span.set_attribute("acquisition.url", safe_url(url))
            span.set_attribute("acquisition.retry_reason", reason)
            doc, estimate, uncertain, error_kind = None, None, False, None
            try:
                async with self._sem:
                    result = await self.client.scrape(url, pdf_max_pages=max_pages)
                fc["escalation"]["attempts"] = result.attempts
                doc, estimate = await self._document(fc, result, max_pages)
            except FirecrawlError as exc:
                fc["escalation"]["attempts"] = fc["escalation"]["attempts"] or 1
                error_kind = "FIRECRAWL_" + exc.kind.value
                uncertain = exc.uncertain
                estimate = None if uncertain else 0.0  # rejected before any work
                fc.update(error_kind=error_kind[:32], error_message=exc.message[:500])
                fc["escalation"]["recovery_status"] = "UNCERTAIN" if uncertain else "FAILED"
                if exc.kind in (FirecrawlErrorKind.AUTH, FirecrawlErrorKind.CREDITS_EXHAUSTED):
                    self._disabled[run_id] = exc.kind.value
            except Exception as exc:  # our own parsing: the request was answered (billed)
                log.warning("firecrawl result for %s not usable", safe_url(url), exc_info=True)
                error_kind = "PARSE_ERROR"
                fc.update(error_kind=error_kind, error_message=f"{type(exc).__name__}"[:500])
                fc["escalation"]["recovery_status"] = "FAILED"
                estimate = None
            latency = round((time.perf_counter() - started) * 1000, 1)
            fc["duration_ms"] = latency
            fc["escalation"]["credits_estimated"] = estimate
            span.set_attribute("acquisition.status", fc["status"])
            span.set_attribute("firecrawl.recovery_status",
                               fc["escalation"]["recovery_status"] or "")
            span.set_attribute("firecrawl.credits_reserved", reserve)
            if estimate is not None:
                span.set_attribute("firecrawl.credits_estimated", estimate)
        await asyncio.to_thread(
            self.store.settle, attempt_id, uncertain=uncertain or estimate is None,
            estimated=estimate, outcome=fc["escalation"]["recovery_status"] or "FAILED",
            error_kind=error_kind or fc.get("error_kind"))
        if estimate is None:
            fc["escalation"]["credits_note"] = ("outcome uncertain or charge not derivable: "
                                                "the reservation is kept")
        record_usage({"run_id": run_id, "category": "acquisition", "kind": "CRAWL",
                      "provider": "firecrawl", "model": None, "task": "scrape",
                      "requests": fc["escalation"]["attempts"] or 1,
                      "retries": max(0, (fc["escalation"]["attempts"] or 1) - 1),
                      "cache_hit": False, "error": error_kind,
                      "attempt_errors": 1 if error_kind else 0,
                      "credits": None, "credits_estimated": estimate if estimate is not None
                      else reserve, "latency_ms": latency, "cost_usd": None,
                      "cost_basis": "estimated" if estimate is not None else "reserved"})
        return fc, doc

    async def _document(self, fc: dict[str, Any], result: FirecrawlResult, max_pages: int
                        ) -> tuple[AcquiredDocument | None, float | None]:
        """Fill the Firecrawl record from an answered request; (document or None, estimated
        credits or None when not derivable)."""
        from .service import is_bot_check
        esc = fc["escalation"]
        esc["fields_seen"] = result.fields_seen[:60]
        if result.warning:
            esc["warning"] = str(result.warning)[:300]
        final = result.final_url or fc["requested_url"]
        media = str(result.metadata.get("contentType") or "")
        is_pdf = "pdf" in media.lower() or bool(result.pages) or \
            result.metadata.get("numPages") is not None
        now = datetime.now(UTC).isoformat(timespec="seconds")
        fc.update(final_url=final, retrieved_at=now, media_type=media or None,
                  title=result.metadata.get("title") or fc.get("title"))
        estimate: float | None = 1.0
        if is_pdf:
            num = result.metadata.get("numPages")
            estimate = (1.0 + float(num)) if isinstance(num, int | float) else None
        code = result.status_code
        if code is not None and code >= 400:
            fc.update(error_kind="TARGET_STATUS", error_message=f"page answered HTTP {code}")
            esc["recovery_status"] = "TARGET_STATUS"
            return None, estimate
        if final != fc["requested_url"]:
            ok = await self.fetcher.check_public(final) is None and \
                sensitive_url(final) is None and \
                await self.fetcher.robots_decision(final) == "ALLOWED"
            if not ok:
                fc.update(error_kind="REDIRECT_REJECTED",
                          error_message="final URL not public or not allowed by robots.txt")
                esc["recovery_status"] = "REDIRECT_REJECTED"
                return None, estimate
        raw = result.raw_html.encode("utf-8")
        if is_pdf:
            pages, basis = pdf_pages(result)
            parsed = len(pages) if pages else None
            cov = coverage(result, max_pages, parsed)
            esc.update(cov, page_basis=basis, representation="MARKDOWN",
                       representation_reason={"rule": "PDF: Firecrawl's parsed text"})
            text = "\n\n".join(t for _, t in pages) if pages else re.sub(
                r"<!--\s*page\s+\d+\s*-->", "", result.markdown).strip()
            doc = AcquiredDocument(url=final, title=fc["title"], media_type="application/pdf",
                                   text=text, acquisition_method="FIRECRAWL_PDF", pages=pages)
            fc["page_count"] = cov["pages_parsed"]
        else:
            html_doc = parse_html(final, raw, "text/html") if raw.strip() else None
            choice, why = select_representation(result.markdown,
                                                html_doc.text if html_doc else "")
            esc.update(representation=choice, representation_reason=why)
            if choice == "RAW_HTML_EXTRACT" and html_doc is not None:
                doc = html_doc
                doc.acquisition_method = "FIRECRAWL"
            else:
                doc = AcquiredDocument(url=final, title=fc["title"], media_type="text/markdown",
                                       text=result.markdown if choice else "",
                                       acquisition_method="FIRECRAWL")
        doc.retrieved_at = now
        text = doc.text or ""
        # On what Firecrawl returned, before selection: a challenge page is a bot check even
        # when neither representation is long enough to be chosen.
        if is_bot_check(raw or result.markdown.encode("utf-8"), result.markdown) or (
                text and is_bot_check(text.encode("utf-8"), text)):
            fc.update(error_kind="BOT_CHECK", error_message="challenge page instead of the "
                                                            "document")
            esc["recovery_status"] = "BOT_CHECK"
            return None, estimate
        if not text.strip():
            fc["status"] = "EMPTY"
            esc["recovery_status"] = "EMPTY"
            return None, estimate
        doc.content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        fc.update(status="OK", acquisition_method=doc.acquisition_method,
                  content_sha256=doc.content_sha256, byte_size=len(raw) or len(text),
                  source_date=doc.source_date or fc.get("source_date"))
        esc["recovery_status"] = "PARTIAL" if esc.get("coverage") == "PARTIAL" else "RECOVERED"
        await self._store_raw(fc, raw, result.markdown)
        return doc, estimate

    async def _store_raw(self, fc: dict[str, Any], raw: bytes, markdown: str) -> None:
        if self.artifacts is None:
            return
        try:
            md = markdown.encode("utf-8")
            if md:
                fc["escalation"]["markdown_object_key"] = await asyncio.to_thread(
                    self.artifacts.put, md, hashlib.sha256(md).hexdigest(), "text/markdown")
            if raw:
                fc["raw_object_key"] = await asyncio.to_thread(
                    self.artifacts.put, raw, hashlib.sha256(raw).hexdigest(), "text/html")
            else:
                fc["raw_object_key"] = fc["escalation"].get("markdown_object_key")
        except Exception as exc:  # noqa: BLE001 - keep the evidence, record the failure
            fc["artifact_error"] = f"{type(exc).__name__}: {exc}"[:500]
