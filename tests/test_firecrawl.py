"""Firecrawl fetch fallback (acquisition/firecrawl.py, escalation.py, firecrawl_store.py).
Offline: the target sites and Firecrawl are MockTransports; no real key or request."""

import asyncio
import json
import httpx
import pytest
from acquisition_helpers import MemoryArtifacts, Resolver, Site, candidate, make_fetcher
from procedure_helpers import make_engine
from sqlalchemy import select
from sqlalchemy.orm import Session
from water_source_research.acquisition import escalation as escalation_module
from water_source_research.acquisition.config import AcquisitionSettings
from water_source_research.acquisition.escalation import FirecrawlEscalator, retry_reason
from water_source_research.acquisition.firecrawl import (
    FirecrawlClient, FirecrawlResult, coverage, pdf_pages, select_representation,
    sensitive_url,
)
from water_source_research.acquisition.firecrawl_store import FirecrawlAttemptStore
from water_source_research.acquisition.service import AcquisitionService, build_acquisition_service
from water_source_research.core.config import Settings
from water_source_research.db.evidence_models import (
    EvidenceChunkRecord, FirecrawlAttemptRecord, SourceDocumentRecord,
)
from water_source_research.services.evidence_store import EvidenceRepository

KEY = "fc-TESTKEY-never-shown-123"
SHELL = (b"<html><head><title>App</title><script src='/app.js'></script></head><body>"
         b"<nav><a href='/'>Home</a> <a href='/about'>About</a></nav>"
         b"<div id='root'></div><script>window.__APP__={}</script></body></html>")
# What the direct fetcher gets from a JavaScript-rendered page: an empty shell.
BARE = (b"<html><head><title>App</title><script src='/app.js'></script></head><body>"
        b"<div id='root'></div><script>window.__APP__={}</script></body></html>")
WATER_MD = ("# Water supply\n\n[Home](/) | [About](/about)\n\n"
            "The city's drinking water comes from the Example River and the Sample aquifer. "
            "The utility treats about 300,000 cubic metres of water per day at two plants. "
            "Water from the river is pumped to the treatment works before distribution. "
            "Groundwater wells supply the eastern districts during the dry season.\n")
OTHER_MD = ("# Annual report\n\nThe agency publishes monthly bulletins for residents. "
            "Its offices are open on weekdays from nine to five o'clock. "
            "Applications for permits are processed within thirty days of receipt. "
            "Please contact the front desk for any questions about the services offered.\n")

async def _no_sleep(_):
    return None

class FakeFirecrawl:
    """Firecrawl API double: url -> response builder; records every request body."""

    def __init__(self):
        self.routes = {}
        self.bodies = []
        self.headers = []
        self.remaining = 1000.0
        self.delay = 0.0

    def ok(self, target, markdown=WATER_MD, raw_html=None, final=None, **metadata):
        raw_html = SHELL.decode() if raw_html is None else raw_html
        meta = {"url": final or target, "sourceURL": target, "statusCode": 200,
                "contentType": "text/html", "title": "Water", **metadata}
        self.routes[target] = lambda: httpx.Response(200, json={
            "success": True, "data": {"markdown": markdown, "rawHtml": raw_html,
                                      "metadata": meta}})
        return self

    def raw(self, url, fn):
        self.routes[url] = fn
        return self

    async def __call__(self, request):
        if request.url.path.endswith("/team/credit-usage"):
            return httpx.Response(200, json={"success": True,
                                             "data": {"remainingCredits": self.remaining}})
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(dict(request.headers))
        if self.delay:
            await asyncio.sleep(self.delay)
        route = self.routes.get(body["url"])
        if route is None:
            return httpx.Response(500, json={"success": False, "code": "UNKNOWN_ERROR"})
        response = route()
        if response.status_code == 200:
            self.remaining -= 1
        return response

    @property
    def sent(self):
        return [b["url"] for b in self.bodies]

