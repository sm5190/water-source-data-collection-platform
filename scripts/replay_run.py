"""Replay a research run on its saved documents (mode REPLAY; no discovery, no fetching).

    uv run python scripts/replay_run.py <run_id> [<run_id> ...] [--max-rounds N]

Documents come back in their original acquisition rounds, so retrieval, later-round
re-extraction and resolution run as they would live, on identical evidence. Extraction calls
the configured LLM provider (VT: needs the VT VPN). Each replay is a new run (its own claims,
documents, records, events); the original run is only read. Prints the replay run id.
"""

import argparse
import sys
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.llm.extraction import build_extraction_gateway
from water_source_research.research_queue.checkpointing import run_async
from water_source_research.services.replay import replay_run

async def main(run_ids: list[str], max_rounds: int | None) -> int:
    engine = get_engine()
    gateway = build_extraction_gateway(get_settings())
    for parent in run_ids:
        run_id, final = await replay_run(engine, parent, gateway, max_rounds)
        ws = final["research_state"]["fields"]["water_source"]["candidate_claims"]
        print(f"{parent} -> replay {run_id}: rounds {final['replay']['rounds_served']}, "
              f"re-extractions {final.get('reextractions') or {}}, "
              f"water_source candidates {[c['value'] for c in ws]}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--max-rounds", type=int, default=None)
    args = parser.parse_args()
    from water_source_research.observability.otel import configure_telemetry, shutdown_telemetry
    configure_telemetry("replay")  # optional (OBSERVABILITY__OTEL_ENABLED)
    from water_source_research.db.session import get_engine
    from water_source_research.observability.usage import configure_usage
    configure_usage(get_engine())  # extraction calls -> provider_usage
    from water_source_research.llm.response_store import configure_response_store
    configure_response_store(get_engine())  # persistent extraction cache (LLM__PERSISTENT_CACHE)
    try:
        code = run_async(main(args.run_ids, args.max_rounds))
    finally:
        shutdown_telemetry()
    sys.exit(code)
