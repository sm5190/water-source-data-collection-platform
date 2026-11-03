"""Access hygiene (Milestone 3.1 Phase 1): challenge pages are failures, never bypassed; a host
that failed twice is skipped for the rest of the run; doi.org failures count per DOI prefix."""

import asyncio
from acquisition_helpers import Site, candidate, html_page, make_acquisition
from water_source_research.acquisition.service import failing_hosts, failure_key, is_bot_check

TEXT = ("Aguas Andinas abastece de agua potable a la mayor parte de Santiago, Chile, desde el "
        "río Maipo y el río Mapocho.")
INCAPSULA = (b"<html><head><META NAME=\"robots\" CONTENT=\"noindex,nofollow\"><script "
             b"src=\"/_Incapsula_Resource?SWJIYLWA=5074a744\"></script></head><body>Request "
             b"unsuccessful. Incapsula incident ID: 123-456</body></html>")

def test_an_incapsula_page_is_a_bot_check_not_a_document():
    url = "https://www.aguasandinas.cl/web/aguasandinas/nosotros"
    out = asyncio.run(make_acquisition(Site().html(url, INCAPSULA)).acquire_round(
        [candidate(url)], []))
    [rec] = out.records
    assert rec["status"] == "FAILED" and rec["error_kind"] == "BOT_CHECK"
    assert out.chunks == []

def test_a_document_that_mentions_captcha_is_still_a_document():
    long_text = " ".join([TEXT] * 30) + " The form uses a captcha."
    assert not is_bot_check(b"<html>g-recaptcha</html>", long_text)
    assert is_bot_check(b"<title>Just a moment...</title>", "Just a moment...")

def rec(url, status="FAILED", kind="AUTH_REQUIRED"):
    return {"requested_url": url, "status": status, "error_kind": kind}

def test_two_failures_skip_the_host_for_the_rest_of_the_run():
    assert failing_hosts([rec("https://www.mdpi.com/a")]) == {}
    two = failing_hosts([rec("https://www.mdpi.com/a"), rec("https://mdpi.com/b", "EMPTY", None)])
    assert list(two) == ["mdpi.com"]
    assert failing_hosts([rec("https://x.example/a", kind="TIMEOUT")] * 2) == {}  # transient

def test_doi_failures_count_against_the_doi_prefix():
    assert failure_key("https://doi.org/10.3390/w12030845") == "doi:10.3390"
    assert failure_key("https://www.mdpi.com/2073-4441/12/3/845") == "mdpi.com"
    skip = failing_hosts([rec("https://doi.org/10.3390/a", kind="BOT_CHECK"),
                          rec("https://doi.org/10.3390/b", kind="AUTH_REQUIRED")])
    assert list(skip) == ["doi:10.3390"]

def test_skipped_hosts_are_not_fetched_again():
    ok = "https://scielo.example/article"
    site = Site().html(ok, html_page("SciELO", TEXT))
    previous = [rec("https://doi.org/10.3390/a"), rec("https://doi.org/10.3390/b")]
    out = asyncio.run(make_acquisition(site).acquire_round(
        [candidate("https://doi.org/10.3390/c"), candidate(ok)], previous))
    assert [r["requested_url"] for r in out.records] == [ok]
    assert out.skipped_failing == {"doi:10.3390": "2 fetches failed (AUTH_REQUIRED)"}
