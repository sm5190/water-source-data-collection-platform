"""Firecrawl checks (live, manual). Never prints the API key.

    uv run python scripts/firecrawl_check.py --pick-empty Santiago     # read only, no credits
    uv run python scripts/firecrawl_check.py <url> [--out f.json]        # ONE scrape (~1 credit)
    uv run python scripts/firecrawl_check.py --reconcile <run_id>        # read only report

--pick-empty: an EMPTY HTML page from the city's stored research runs whose origin's
robots.txt allows it now (same rules as the escalation), so it is eligible.
<url>: one request with the production request policy (fresh, not stored in Firecrawl's cache,
basic proxy, full page, markdown + rawHtml); prints the response fields seen (vs the docs),
status, the representation the pipeline would choose and why (with both texts' profiles),
estimated credits and the team's remaining credits before/after (team-wide, approximate).
One ledger row (category acquisition_check). Nothing else is written.
--reconcile: the run's firecrawl_attempts and Firecrawl document records: reserved, estimated,
uncertain, and the observed team deltas the rounds recorded.
"""

import argparse
import json
from pathlib import Path
import sys
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from water_source_research.acquisition.firecrawl import (
    FirecrawlClient, FirecrawlError, coverage, pdf_pages, select_representation,
    sensitive_url,
)
from water_source_research.acquisition.http import parse_html
from water_source_research.acquisition.safe_http import SafeFetcher
from water_source_research.core.config import get_settings
from water_source_research.db.evidence_models import FirecrawlAttemptRecord, SourceDocumentRecord
from water_source_research.db.session import get_engine
from water_source_research.observability.usage import configure_usage, recorder
from water_source_research.research_queue.checkpointing import run_async

DOCUMENTED = {"markdown", "rawHtml", "warning", "metadata", "pages", "metadata.title",
              "metadata.sourceURL", "metadata.url", "metadata.statusCode",
              "metadata.numPages", "metadata.totalPages", "metadata.contentType",
              "metadata.error"}

async def pick_empty(city: str) -> None:
    engine = get_engine()
    with engine.connect() as c:
        rows = c.execute(text(
            "select d.requested_url, d.run_id from source_documents d join research_jobs j "
            "on j.run_id = d.run_id where j.city_name = :city and j.mode = 'RESEARCH' and "
            "d.status = 'EMPTY' and d.error_kind is null and coalesce(d.attempt,'direct') = "
            "'direct' and (d.media_type ilike '%html%' or d.media_type is null) "
            "order by d.created_at desc"), {"city": city}).all()
    fetcher = SafeFetcher(get_settings().acquisition)
    seen = set()
    for url, run_id in rows:
        if url in seen:
            continue
        seen.add(url)
        if sensitive_url(url) or await fetcher.check_public(url):
            continue
        decision = await fetcher.robots_decision(url)
        print(f"{decision:12} {url}  (run {run_id[:8]})")
        if decision == "ALLOWED":
            print(f"\nELIGIBLE: {url}")
            break
    await fetcher.aclose()