def build(tmp_path, site, fake, cap=20, resolver=None, artifacts=None):
    engine = make_engine(tmp_path, "fc.db")
    s = AcquisitionSettings(per_host_min_interval_seconds=0)
    fetcher = make_fetcher(site, resolver, s)
    client = FirecrawlClient(KEY, client=httpx.AsyncClient(
        transport=httpx.MockTransport(fake)), sleep=_no_sleep)
    store = FirecrawlAttemptStore(engine)
    esc = FirecrawlEscalator(client, store, fetcher, cap=cap, pdf_max_pages=8,
                             artifacts=artifacts or MemoryArtifacts())
    svc = AcquisitionService(s, fetcher=fetcher, artifact_store=MemoryArtifacts(),
                             firecrawl=esc)
    return svc, store, engine

def run(svc, urls, previous=(), round_no=1, run_id="run-1"):
    return asyncio.run(svc.acquire_round([candidate(u, source_type="GOVERNMENT") for u in urls],
                                         list(previous),
                                         round_no=round_no, run_id=run_id))

def by_attempt(records, attempt):
    return [r for r in records if r.get("attempt") == attempt]

# --- off by default ---------------------------------------------------------------------

def test_disabled_unless_flag_key_and_store(tmp_path):
    engine = make_engine(tmp_path, "x.db")
    base = {"_env_file": None}
    assert build_acquisition_service(Settings(**base), None, engine).firecrawl is None
    assert build_acquisition_service(Settings(**base, firecrawl_api_key=KEY), None,
                                     engine).firecrawl is None  # key alone: off
    flag = {"acquisition": AcquisitionSettings(firecrawl_enabled=True)}
    assert build_acquisition_service(Settings(**base, **flag), None,
                                     engine).firecrawl is None  # flag alone: off
    assert build_acquisition_service(Settings(**base, **flag, firecrawl_api_key=KEY), None,
                                     None).firecrawl is None  # no attempt store: off
    on = build_acquisition_service(Settings(**base, **flag, firecrawl_api_key=KEY), None,
                                   engine).firecrawl
    assert on is not None and on.cap == 20  # the lower of 20 and the per-city setting (100)
    assert KEY not in repr(on.client)

def test_without_firecrawl_the_outcome_is_unchanged(tmp_path):
    site = Site().html("https://city.example/water", BARE)
    fake = FakeFirecrawl().ok("https://city.example/water")
    svc, _, _ = build(tmp_path, site, fake)
    plain = AcquisitionService(svc.s, fetcher=make_fetcher(site, None, svc.s))
    a = run(plain, ["https://city.example/water"])
    assert [r["status"] for r in a.records] == ["EMPTY"] and not a.chunks
    assert fake.sent == []

# --- recovery and content selection -------------------------------------------------------

def test_js_shell_page_recovered_from_markdown_and_direct_record_kept(tmp_path):
    """Regression: rawHtml is a non-empty shell with no body text; the markdown holds a
    water-source passage -> MARKDOWN chosen, chunked, reason recorded."""
    url = "https://city.example/water"
    site = Site().html(url, BARE)
    fake = FakeFirecrawl().ok(url)
    svc, store, _ = build(tmp_path, site, fake)
    out = run(svc, [url])
    direct, = by_attempt(out.records, "direct")
    fc, = by_attempt(out.records, "firecrawl")
    assert direct["status"] == "EMPTY" and direct["error_kind"] is None  # unchanged
    assert fc["status"] == "OK" and fc["acquisition_method"] == "FIRECRAWL"
    esc = fc["escalation"]
    assert esc["recovery_status"] == "RECOVERED" and esc["retry_reason"] == "EMPTY"
    assert esc["representation"] == "MARKDOWN"
    assert esc["representation_reason"]["raw_html_extract"]["body_chars"] < 200
    assert esc["representation_reason"]["markdown"]["water_terms"] >= 3
    assert esc["from"] == {"status": "EMPTY", "error_kind": None, "error_message": "",
                           "duration_ms": direct["duration_ms"]}
    assert any("Example River" in c.text for c in out.chunks)
    assert all(c.url == url for c in out.chunks)
    assert esc["credits_reserved"] == 2.0 and esc["credits_estimated"] == 1.0
    assert esc["credits_actual"] is None  # never reported per request
    assert store.used("run-1") == 1.0
    body = fake.bodies[0]
    assert body["proxy"] == "basic" and body["maxAge"] == 0 and body["storeInCache"] is False
    assert body["onlyMainContent"] is False and body["formats"] == ["markdown", "rawHtml"]
    assert body["parsers"][0]["maxPages"] == 1  # HTML expected: an unexpected PDF stays small

