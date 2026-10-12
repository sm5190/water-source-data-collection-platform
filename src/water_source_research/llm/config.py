"""LLM call policy. Loaded as ``Settings.llm`` (env: ``LLM__<FIELD>``).

Provider selection and credentials are top-level settings (``LLM_PROVIDER``, ``VT_LLM_*``,
``OPENAI_*``) like the other providers. Limits are per worker *process*: when running N worker
processes against one VT account, divide ``requests_per_minute``/``max_concurrency`` by N.
"""

from typing import Literal
from pydantic import BaseModel, Field, model_validator

# VT ARC rejects non-streaming requests asking for more output tokens than this (HTTP 413).
VT_MAX_OUTPUT_TOKENS = 8000

class LLMSettings(BaseModel):
    # Rate limiting (shared by every LLM call in the process). VT allows 20 requests in
    # flight per user across all models; the per-minute budget is the project's own.
    requests_per_minute: int = Field(default=27, ge=1)
    max_concurrency: int = Field(default=8, ge=1, le=20)

    # Per request
    timeout_seconds: float = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)  # transient errors, after the first try
    backoff_base_seconds: float = Field(default=1.0, ge=0)
    backoff_max_seconds: float = Field(default=30.0, ge=0)
    max_output_tokens: int = Field(default=4000, ge=1)

    # Structured output. Extra model round-trips that send back the validation error.
    structured_max_attempts: int = Field(default=2, ge=0, le=5)
    # How VT is asked for JSON; output is always validated client-side against the schema.
    # Run scripts/llm_smoke.py to find which mode the endpoint accepts.
    vt_structured_mode: Literal["json_schema", "json_object", "prompt"] = "json_schema"

    # In-process response cache (per worker process)
    cache_ttl_seconds: float = Field(default=86_400, ge=0)  # 0 disables caching
    cache_max_entries: int = Field(default=1024, ge=1)

    # Retrieval queries to VT's Qwen3 embedding model get this one-line task prefix.
    embedding_query_instruction: str = (
        "Given a research task description, retrieve research procedures that fit it"
    )

    @model_validator(mode="after")
    def _validate(self) -> "LLMSettings":
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("llm.backoff_max_seconds < backoff_base_seconds")
        return self