async def check(url: str, out: str | None) -> int:
    settings = get_settings()
    if not settings.firecrawl_api_key:
        print("FIRECRAWL_API_KEY is not set in .env")
        return 2
    if sensitive_url(url):
        print("refused: the URL carries credential-like parameters")
        return 2
    fetcher = SafeFetcher(settings.acquisition)
    if await fetcher.check_public(url) or await fetcher.robots_decision(url) != "ALLOWED":
        print("refused: not a public URL whose robots.txt allows it")
        return 2
    configure_usage(get_engine(), settings)
    client = FirecrawlClient(settings.firecrawl_api_key,
                             timeout_seconds=settings.acquisition.firecrawl_timeout_seconds)
    before = await client.remaining_credits()
    import time
    started = time.perf_counter()
    report: dict = {"url": url, "team_remaining_before": before}
    try:
        result = await client.scrape(url, pdf_max_pages=1)
    except FirecrawlError as exc:
        report.update(error=exc.kind.value, message=exc.message, uncertain=exc.uncertain)
        result = None
    latency = round((time.perf_counter() - started) * 1000, 1)
    after = await client.remaining_credits()
    report.update(team_remaining_after=after, latency_ms=latency,
                  observed_team_delta=None if before is None or after is None
                  else round(before - after, 3))
    estimate = None
    if result is not None:
        meta = result.metadata
        seen = set(result.fields_seen)
        report.update(fields_seen=sorted(seen), undocumented_fields=sorted(seen - DOCUMENTED),
                      documented_missing=sorted({"markdown", "metadata", "metadata.statusCode",
                                                 "metadata.url"} - seen),
                      status_code=result.status_code, final_url=result.final_url,
                      content_type=meta.get("contentType"), warning=result.warning,
                      markdown_chars=len(result.markdown), raw_html_chars=len(result.raw_html))
        is_pdf = "pdf" in str(meta.get("contentType") or "").lower()
        if is_pdf:
            pages, basis = pdf_pages(result)
            report.update(page_basis=basis, **coverage(result, 1, len(pages) if pages else None))
            num = meta.get("numPages")
            estimate = 1.0 + float(num) if isinstance(num, int | float) else None
        else:
            html = parse_html(result.final_url or url, result.raw_html.encode("utf-8")).text \
                if result.raw_html.strip() else ""
            choice, why = select_representation(result.markdown, html)
            report.update(representation=choice, representation_reason=why,
                          markdown_head=result.markdown[:600], raw_html_extract_head=html[:600])
            estimate = 1.0
    report["credits_estimated"] = estimate
    r = recorder()
    if r is not None:
        r.record({"category": "acquisition_check", "kind": "CRAWL", "provider": "firecrawl",
                  "task": "scrape", "requests": result.attempts if result else 1,
                  "retries": (result.attempts - 1) if result else 0, "cache_hit": False,
                  "error": report.get("error"), "credits": None,
                  "credits_estimated": estimate if estimate is not None else 2.0,
                  "latency_ms": latency, "cost_basis": "estimated"})
        r.flush()
    await client.aclose()
    await fetcher.aclose()
    print(json.dumps(report, indent=1, default=str, ensure_ascii=False))
    if out:
        Path(out).write_text(json.dumps(report, indent=1, default=str, ensure_ascii=False),
                             encoding="utf-8")
    return 0 if result is not None else 1

def reconcile(run_id: str) -> None:
    engine = get_engine()
    with Session(engine) as s:
        rows = s.scalars(select(FirecrawlAttemptRecord).where(
            FirecrawlAttemptRecord.run_id.like(run_id + "%"))).all()
        docs = s.scalars(select(SourceDocumentRecord).where(
            SourceDocumentRecord.run_id.like(run_id + "%"),
            SourceDocumentRecord.attempt == "firecrawl")).all()
    held = sum(r.credits_reserved for r in rows if r.state != "SETTLED")
    settled = sum(r.credits_estimated or 0 for r in rows if r.state == "SETTLED")
    print(f"attempts {len(rows)}: settled {sum(r.state == 'SETTLED' for r in rows)}, "
          f"uncertain/pending {sum(r.state != 'SETTLED' for r in rows)}")
    print(f"budget used (conservative) = settled estimates {settled:g} + kept reservations "
          f"{held:g} = {settled + held:g}")
    for r in rows:
        print(f"  {r.state:9} {r.outcome or '':16} reserved {r.credits_reserved:g} est "
              f"{r.credits_estimated if r.credits_estimated is not None else 'Unknown'}  "
              f"{r.canonical_url[:90]}")
    deltas = {json.dumps((d.escalation or {}).get("round_team_credits"), sort_keys=True)
              for d in docs if (d.escalation or {}).get("round_team_credits")}
    for dlt in sorted(deltas):
        print("  round team credits (team-wide, approximate):", dlt)

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("url", nargs="?")
    p.add_argument("--pick-empty")
    p.add_argument("--reconcile")
    p.add_argument("--out")
    a = p.parse_args()
    if a.pick_empty:
        run_async(pick_empty(a.pick_empty))
        return 0
    if a.reconcile:
        reconcile(a.reconcile)
        return 0
    if not a.url:
        p.error("a URL, --pick-empty CITY or --reconcile RUN")
    return run_async(check(a.url, a.out))

if __name__ == "__main__":
    raise SystemExit(main())
