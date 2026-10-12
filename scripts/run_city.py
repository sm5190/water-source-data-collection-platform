"""Dev: run the graph inline for one city, no queue and no database writes.

Usage: python scripts/run_city.py Toronto [--country Canada] [--fields a,b,c] [--no-acquire]
For a real, persisted run use the API + worker or scripts/vertical_slice_smoke.py.
"""

import argparse
import asyncio
import json
from uuid import uuid4
from water_source_research.acquisition.service import build_acquisition_service
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import get_settings
from water_source_research.discovery.service import build_discovery_service
from water_source_research.llm.extraction import build_extraction_gateway
from water_source_research.services.field_state import (
    initialize_city_state, select_fields, unresolved_fields,
)

async def main(city_name: str, country: str | None, fields: list[str] | None,
               acquire: bool) -> None:
    settings = get_settings()
    state = initialize_city_state(city_name, country)
    graph = build_graph(gateway=build_extraction_gateway(settings),
                        discovery=build_discovery_service(settings),
                        acquisition=build_acquisition_service(settings) if acquire else None)
    result = await graph.ainvoke({
        "run_id": str(uuid4()),
        "city_name": city_name,
        "country": country,
        "research_state": state.model_dump(mode="json"),
        "unresolved_fields": unresolved_fields(state, fields),
    })
    researched = fields or list(result["research_state"]["fields"])
    print(json.dumps({
        "run_id": result["run_id"],
        "documents": [{k: d.get(k) for k in ("requested_url", "status", "error_kind", "chunks")}
                      for d in result.get("acquired_documents", [])],
        "fields": {k: result["research_state"]["fields"][k] for k in researched},
        "needs_human_review": result.get("needs_human_review"),
        "review_reason": result.get("review_reason"),
    }, indent=2, default=str))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("city", nargs="+")
    parser.add_argument("--country", default=None)
    parser.add_argument("--fields", default=None, help="comma-separated field keys")
    parser.add_argument("--no-acquire", action="store_true", help="skip document acquisition")
    args = parser.parse_args()
    keys = select_fields([k.strip() for k in args.fields.split(",")] if args.fields else None)
    asyncio.run(main(" ".join(args.city), args.country, keys, not args.no_acquire))
