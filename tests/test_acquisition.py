import asyncio
from itertools import pairwise
import pytest
from acquisition_helpers import (
    MemoryArtifacts,
    Resolver,
    Site,
    candidate,
    html_page,
    make_acquisition,
    make_fetcher,
    make_pdf,
)
from water_source_research.acquisition.base import AcquiredDocument
from water_source_research.acquisition.config import AcquisitionSettings
from water_source_research.acquisition.http import DirectHTTPAcquirer
from water_source_research.acquisition.pdf import parse_pdf
from water_source_research.acquisition.safe_http import (
    FetchError,
    FetchErrorKind,
    is_public_address,
    validate_url,
)
from water_source_research.acquisition.service import select_candidates
from water_source_research.services.evidence import chunk_document

UTIL = "https://water.example.gov/drinking-water"
PDF_URL = "https://water.example.gov/report-2023.pdf"
WATER_TEXT = ("Toronto Water supplies drinking water drawn from Lake Ontario through four "
              "treatment plants serving residents of the City of Toronto.")

def run(coro):
    return asyncio.run(coro)

# --- URL / SSRF validation -----------------------------------------------------------

@pytest.mark.parametrize("url", ["ftp://x.org/a", "file:///etc/passwd", "http://x.org:8080/",
                                 "https://user:pw@x.org/", "javascript:alert(1)", "https:///x"])
def test_invalid_urls_rejected(url):
    with pytest.raises(FetchError) as e:
        validate_url(url)
    assert e.value.kind is FetchErrorKind.INVALID_URL

@pytest.mark.parametrize("addr,public", [
    ("93.184.216.34", True), ("10.0.0.5", False), ("127.0.0.1", False), ("169.254.169.254", False),
    ("192.168.1.1", False), ("::1", False), ("fe80::1", False), ("::ffff:127.0.0.1", False),
    ("0.0.0.0", False), ("100.64.0.1", False), ("2606:4700::1111", True)])
def test_public_address_classification(addr, public):
    assert is_public_address(addr) is public

def test_private_host_is_blocked_before_any_request():
    site = Site().html("https://intranet.example/", html_page("x", WATER_TEXT))
    fetcher = make_fetcher(site, Resolver({"intranet.example": ["10.1.2.3"]}))
    with pytest.raises(FetchError) as e:
        run(fetcher.fetch("https://intranet.example/", 10_000))
    assert e.value.kind is FetchErrorKind.BLOCKED_ADDRESS
    assert site.requests == []

def test_literal_metadata_ip_blocked():
    site = Site()
    with pytest.raises(FetchError) as e:
        run(make_fetcher(site).fetch("http://169.254.169.254/latest/meta-data", 10_000))
    assert e.value.kind is FetchErrorKind.BLOCKED_ADDRESS
    assert site.requests == []

def test_redirect_to_private_address_blocked():
    site = Site().redirect(UTIL, "http://127.0.0.1/admin")
    with pytest.raises(FetchError) as e:
        run(make_fetcher(site).fetch(UTIL, 10_000))
    assert e.value.kind is FetchErrorKind.BLOCKED_ADDRESS
    assert "http://127.0.0.1/admin" not in site.requests

def test_redirects_followed_and_bounded():
    site = Site().redirect(UTIL, "/final").html("https://water.example.gov/final",
                                                html_page("Final", WATER_TEXT))
    r = run(make_fetcher(site).fetch(UTIL, 100_000))
    assert r.final_url == "https://water.example.gov/final"
    loop = Site().redirect("https://a.example/1", "https://a.example/2") \
        .redirect("https://a.example/2", "https://a.example/1")
    with pytest.raises(FetchError) as e:
        run(make_fetcher(loop).fetch("https://a.example/1", 100_000))
    assert e.value.kind is FetchErrorKind.TOO_MANY_REDIRECTS

# --- robots / auth / size ----------------------------------------------------------------

