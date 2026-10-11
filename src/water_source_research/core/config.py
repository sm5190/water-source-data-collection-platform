from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from water_source_research.procedures.config import ProcedureMemorySettings
from water_source_research.research_queue.config import ResearchQueueSettings

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_nested_delimiter="__")

    database_url: str = "postgresql+psycopg://water:water@localhost:5432/water_source"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-terra"
    vt_llm_base_url: str = ""
    vt_llm_api_key: str = ""
    vt_llm_model: str = ""
    searxng_url: str = "http://localhost:8080"
    firecrawl_api_key: str = ""
    apify_api_token: str = ""
    otel_service_name: str = "water-source-research"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    max_openai_input_tokens_per_city: int = 300_000
    max_openai_output_tokens_per_city: int = 40_000
    max_firecrawl_credits_per_city: int = 100
    max_discovery_rounds_per_city: int = 3
    procedure_memory: ProcedureMemorySettings = Field(default_factory=ProcedureMemorySettings)
    research_queue: ResearchQueueSettings = Field(default_factory=ResearchQueueSettings)

@lru_cache
def get_settings() -> Settings:
    return Settings()