def test_both_representations_useful_prefers_the_richer_one():
    rich = ("Water from the Example River is treated. The supply serves the city. "
            "Drinking water quality is tested daily. Reservoir levels are reported weekly. "
            "Treatment plants filter and disinfect the water before it reaches the network.\n"
            "| Source | Share |\n| River | 80% |\n| Wells | 20% |\n")
    choice, why = select_representation(OTHER_MD, rich)
    assert choice == "RAW_HTML_EXTRACT" and why["rule"].startswith("both useful")
    choice, _ = select_representation(rich, OTHER_MD)
    assert choice == "MARKDOWN"
    assert select_representation("short", "")[0] is None

# --- what is retried ------------------------------------------------------------------

@pytest.mark.parametrize(("setup", "retried"), [
    (lambda s, u: s.status(u, 503), True),
    (lambda s, u: s.status(u, 429), True),
    (lambda s, u: s.status(u, 404), False),
    (lambda s, u: s.status(u, 401), False),
    (lambda s, u: s.status(u, 403), False),
    (lambda s, u: s.html(u, b"<html><body>Attention Required! | Cloudflare</body></html>"),
     False),  # bot check
    (lambda s, u: s.html("https://city.example/robots.txt", b"").routes.update({
        "https://city.example/robots.txt": (200, {}, b"User-agent: *\nDisallow: /")}), False),
    (lambda s, u: s.routes.update({"https://city.example/robots.txt": (503, {}, b"")}), False),
])
def test_only_retryable_failures_with_robots_allowed_are_sent(tmp_path, setup, retried):
    url = "https://city.example/page"
    site = Site()
    setup(site, url)
    fake = FakeFirecrawl().ok(url)
    svc, _, _ = build(tmp_path, site, fake)
    out = run(svc, [url])
    assert (fake.sent == [url]) is retried
    assert bool(by_attempt(out.records, "firecrawl")) is retried

def test_retry_reason_codes():
    assert retry_reason({"status": "EMPTY", "error_kind": None}) == "EMPTY"
    assert retry_reason({"status": "FAILED", "error_kind": "TIMEOUT"}) == "TIMEOUT"
    assert retry_reason({"status": "FAILED", "error_kind": "HTTP_STATUS",
                         "error_message": "HTTP 502"}) == "HTTP_5XX"
    for kind in ("AUTH_REQUIRED", "BOT_CHECK", "ROBOTS_DISALLOWED", "ROBOTS_UNREACHABLE",
                 "TOO_LARGE", "INVALID_URL", "PARSE_ERROR"):
        assert retry_reason({"status": "FAILED", "error_kind": kind}) is None

def test_sensitive_and_private_urls_are_never_sent(tmp_path):
    assert sensitive_url("https://a.example/x?token=abc") and \
        sensitive_url("https://a.example/x?X-Amz-Signature=1") and \
        sensitive_url("https://a.example/x?sid=9") and \
        sensitive_url("https://a.example/x?page=2&lang=es") is None
    url = "https://city.example/doc?session=abc123"
    site = Site().html(url, BARE)
    fake = FakeFirecrawl().ok(url)
    svc, _, _ = build(tmp_path, site, fake)
    out = run(svc, [url])
    assert fake.sent == [] and not by_attempt(out.records, "firecrawl")
    assert out.records[0]["escalation"]["firecrawl"] == "NOT_SENT_SENSITIVE_URL"

