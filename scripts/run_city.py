import asyncio
import sys
from uuid import uuid4
from water_source_research.agents.graph import build_graph
from water_source_research.core.config import get_settings
from water_source_research.discovery.service import build_discovery_service
from water_source_research.llm.extraction import build_extraction_gateway
from water_source_research.services.field_state import initialize_city_state, unresolved_fields

async def main(city_name: str) -> None:
    settings = get_settings()
    state = initialize_city_state(city_name)
    graph = build_graph(gateway=build_extraction_gateway(settings),
                        discovery=build_discovery_service(settings))
    result = await graph.ainvoke({
        "run_id": str(uuid4()),
        "city_name": city_name,
        "research_state": state.model_dump(mode="json"),
        "unresolved_fields": unresolved_fields(state),
    })
    print(result)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/run_city.py <CITY NAME>")
    asyncio.run(main(" ".join(sys.argv[1:])))
