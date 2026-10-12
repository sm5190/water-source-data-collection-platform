"""Discovery policy. Loaded as ``Settings.discovery`` (env: ``DISCOVERY__<FIELD>``).

Credentials are not here: they are top-level settings (``OPENALEX_API_KEY``,
``SEMANTIC_SCHOLAR_API_KEY``, ``CROSSREF_MAILTO``, ``SEARXNG_URL``) like the other providers.
"""

from typing import Literal
from pydantic import BaseModel, Field, model_validator

ProviderName = Literal["searxng", "openalex", "semantic_scholar", "crossref"]
ALL_PROVIDERS: tuple[ProviderName, ...] = ("searxng", "openalex", "semantic_scholar", "crossref")

class ProviderLimits(BaseModel):
    max_concurrency: int = Field(default=2, ge=1, le=32)
    min_interval_seconds: float = Field(default=0.0, ge=0)  # between request starts

# One class per provider so that overriding a single field from the environment
# (e.g. DISCOVERY__SEMANTIC_SCHOLAR_LIMITS__MIN_INTERVAL_SECONDS) keeps the provider's other
# defaults. Official guidance: S2 keyed = 1 req/s; OpenAlex <= 10 req/s; Crossref's polite
# pool allows a few concurrent requests.
class SearXNGLimits(ProviderLimits):
    max_concurrency: int = Field(default=4, ge=1, le=32)

class OpenAlexLimits(ProviderLimits):
    max_concurrency: int = Field(default=4, ge=1, le=32)
    min_interval_seconds: float = Field(default=0.12, ge=0)

class SemanticScholarLimits(ProviderLimits):
    max_concurrency: int = Field(default=1, ge=1, le=32)
    min_interval_seconds: float = Field(default=1.05, ge=0)

class CrossrefLimits(ProviderLimits):
    max_concurrency: int = Field(default=2, ge=1, le=32)
    min_interval_seconds: float = Field(default=0.2, ge=0)

class DiscoverySettings(BaseModel):
    enabled: bool = True
    providers: list[ProviderName] = Field(default_factory=lambda: list(ALL_PROVIDERS))

    # HTTP behaviour (per request)
    timeout_seconds: float = Field(default=20, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)  # retries after the first attempt
    backoff_base_seconds: float = Field(default=1.0, ge=0)
    backoff_max_seconds: float = Field(default=30.0, ge=0)

    # In-process response cache (per worker process)
    cache_ttl_seconds: float = Field(default=86_400, ge=0)  # 0 disables caching
    cache_max_entries: int = Field(default=2048, ge=1)

    # Volume per round / run
    results_per_query: int = Field(default=10, ge=1, le=100)
    max_queries_per_round: int = Field(default=6, ge=1, le=50)
    max_candidates_per_round: int = Field(default=40, ge=1)
    max_candidates_total: int = Field(default=150, ge=1)
    max_results_per_host: int = Field(default=5, ge=1)
    crossref_verify_top_n: int = Field(default=15, ge=0)
    semantic_scholar_related_per_round: int = Field(default=0, ge=0)  # related-paper expansion

    # Rate limits per provider
    searxng_limits: SearXNGLimits = Field(default_factory=SearXNGLimits)
    openalex_limits: OpenAlexLimits = Field(default_factory=OpenAlexLimits)
    semantic_scholar_limits: SemanticScholarLimits = Field(
        default_factory=SemanticScholarLimits)
    crossref_limits: CrossrefLimits = Field(default_factory=CrossrefLimits)

    def limits_for(self, provider: str) -> ProviderLimits:
        return getattr(self, f"{provider}_limits")

    @model_validator(mode="after")
    def _validate(self) -> "DiscoverySettings":
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("discovery.backoff_max_seconds < backoff_base_seconds")
        if self.max_candidates_total < self.max_candidates_per_round:
            raise ValueError("discovery.max_candidates_total < max_candidates_per_round")
        return self
