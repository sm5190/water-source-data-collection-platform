"""Manual smoke test for discovery providers against the REAL APIs (not run by pytest).

Usage:
  uv run python scripts/discovery_smoke.py Toronto --country Canada
  uv run python scripts/discovery_smoke.py "São Paulo" --country Brazil --providers openalex,crossref
  uv run python scripts/discovery_smoke.py Lima --json > lima.json

Reads credentials from .env (SEARXNG_URL, OPENALEX_API_KEY, SEMANTIC_SCHOLAR_API_KEY,
CROSSREF_MAILTO). Prints per-provider status, the merged ranked candidates and Crossref
verification. Exits 1 only if every provider failed. Keys are never printed.
"""

import argparse
import asyncio
import json
import sys
from water_source_research.core.config import get_settings
from water_source_research.discovery.queries import build_queries
from water_source_research.discovery.service import build_discovery_service
from water_source_research.services.field_state import load_field_definitions

DEFAULT_FIELDS = ["utility_name", "water_source", "total_city_water_withdrawals_m3_day"]

async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.providers:
        names = [p.strip() for p in args.providers.split(",") if p.strip()]
        settings = settings.model_copy(update={"discovery": settings.discovery.model_copy(
            update={"providers": names, "enabled": True})})
    service = build_discovery_service(settings)
    if service is None:
        print("Discovery is disabled (DISCOVERY__ENABLED=false).", file=sys.stderr)
        return 1
    queries = build_queries(city=args.city, country=args.country, utility=None,
                            field_keys=args.fields or DEFAULT_FIELDS,
                            fields=load_field_definitions(),
                            max_per_kind=args.max_queries)
    outcome = await service.discover(queries, city=args.city, country=args.country)

    if args.json:
        print(json.dumps({
            "queries": [{"kind": q.kind, "query": q.query} for q in queries],
            "provider_status": outcome.provider_status,
            "calls": [{"provider": c.provider, "strategy": c.strategy, "result": c.result,
                       "query": c.query, "found": c.sources_found,
                       "retained": c.sources_retained, "duration_ms": c.duration_ms}
                      for c in outcome.calls],
            "candidates": [c.to_dict() for c in outcome.candidates],
        }, indent=2, ensure_ascii=False))
    else:
        print(f"Configured providers: {', '.join(service.provider_names) or '(none)'}")
        print(f"OpenAlex key: {'set' if settings.openalex_api_key else 'not set'} | "
              f"Semantic Scholar key: {'set' if settings.semantic_scholar_api_key else 'not set'}"
              f" | Crossref mailto: {'set' if settings.crossref_mailto else 'not set'}\n")
        print("Queries:")
        for q in queries:
            print(f"  [{q.kind:8}] {q.query}")
        print("\nCalls:")
        for c in outcome.calls:
            print(f"  {c.provider:17} {c.result:9} {c.strategy:17} found={c.sources_found:<3}"
                  f" kept={c.sources_retained:<3} {c.duration_ms:>6} ms  {c.query or ''}")
        print("\nProvider status:")
        for name, status in sorted(outcome.provider_status.items()):
            print(f"  {name:17} {status}")
        print(f"\nTop {min(args.top, len(outcome.candidates))} of "
              f"{len(outcome.candidates)} candidates:")
        for i, c in enumerate(outcome.candidates[:args.top], 1):
            doi = f" doi:{c.doi} verified={c.doi_verified}" if c.doi else ""
            print(f"{i:3}. [{c.source_type}] {c.score:.3f} {c.title[:90]}")
            print(f"     {c.url}")
            print(f"     via {', '.join(c.providers)}"
                  f"{' | ' + c.published_date if c.published_date else ''}{doi}")

    statuses = outcome.provider_status.values()
    failed = all(s not in ("ok", "no_results") and not s.startswith("degraded")
                 for s in statuses)
    return 1 if failed else 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("city")
    parser.add_argument("--country")
    parser.add_argument("--providers", help="comma-separated subset, e.g. searxng,openalex")
    parser.add_argument("--fields", nargs="*", help=f"field keys (default: {DEFAULT_FIELDS})")
    parser.add_argument("--max-queries", type=int, default=2, help="per kind (web/academic)")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--json", action="store_true")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