def test_redirect_to_a_private_address_is_discarded(tmp_path):
    url = "https://city.example/water"
    site = Site().html(url, BARE)
    fake = FakeFirecrawl().ok(url, final="https://internal.example/x")
    resolver = Resolver({"internal.example": ["10.0.0.5"]})
    svc, _, _ = build(tmp_path, site, fake, resolver=resolver)
    out = run(svc, [url])
    fc, = by_attempt(out.records, "firecrawl")
    assert fc["status"] == "FAILED" and fc["error_kind"] == "REDIRECT_REJECTED"
    assert not out.chunks

def test_target_refusal_and_challenge_page_are_not_evidence(tmp_path):
    a, b = "https://city.example/a", "https://city.example/b"
    site = Site().html(a, BARE).html(b, BARE)
    fake = FakeFirecrawl().ok(a, statusCode=403).ok(
        b, markdown="Just a moment... checking your browser before accessing", raw_html="")
    svc, _, _ = build(tmp_path, site, fake)
    out = run(svc, [a, b])
    kinds = {r["requested_url"]: r["error_kind"] for r in by_attempt(out.records, "firecrawl")}
    assert kinds == {a: "TARGET_STATUS", b: "BOT_CHECK"} and not out.chunks

# --- credits ---------------------------------------------------------------------------

def test_concurrent_reservations_never_exceed_the_cap_and_count_across_rounds(tmp_path):
    urls = [f"https://city{i}.example/water" for i in range(12)]
    site = Site()
    fake = FakeFirecrawl()
    for u in urls:
        site.html(u, BARE)
        fake.ok(u)
    fake.delay = 0.05
    svc, store, engine = build(tmp_path, site, fake, cap=5)
    svc.s.max_documents_per_round = 12
    svc.s.max_documents_total = 24
    svc.s.max_per_host = 2
    first = run(svc, urls[:8])
    assert store.used("run-1") <= 5
    sent_first = len(fake.sent)
    assert 1 <= sent_first <= 4
    notes = [r.get("escalation", {}).get("firecrawl") for r in by_attempt(first.records,
                                                                          "direct")]
    assert "BUDGET" in notes
    second = run(svc, urls[8:], previous=first.records, round_no=2)
    assert store.used("run-1") <= 5 and len(fake.sent) - sent_first <= 5 - sent_first
    with Session(engine) as s:
        rows = s.scalars(select(FirecrawlAttemptRecord)).all()
    assert all(r.state == "SETTLED" for r in rows) and len(rows) == len(fake.sent)
    assert by_attempt(second.records, "direct")

def test_uncertain_outcome_keeps_its_reservation(tmp_path):
    url = "https://city.example/water"
    site = Site().html(url, BARE)

    def timeout():
        raise httpx.ReadTimeout("slow")
    fake = FakeFirecrawl().raw(url, timeout)
    svc, store, _ = build(tmp_path, site, fake)
    out = run(svc, [url])
    fc, = by_attempt(out.records, "firecrawl")
    assert fc["escalation"]["recovery_status"] == "UNCERTAIN"
    assert fc["escalation"]["credits_estimated"] is None
    assert store.used("run-1") == 2.0  # the reservation, never released to zero

def test_a_restarted_worker_never_repeats_a_pending_request(tmp_path):
    url = "https://city.example/water"
    site = Site().html(url, BARE)
    fake = FakeFirecrawl().ok(url)
    svc, store, engine = build(tmp_path, site, fake)
    from water_source_research.discovery.normalize import canonical_url
    attempt_id, status, _ = store.reserve("run-1", canonical_url(url), 1, 2.0, 20)
    assert status == "RESERVED"  # the process "died" before settling
    out = run(svc, [url])
    assert fake.sent == []
    assert out.records[0]["escalation"]["firecrawl"] == "ALREADY_ATTEMPTED"
    with Session(engine) as s:
        row = s.get(FirecrawlAttemptRecord, attempt_id)
    assert row.state == "UNCERTAIN" and row.outcome == "PROCESS_ENDED"
    assert store.used("run-1") == 2.0