def test_robots_disallow_is_respected():
    site = Site({"https://water.example.gov/robots.txt":
                 (200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /drinking")})
    site.html(UTIL, html_page("x", WATER_TEXT))
    with pytest.raises(FetchError) as e:
        run(make_fetcher(site).fetch(UTIL, 100_000))
    assert e.value.kind is FetchErrorKind.ROBOTS_DISALLOWED
    assert UTIL not in site.requests

def test_robots_server_error_skips_host():
    site = Site().status("https://water.example.gov/robots.txt", 503).html(
        UTIL, html_page("x", WATER_TEXT))
    with pytest.raises(FetchError) as e:
        run(make_fetcher(site).fetch(UTIL, 100_000))
    assert e.value.kind is FetchErrorKind.ROBOTS_DISALLOWED

def test_robots_fetched_once_per_host():
    site = Site().html(UTIL, html_page("a", WATER_TEXT)).html(
        "https://water.example.gov/b", html_page("b", WATER_TEXT))
    fetcher = make_fetcher(site)
    run(fetcher.fetch(UTIL, 100_000))
    run(fetcher.fetch("https://water.example.gov/b", 100_000))
    assert site.requests.count("https://water.example.gov/robots.txt") == 1

@pytest.mark.parametrize("code", [401, 403])
def test_auth_walls_not_bypassed(code):
    site = Site().status(UTIL, code)
    with pytest.raises(FetchError) as e:
        run(make_fetcher(site).fetch(UTIL, 100_000))
    assert e.value.kind is FetchErrorKind.AUTH_REQUIRED
    assert site.fetched().count(UTIL) == 1  # no retry, no workaround

def test_size_limit_enforced():
    site = Site().html(UTIL, b"<html>" + b"x" * 5000 + b"</html>")
    with pytest.raises(FetchError) as e:
        run(make_fetcher(site).fetch(UTIL, 1000))
    assert e.value.kind is FetchErrorKind.TOO_LARGE

# --- parsing -------------------------------------------------------------------------

def test_html_acquisition_with_existing_acquirer():
    site = Site().html(UTIL, html_page("Toronto Water", WATER_TEXT))
    doc = run(DirectHTTPAcquirer(make_fetcher(site)).acquire(UTIL))
    assert doc.acquisition_method == "DIRECT_HTTP"
    assert doc.title == "Toronto Water"
    assert "Lake Ontario" in doc.text
    assert doc.retrieved_at and doc.content_sha256

def test_pdf_text_with_page_numbers():
    body = make_pdf([["Annual Water Report 2023", "Introduction page."],
                     ["The city produced an average of 1,000,000 m3 per day in 2023."]],
                    title="Water Report")
    doc = parse_pdf(PDF_URL, body)
    assert doc.acquisition_method == "DIRECT_HTTP_PDF"
    assert [p for p, _ in doc.pages] == [1, 2]
    assert "1,000,000 m3 per day" in doc.pages[1][1]
    assert doc.title == "Water Report"
    assert doc.source_date == "2023-04-15"
    chunks = chunk_document(doc, source_type="UTILITY")
    page2 = [c for c in chunks if "1,000,000" in c.text]
    assert page2 and page2[0].page_number == 2

# --- chunking ------------------------------------------------------------------------

def test_chunk_ids_deterministic_and_overlapping():
    paras = [f"Paragraph {i} " + "word " * 30 for i in range(6)]
    doc = AcquiredDocument(UTIL, "T", "text/html", "\n\n".join(paras), "DIRECT_HTTP",
                           retrieved_at="2026-01-01T00:00:00+00:00")
    a = chunk_document(doc, max_chars=400, overlap_chars=200)
    b = chunk_document(doc, max_chars=400, overlap_chars=200)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert len({c.chunk_id for c in a}) == len(a)
    assert all(len(c.text) <= 400 for c in a)
    # each consecutive pair shares a paragraph
    for x, y in pairwise(a):
        assert x.text.split("\n\n")[-1] == y.text.split("\n\n")[0]
    assert a[0].retrieved_at == "2026-01-01T00:00:00+00:00"
    for c in a:  # verbatim spans of the source text
        for part in c.text.split("\n\n"):
            assert part in doc.text

def test_long_paragraph_split_into_overlapping_windows():
    text = " ".join(f"Sentence number {i} about the water supply." for i in range(100))
    doc = AcquiredDocument(UTIL, "T", "application/pdf", text, "DIRECT_HTTP_PDF",
                           pages=[(3, text)])
    chunks = chunk_document(doc, max_chars=500, overlap_chars=100)
    assert len(chunks) > 1
    assert all(len(c.text) <= 500 and c.page_number == 3 for c in chunks)
    assert all(c.text in text for c in chunks)
    assert chunks[0].text[-40:] in chunks[1].text or chunks[1].text[:20] in chunks[0].text

# --- service: selection, duplicates, failures --------------------------------------------

def test_selection_prefers_official_and_caps_hosts():
    s = AcquisitionSettings(max_documents_per_round=3, max_per_host=1)
    cands = [candidate("https://blog.example/a", source_type="OTHER"),
             candidate("https://uni.example/p", source_type="ACADEMIC"),
             candidate("https://water.gov.example/a", source_type="UTILITY"),
             candidate("https://water.gov.example/b", source_type="UTILITY"),
             candidate("https://gov.example/x", source_type="GOVERNMENT"),
             candidate("https://water.gov.example/data.xlsx", source_type="UTILITY")]
    chosen = select_candidates(cands, set(), s, budget=10)
    assert [c["url"] for c in chosen] == ["https://water.gov.example/a", "https://gov.example/x",
                                          "https://uni.example/p"]

def test_round_acquires_html_and_pdf_and_ignores_snippets():
    site = Site().html(UTIL, html_page("Toronto Water", WATER_TEXT)).pdf(
        PDF_URL, make_pdf([["Cover"], ["Average daily production was 980 ML per day."]]))
    svc = make_acquisition(site)
    out = run(svc.acquire_round([candidate(UTIL), candidate(PDF_URL, published_date="2023")], []))
    assert [r["status"] for r in out.records] == ["OK", "OK"]
    assert {r["acquisition_method"] for r in out.records} == {"DIRECT_HTTP", "DIRECT_HTTP_PDF"}
    assert not any("SNIPPET" in c.text for c in out.chunks)
    pdf_chunks = [c for c in out.chunks if c.url == PDF_URL]
    assert {c.page_number for c in pdf_chunks} == {1, 2}
    assert all(c.source_type == "UTILITY" and c.retrieved_at for c in out.chunks)

def test_one_failure_does_not_fail_the_round():
    site = Site().html(UTIL, html_page("ok", WATER_TEXT)).status(
        "https://down.example/x", 500)
    out = run(make_acquisition(site).acquire_round(
        [candidate("https://down.example/x"), candidate(UTIL)], []))
    by_url = {r["requested_url"]: r for r in out.records}
    assert by_url["https://down.example/x"]["status"] == "FAILED"
    assert by_url["https://down.example/x"]["error_kind"] == "HTTP_STATUS"
    assert by_url[UTIL]["status"] == "OK"
    assert out.chunks

def test_empty_document_yields_no_chunks():
    site = Site().html(UTIL, b"<!doctype html><html><head><title>t</title></head>"
                             b"<body></body></html>")
    out = run(make_acquisition(site).acquire_round([candidate(UTIL)], []))
    assert out.records[0]["status"] == "EMPTY"
    assert out.chunks == []

def test_duplicates_not_refetched_or_reprocessed():
    page = html_page("Toronto Water", WATER_TEXT)
    site = Site().html(UTIL, page).html("https://mirror.example/copy", page) \
        .redirect("https://water.example.gov/alias", UTIL)
    svc = make_acquisition(site)
    first = run(svc.acquire_round([candidate(UTIL), candidate(UTIL + "/")], []))
    assert len(first.records) == 1  # canonical duplicate in the same round
    second = run(svc.acquire_round(
        [candidate(UTIL), candidate("https://mirror.example/copy"),
         candidate("https://water.example.gov/alias")], first.records, round_no=2))
    assert site.fetched().count(UTIL) == 2  # once in round 1, once via the alias redirect
    statuses = {r["requested_url"]: r["status"] for r in second.records}
    assert UTIL not in statuses  # already attempted: not selected again
    assert statuses["https://mirror.example/copy"] == "SKIPPED_DUPLICATE"  # same content
    assert statuses["https://water.example.gov/alias"] == "SKIPPED_DUPLICATE"  # same final URL
    assert second.chunks == []

def test_run_budget_limits_documents():
    site = Site()
    cands = []
    for i in range(5):
        url = f"https://h{i}.example/p"
        site.html(url, html_page(f"p{i}", WATER_TEXT + f" Page {i}."))
        cands.append(candidate(url))
    svc = make_acquisition(site, max_documents_per_round=2, max_documents_total=3)
    r1 = run(svc.acquire_round(cands, []))
    r2 = run(svc.acquire_round(cands, r1.records, 2))
    r3 = run(svc.acquire_round(cands, r1.records + r2.records, 3))
    assert (len(r1.records), len(r2.records), len(r3.records)) == (2, 1, 0)

def test_raw_artifacts_stored_and_failure_tolerated():
    site = Site().html(UTIL, html_page("Toronto Water", WATER_TEXT))
    store = MemoryArtifacts()
    out = run(make_acquisition(site, artifact_store=store).acquire_round([candidate(UTIL)], []))
    key = out.records[0]["raw_object_key"]
    assert key and key.endswith(".html") and store.objects[key].startswith(b"<!doctype")
    site2 = Site().html(UTIL, html_page("Toronto Water", WATER_TEXT))
    out2 = run(make_acquisition(site2, artifact_store=MemoryArtifacts(fail=True))
               .acquire_round([candidate(UTIL)], []))
    assert out2.records[0]["status"] == "OK" and out2.chunks
    assert out2.records[0]["raw_object_key"] is None
    assert "minio down" in out2.records[0]["artifact_error"]
