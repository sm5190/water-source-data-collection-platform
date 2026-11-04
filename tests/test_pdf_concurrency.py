"""PDFium is not thread-safe within a process: concurrent parses failed with "Failed to load
document (PDFium: Data format error)" (2026-11-04, 16 PDFs; reproduced with the stored copies:
0/9 errors sequential, 20/27 with 2 threads, 0/108 across 3 separate processes).
parse_pdf serializes the PDFium session per process; these tests hold it to that."""

import asyncio
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pypdfium2
from acquisition_helpers import MemoryArtifacts, Site, candidate, make_acquisition, make_pdf
from water_source_research.acquisition.pdf import parse_pdf

def _pdfs(n: int = 6, pages: int = 40) -> list[bytes]:
    return [make_pdf([[f"Report {d} page {p}: the Example River supplies the city.",
                       f"Withdrawals were {1000 + d * p} cubic metres per day."]
                      for p in range(pages)], title=f"Report {d}") for d in range(n)]

def test_parallel_parses_in_one_process_all_succeed_and_match_sequential():
    blobs = _pdfs()
    sequential = [parse_pdf(f"https://x.example/{i}.pdf", b) for i, b in enumerate(blobs)]
    jobs = [(i, b) for _ in range(4) for i, b in enumerate(blobs)]
    with ThreadPoolExecutor(8) as ex:
        parallel = list(ex.map(lambda j: parse_pdf(f"https://x.example/{j[0]}.pdf", j[1]),
                               jobs))
    assert len(parallel) == len(jobs)  # no exception in any thread
    for (i, _), doc in zip(jobs, parallel, strict=True):
        assert doc.pages == sequential[i].pages and doc.title == sequential[i].title

def test_at_most_one_pdfium_session_is_open_at_a_time(monkeypatch):
    """The invariant itself, independent of whether a race happens to corrupt a load: with 8
    threads parsing, PDFium documents are never open concurrently in this process."""
    real = pypdfium2.PdfDocument
    state = {"open": 0, "max": 0}
    guard = threading.Lock()

    class Counting(real):
        def __init__(self, *a, **k):
            with guard:
                state["open"] += 1
                state["max"] = max(state["max"], state["open"])
            time.sleep(0.01)  # widen the window a concurrent caller would hit
            super().__init__(*a, **k)

        def close(self):
            try:
                super().close()
            finally:
                with guard:
                    state["open"] -= 1

    monkeypatch.setattr(pypdfium2, "PdfDocument", Counting)
    blobs = _pdfs(n=4, pages=3)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda b: parse_pdf("https://x.example/r.pdf", b), blobs * 4))
    assert state["max"] == 1 and state["open"] == 0

def test_a_parse_failure_keeps_the_raw_bytes_and_its_error():
    """A document whose parse fails is still stored (content addressed) so it can be replayed
    exactly; the failure is recorded, never swallowed."""
    url = "https://city.example/broken.pdf"
    body = b"%PDF-1.4\nthis is not really a pdf\n%%EOF\n"
    site = Site().pdf(url, body)
    artifacts = MemoryArtifacts()
    svc = make_acquisition(site, artifact_store=artifacts)
    out = asyncio.run(svc.acquire_round([candidate(url, source_type="GOVERNMENT")], []))
    rec, = out.records
    assert rec["status"] == "FAILED" and rec["error_kind"] == "PARSE_ERROR"
    assert "PdfiumError" in rec["error_message"]
    assert rec["raw_object_key"] in artifacts.objects
    assert artifacts.objects[rec["raw_object_key"]] == body
    assert rec["content_sha256"] == hashlib.sha256(body).hexdigest()
    assert not out.chunks