def test_no_credits_turns_firecrawl_off_for_the_run(tmp_path):
    a, b = "https://city.example/a", "https://city.example/b"
    site = Site().html(a, BARE).html(b, BARE)
    fake = FakeFirecrawl().raw(a, lambda: httpx.Response(402, json={
        "success": False, "error": "Payment required", "code": "insufficient_credits"}))
    fake.ok(b)
    svc, store, _ = build(tmp_path, site, fake)
    first = run(svc, [a])
    fc, = by_attempt(first.records, "firecrawl")
    assert fc["error_kind"] == "FIRECRAWL_CREDITS_EXHAUSTED" and store.used("run-1") == 0
    second = run(svc, [b], previous=first.records, round_no=2)
    assert fake.sent == [a]
    assert by_attempt(second.records, "direct")[0]["escalation"]["firecrawl"].startswith(
        "DISABLED")

# --- PDFs ------------------------------------------------------------------------------

def test_pdf_pages_from_firecrawl_and_truncation_is_partial(tmp_path):
    url = "https://city.example/report.pdf"
    site = Site().status(url, 503)
    fake = FakeFirecrawl()
    page = ("Page {n}: the Example River supplies the treatment plant. Water demand is "
            "rising. The reservoir stores raw water. Wells supply the eastern districts.")
    fake.raw(url, lambda: httpx.Response(200, json={"success": True, "data": {
        "markdown": "x", "rawHtml": "", "pages": [
            {"pageNumber": 1, "markdown": page.format(n=1)},
            {"pageNumber": 2, "markdown": page.format(n=2)}],
        "metadata": {"url": url, "statusCode": 200, "contentType": "application/pdf",
                     "numPages": 2, "totalPages": 5}}}))
    svc, store, _ = build(tmp_path, site, fake)
    out = run(svc, [url])
    fc, = by_attempt(out.records, "firecrawl")
    esc = fc["escalation"]
    assert fc["status"] == "OK" and fc["acquisition_method"] == "FIRECRAWL_PDF"
    assert esc["recovery_status"] == "PARTIAL" and esc["coverage"] == "PARTIAL"
    assert esc["pages_parsed"] == 2 and esc["pages_total"] == 5 and esc["truncated"]
    assert esc["page_basis"] == "pages"
    assert {c.page_number for c in out.chunks} == {1, 2}
    assert esc["credits_reserved"] == 9.0 and esc["credits_estimated"] == 3.0
    assert fake.bodies[0]["parsers"][0] == {"type": "pdf", "mode": "auto", "maxPages": 8,
                                            "pages": True, "pageMarkers": True}
    assert store.used("run-1") == 3.0

def test_page_markers_are_a_fallback_and_page_numbers_are_never_invented():
    r = FirecrawlResult(markdown="intro\n<!-- page 3 -->\nthree\n<!-- page 4 -->\nfour",
                        raw_html="", metadata={"numPages": 2}, pages=None)
    pages, basis = pdf_pages(r)
    assert basis == "markers" and pages == [(3, "intro\n\nthree"), (4, "four")]
    plain = FirecrawlResult(markdown="no markers", raw_html="", metadata={}, pages=None)
    assert pdf_pages(plain) == (None, "none")
    assert coverage(plain, 8, None)["coverage"] == "FULL"
    capped = FirecrawlResult(markdown="", raw_html="", metadata={"numPages": 8}, pages=None)
    assert coverage(capped, 8, 8)["coverage"] == "PARTIAL"  # cap reached, total unknown

# --- storage and secrets ----------------------------------------------------------------

