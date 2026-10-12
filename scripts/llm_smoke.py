"""Manual smoke test for the configured LLM provider against the REAL API (not run by pytest).

Usage:
  uv run python scripts/llm_smoke.py                 # VT: models, structured modes, extraction
  uv run python scripts/llm_smoke.py --skip-embeddings
  uv run python scripts/llm_smoke.py --allow-openai  # required when LLM_PROVIDER=openai (paid)

Reads LLM_PROVIDER, VT_LLM_* / OPENAI_* from .env. Makes about six small requests (within the
27/min budget): lists models, probes which ``LLM__VT_STRUCTURED_MODE`` the endpoint accepts,
runs one extraction on a canned evidence bundle and one embedding. Keys are never printed.
Exits 1 if no structured mode works or the extraction call fails.
"""

import argparse
import asyncio
import sys
import openai
from pydantic import BaseModel, ConfigDict
from water_source_research.context_manager import ContextManager, ContextPurpose, ContextTask
from water_source_research.core.config import Settings, get_settings
from water_source_research.llm.base import LLMError
from water_source_research.llm.extraction import StructuredExtractionGateway
from water_source_research.llm.router import (
    build_embedding_client, build_structured_llm, reset_shared_state,
)
from water_source_research.schemas.research import EvidenceChunk
from water_source_research.services.field_state import initialize_city_state

MODES = ("json_schema", "json_object", "prompt")

class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str
    country: str
    founded_year: int

CHUNK = EvidenceChunk(
    chunk_id="smoke", url="https://www.toronto.ca/services-payments/water-environment/",
    title="Toronto Water", source_type="UTILITY",
    text="Toronto Water treats drinking water drawn from Lake Ontario, a surface water source, "
         "and serves 3.6 million residents and businesses in Toronto and York Region.")

def with_llm(settings: Settings, **llm) -> Settings:
    # No caching and no repair turns: each probe must succeed on its own.
    llm = {"cache_ttl_seconds": 0, "structured_max_attempts": 0, **llm}
    return settings.model_copy(update={"llm": settings.llm.model_copy(update=llm)})

def list_models(settings: Settings) -> None:
    base_url = settings.vt_llm_base_url if settings.llm_provider == "vt" else None
    key = settings.vt_llm_api_key if settings.llm_provider == "vt" else settings.openai_api_key
    try:
        models = openai.OpenAI(api_key=key, base_url=base_url, max_retries=0,
                               timeout=30).models.list()
        names = sorted(m.id for m in models.data)
        print(f"models ({len(names)}): {', '.join(names)}")
    except openai.OpenAIError as exc:
        print(f"models: could not list ({type(exc).__name__})")

def probe_modes(settings: Settings) -> list[str]:
    working = []
    for mode in MODES if settings.llm_provider == "vt" else ("native",):
        reset_shared_state()
        llm = build_structured_llm(with_llm(settings, vt_structured_mode=mode)
                                   if mode != "native" else with_llm(settings),
                                   task="smoke", prompt_version="smoke-1")
        try:
            answer = llm.structured(
                "Answer with facts about the city named by the user.",
                "Toronto (give its country and the year it was incorporated as a city).", Probe)
            print(f"structured mode {mode:12s} OK      -> {answer.model_dump()}")
            working.append(mode)
        except LLMError as exc:
            print(f"structured mode {mode:12s} FAILED  -> {exc}")
    return working

async def extraction(settings: Settings) -> bool:
    reset_shared_state()
    gateway = StructuredExtractionGateway(build_structured_llm(
        with_llm(settings, structured_max_attempts=2), task="extract", prompt_version="smoke"))
    state = initialize_city_state("Toronto", "Canada")
    ok = True
    for field_key in ("water_type", "population_served_millions"):
        bundle = ContextManager().build(ContextTask(ContextPurpose.EXTRACT, [field_key]), state,
                                        [CHUNK], set())
        try:
            claims = await gateway.extract(bundle, run_id="smoke", field_key=field_key)
        except LLMError as exc:
            print(f"extract {field_key}: FAILED -> {exc}")
            ok = False
            continue
        for c in claims:
            quotes = "; ".join(repr(r.passage) for r in c.evidence)
            print(f"extract {field_key}: {c.value!r} ({c.value_status}, {c.value_origin}, "
                  f"confidence {c.confidence}) evidence: {quotes}")
        if not claims:
            print(f"extract {field_key}: no grounded claim")
    return ok

def embedding(settings: Settings) -> None:
    try:
        client = build_embedding_client(settings)
        vector = client.embed_query("drinking water withdrawal research")
        print(f"embedding {client.model}: {len(vector)} dimensions")
    except (LLMError, RuntimeError) as exc:
        print(f"embedding: FAILED -> {exc}")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-openai", action="store_true",
                        help="permit paid OpenAI calls when LLM_PROVIDER=openai")
    parser.add_argument("--skip-embeddings", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    provider = settings.llm_provider
    if provider == "openai" and not args.allow_openai:
        print("LLM_PROVIDER=openai makes paid calls; rerun with --allow-openai.",
              file=sys.stderr)
        return 1
    if provider == "vt":
        print(f"provider: vt | base URL: {settings.vt_llm_base_url or '(not set)'} | "
              f"model: {settings.vt_llm_model or '(not set)'} | "
              f"key: {'set' if settings.vt_llm_api_key else 'not set'}")
    else:
        print(f"provider: openai | model: {settings.openai_model} | "
              f"key: {'set' if settings.openai_api_key else 'not set'}")

    list_models(settings)
    working = probe_modes(settings)
    if not working:
        print("No structured-output mode works; check the model name and key.", file=sys.stderr)
        return 1
    if provider == "vt":
        current = settings.llm.vt_structured_mode
        advice = "keep it" if current in working else f"set LLM__VT_STRUCTURED_MODE={working[0]}"
        print(f"LLM__VT_STRUCTURED_MODE is {current}: {advice}")
        settings = with_llm(settings, vt_structured_mode=current if current in working
                            else working[0])
    ok = asyncio.run(extraction(settings))
    if not args.skip_embeddings:
        embedding(settings)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
