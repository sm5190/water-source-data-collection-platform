"""Procedural-memory policy. Every threshold lives here; defaults are untuned starting points.

Loaded as ``Settings.procedure_memory`` (env: ``PROCEDURE_MEMORY__<FIELD>``) and validated when
settings are constructed, so a bad configuration fails at startup.
"""

from typing import Literal
from pydantic import BaseModel, Field, model_validator
from water_source_research.schemas.procedures import ProcedureStatus

_PROBABILITIES = (
    "min_similarity", "promotion_max_correction_rate", "min_recent_success_rate",
    "eligibility_min_resolved_ratio", "merge_min_jaccard", "merge_min_cosine", "ewma_alpha",
)

DEFAULT_CONSTRAINTS = {
    "withdrawal": [
        "preserve the reference year of every quantity",
        "do not infer a missing quantity",
        "record units and conversion method",
    ],
    "population": ["preserve the reference year", "prefer census or official estimates"],
    "water_quality": ["preserve the report year"],
    "point_of_diversion": ["record how coordinates were obtained"],
}
GENERIC_CONSTRAINTS = ["cite an approved source for every value"]

class ProcedureMemorySettings(BaseModel):
    enabled: bool = False

    # Retrieval
    max_results: int = Field(default=3, ge=1, le=5)
    max_candidates: int = Field(default=50, ge=1, le=500)
    min_similarity: float = 0.2
    statuses: list[ProcedureStatus] = Field(default_factory=lambda: [ProcedureStatus.ACTIVE])
    procedure_token_budget: int = Field(default=300, ge=50)

    # Ranking (score = w_sim*sim + w_rel*wilson + w_recent*recent + w_recency*decay
    #                  - w_correction*correction_rate)
    weight_similarity: float = Field(default=0.4, ge=0)
    weight_reliability: float = Field(default=0.3, ge=0)
    weight_recent_success: float = Field(default=0.15, ge=0)
    weight_recency: float = Field(default=0.1, ge=0)
    weight_correction: float = Field(default=0.2, ge=0)
    wilson_z: float = Field(default=1.96, gt=0)
    recency_half_life_days: float = Field(default=90, gt=0)
    ewma_alpha: float = 0.2

    # Lifecycle
    require_human_approval: bool = True
    promotion_min_support_runs: int = Field(default=3, ge=1)
    promotion_min_support_cities: int = Field(default=2, ge=1)
    promotion_max_correction_rate: float = 0.3
    min_usage_for_deprecation: int = Field(default=10, ge=1)
    min_recent_success_rate: float = 0.5
    stale_after_days: float = Field(default=180, gt=0)
    retire_after_days: float = Field(default=90, gt=0)

    # Distillation eligibility
    eligibility_min_resolved_ratio: float = 1.0
    eligibility_allow_conflicted: bool = False  # if True: eligible, but never auto-promoted
    eligibility_require_evidence: bool = True

    # Dedupe / merge
    merge_min_jaccard: float = 0.6
    merge_min_cosine: float = 0.9

    # Distillation execution. "llm" runs on the configured LLM_PROVIDER and falls back to
    # "deterministic" when that provider has no credentials.
    distiller: Literal["llm", "deterministic"] = "llm"
    # default: the active provider's model (VT_LLM_MODEL or OPENAI_MODEL). If set, it must be
    # a model the active provider serves.
    distiller_model: str | None = None
    max_recipe_chars: int = Field(default=4000, ge=500)
    distillation_max_attempts: int = Field(default=3, ge=1)
    distillation_lease_seconds: int = Field(default=300, ge=10)

    # Embeddings. Used with LLM_PROVIDER=openai; VT uses VT_LLM_EMBEDDING_MODEL.
    embedding_model: str = "text-embedding-3-small"

    constraints_by_field_type: dict[str, list[str]] = Field(
        default_factory=lambda: dict(DEFAULT_CONSTRAINTS)
    )

    @model_validator(mode="after")
    def _validate(self) -> "ProcedureMemorySettings":
        for name in _PROBABILITIES:
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"procedure_memory.{name} must be within [0, 1], got {value}")
        if self.weight_similarity + self.weight_reliability + self.weight_recent_success \
                + self.weight_recency <= 0:
            raise ValueError("procedure_memory ranking needs at least one positive weight")
        if not self.statuses:
            raise ValueError("procedure_memory.statuses must not be empty")
        if self.promotion_min_support_cities > self.promotion_min_support_runs:
            raise ValueError("promotion_min_support_cities cannot exceed support_runs")
        return self
