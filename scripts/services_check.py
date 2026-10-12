"""External services check against the REAL services (not run by pytest). No secrets printed.

Usage: uv run python scripts/services_check.py [--html URL] [--pdf URL]

VT LLM (LLM_PROVIDER=vt only; OpenAI is refused) structured output, SearXNG, OpenAlex,
Semantic Scholar, Crossref (DOI lookup), direct HTTP acquisition and PDF text extraction
through the same code paths the worker uses. Each line is OK / FAIL / UNAVAILABLE with the
reason. Exit 1 if VT, SearXNG or acquisition fails (the slice cannot run without them).
"""

import argparse
import sys
import time
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict
from water_source_research.acquisition.safe_http import SafeFetcher
from water_source_research.acquisition.service import AcquisitionService
from water_source_research.core.config import get_settings
from water_source_research.discovery.http import ProviderError
from water_source_research.discovery.service import build_discovery_service
from water_source_research.llm.base import LLMError
from water_source_research.llm.router import build_structured_llm
from water_source_research.research_queue.checkpointing import run_async

DEFAULT_HTML = "https://www.toronto.ca/services-payments/water-environment/tap-water-in-toronto/"
DEFAULT_PDF = ("https://www.ocwa.com/wp-content/uploads/2025/07/"
               "OCWA-AR2025-FULL-Final-ENG-tagged-1.pdf")

class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str
    country: str

def line(name: str, status: str, detail: str, seconds: float) -> None:
    print(f"[{status:<11}] {name:<18} {seconds:6.2f}s  {detail}")

async def search(provider, query: str) -> tuple[str, str, list]:
    try:
        results = await provider.search(query, 5)
    except ProviderError as exc:
        status = "UNAVAILABLE" if exc.kind.value in ("rate_limited", "unauthorized") else "FAIL"
        return status, f"{exc.kind.value}: {str(exc)[:160]}", []
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}", []
    if not results:
        return "FAIL", "no results", []
    top = results[0]
    return "OK", f"{len(results)} results; top: {top.title[:60]!r} <{top.url}>", results

async def checks(html_url: str, pdf_url: str) -> bool:
    settings = get_settings()
    critical_ok = True

    t = time.perf_counter()
    if settings.llm_provider != "vt":
        line("vt-llm", "FAIL", f"LLM_PROVIDER={settings.llm_provider}; only vt is allowed", 0)
        critical_ok = False
    else:
        try:
            llm = build_structured_llm(settings.model_copy(update={"llm": settings.llm.model_copy(
                update={"cache_ttl_seconds": 0, "structured_max_attempts": 1})}),
                task="services-check", prompt_version="1")
            answer = await llm.astructured("Answer with JSON only.",
                                           "Which country is Toronto in? Give city and country.",
                                           Probe)
            line("vt-llm", "OK", f"host={urlsplit(settings.vt_llm_base_url).netloc} "
                 f"model={settings.vt_llm_model} mode={settings.llm.vt_structured_mode} "
                 f"-> {answer.model_dump()}", time.perf_counter() - t)
        except LLMError as exc:
            line("vt-llm", "FAIL", f"{exc.kind.value}: {str(exc)[:200]}", time.perf_counter() - t)
            critical_ok = False

    service = build_discovery_service(settings)
    providers = {p.name: p for p in (*service.web, *service.scholarly)} if service else {}
    dois: list[str] = []
    for name, query in (("searxng", "Toronto drinking water source"),
                        ("openalex", "Toronto drinking water supply"),
                        ("semantic_scholar", "Toronto drinking water supply")):
        t = time.perf_counter()
        if name not in providers:
            line(name, "UNAVAILABLE", "not enabled in DISCOVERY__PROVIDERS", 0)
            continue
        status, detail, found = await search(providers[name], query)
        if name == "semantic_scholar" and not settings.semantic_scholar_api_key:
            detail += " (keyless)"
        line(name, status, detail, time.perf_counter() - t)
        if name == "searxng" and status != "OK":
            critical_ok = False
        if name == "openalex":
            dois = [r.doi for r in found if r.doi]

    t = time.perf_counter()
    if service and service.crossref and dois:
        # OpenAlex DOIs can be DataCite-registered (theses, repositories): Crossref returns
        # "not found" for those, which is correct. Try them until one is a Crossref DOI.
        misses = []
        try:
            for doi in dois:
                rec = await service.crossref.lookup_doi(doi)
                if rec:
                    line("crossref", "OK", f"doi {doi}: {rec.title[:60]!r}"
                         + (f" (not Crossref DOIs: {misses})" if misses else ""),
                         time.perf_counter() - t)
                    break
                misses.append(doi)
            else:
                line("crossref", "FAIL", f"none of {misses} resolved", time.perf_counter() - t)
        except Exception as exc:  # noqa: BLE001
            line("crossref", "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}",
                 time.perf_counter() - t)
    else:
        line("crossref", "UNAVAILABLE", "disabled or no DOI to look up", 0)

    acq_settings = settings.acquisition
    for name, url in (("direct-http(html)", html_url), ("pdf-extraction", pdf_url)):
        t = time.perf_counter()
        service_ = AcquisitionService(acq_settings, fetcher=SafeFetcher(acq_settings))
        try:
            out = await service_.acquire_round([{"url": url, "title": None,
                                                 "source_type": "GOVERNMENT"}], [])
        finally:
            await service_.fetcher.aclose()
        rec = out.records[0]
        ok = rec["status"] == "OK"
        pages = f", {rec['page_count']} pages" if rec.get("page_count") else ""
        line(name, "OK" if ok else "FAIL",
             f"{rec['status']} {rec.get('error_kind') or ''} {rec.get('acquisition_method')} "
             f"{rec.get('byte_size')} bytes{pages}, {len(out.chunks)} chunks <{url}>",
             time.perf_counter() - t)
        critical_ok &= ok
    return critical_ok

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", default=DEFAULT_HTML)
    parser.add_argument("--pdf", default=DEFAULT_PDF)
    args = parser.parse_args()
    return 0 if run_async(checks(args.html, args.pdf)) else 1

if __name__ == "__main__":
    sys.exit(main())