def test_direct_and_firecrawl_records_are_both_stored_and_chunks_attach_to_firecrawl(
        tmp_path):
    url = "https://city.example/water"
    site = Site().html(url, BARE)
    fake = FakeFirecrawl().ok(url)
    svc, _, engine = build(tmp_path, site, fake)
    out = run(svc, [url])
    repo = EvidenceRepository(engine)
    repo.save_documents("run-1", out.records, out.chunks)
    repo.save_documents("run-1", out.records, out.chunks)  # a resumed round: no duplicates
    with Session(engine) as s:
        docs = s.scalars(select(SourceDocumentRecord)).all()
        chunks = s.scalars(select(EvidenceChunkRecord)).all()
    assert sorted(d.attempt for d in docs) == ["direct", "firecrawl"]
    fc = next(d for d in docs if d.attempt == "firecrawl")
    assert chunks and {c.document_id for c in chunks} == {fc.id}
    assert fc.escalation["recovery_status"] == "RECOVERED"

def test_a_firecrawl_copy_of_an_acquired_document_is_a_duplicate(tmp_path):
    good, shell = "https://city.example/good", "https://city.example/shell"
    page = b"<html><body><article>" + WATER_MD.encode() * 2 + b"</article></body></html>"
    site = Site().html(good, page).html(shell, BARE)
    fake = FakeFirecrawl().ok(shell, final=good)
    svc, _, _ = build(tmp_path, site, fake)
    out = run(svc, [good, shell])
    fc, = by_attempt(out.records, "firecrawl")
    assert fc["status"] == "SKIPPED_DUPLICATE"
    assert fc["escalation"]["recovery_status"] == "DUPLICATE"

def test_the_key_never_appears_in_records_events_or_ledger(tmp_path, monkeypatch):
    rows = []
    monkeypatch.setattr(escalation_module, "record_usage", rows.append)
    url = "https://city.example/water"
    site = Site().html(url, BARE)
    fake = FakeFirecrawl().ok(url)
    svc, _, _ = build(tmp_path, site, fake)
    out = run(svc, [url])
    assert fake.headers[0]["authorization"] == f"Bearer {KEY}"
    dumped = json.dumps(out.records, default=str) + json.dumps(rows, default=str)
    assert KEY not in dumped
    row, = rows
    assert row["provider"] == "firecrawl" and row["kind"] == "CRAWL"
    assert row["credits"] is None and row["credits_estimated"] == 1.0

def test_usage_summary_keeps_estimated_and_unknown_actual_credits_apart():
    from types import SimpleNamespace
    from water_source_research.observability.monitoring import summarize_usage
    def row(**kw):
        base = {"cache_hit": False, "requests": 1, "retries": 0, "error": None,
                "attempt_errors": None, "input_tokens": None, "output_tokens": None,
                "cached_tokens": None, "latency_ms": 900, "cost_usd": None,
                "cache_layer": None, "cache_source": None, "kind": "CRAWL", "credits": None,
                "credits_estimated": 1.0}
        return SimpleNamespace(**{**base, **kw})
    u = summarize_usage([row(), row(credits_estimated=2.0), row(kind="LLM",
                                                                credits_estimated=None)])
    assert u["credit_calls"] == 2 and u["credits_estimated"] == 3.0
    assert u["credits_actual"] is None and u["unknown_actual_credit_calls"] == 2

def test_a_reported_charge_is_recorded_apart_and_the_budget_takes_the_higher(tmp_path,
                                                                             monkeypatch):
    rows = []
    monkeypatch.setattr(escalation_module, "record_usage", rows.append)
    url = "https://city.example/water"
    site = Site().html(url, BARE)
    fake = FakeFirecrawl().ok(url, creditsUsed=2, proxyUsed="basic")
    svc, store, _ = build(tmp_path, site, fake)
    out = run(svc, [url])
    esc = by_attempt(out.records, "firecrawl")[0]["escalation"]
    assert esc["credits_estimated"] == 1.0 and esc["credits_actual"] == 2.0
    assert esc["proxy_used"] == "basic"
    assert store.used("run-1") == 2.0
    assert rows[0]["credits"] == 2.0 and rows[0]["credits_estimated"] == 1.0
    assert rows[0]["attempt_errors"] is None
