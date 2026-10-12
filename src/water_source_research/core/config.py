from functools import lru_cache
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from water_source_research.acquisition.config import AcquisitionSettings, ArtifactSettings
from water_source_research.discovery.config import DiscoverySettings
from water_source_research.llm.config import LLMSettings
from water_source_research.procedures.config import ProcedureMemorySettings
from water_source_research.research_queue.config import ResearchQueueSettings

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_nested_delimiter="__")

    database_url: str = "postgresql+psycopg://water:water@localhost:5432/water_source"
    # Which provider serves every LLM call (llm/router.py). Exactly one is active; there is
    # no automatic fallback between them.
    llm_provider: Literal["vt", "openai"] = "vt"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-terra"
    vt_llm_base_url: str = ""
    vt_llm_api_key: str = ""
    vt_llm_model: str = ""
    vt_llm_embedding_model: str = "Qwen3-Embedding-4B"
    searxng_url: str = "http://localhost:8080"
    openalex_api_key: str = ""
    semantic_scholar_api_key: str = ""
    crossref_mailto: str = ""  # contact email for Crossref's "polite" pool (not a key)
    firecrawl_api_key: str = ""
    apify_api_token: str = ""
    otel_service_name: str = "water-source-research"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    max_openai_input_tokens_per_city: int = 300_000
    max_openai_output_tokens_per_city: int = 40_000
    max_firecrawl_credits_per_city: int = 100
    max_discovery_rounds_per_city: int = 3
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    acquisition: AcquisitionSettings = Field(default_factory=AcquisitionSettings)
    artifacts: ArtifactSettings = Field(default_factory=ArtifactSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    procedure_memory: ProcedureMemorySettings = Field(default_factory=ProcedureMemorySettings)
    research_queue: ResearchQueueSettings = Field(default_factory=ResearchQueueSettings)

@lru_cache
def get_settings() -> Settings:
    return Settings()
