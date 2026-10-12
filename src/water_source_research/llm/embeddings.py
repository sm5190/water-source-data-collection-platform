import math
import time
from collections.abc import Callable
from typing import Any, Protocol
import openai
from openai import OpenAI
from water_source_research.core.versions import EMBEDDING_DIMENSIONS
from .base import LLMError, LLMErrorKind, classify_openai_error

class EmbeddingClient(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

class OpenAICompatibleEmbeddingClient:
    """``/embeddings`` on OpenAI or an OpenAI-compatible server (VT ARC).

    Vectors always come back with ``dimensions`` entries (the pgvector column width). Longer
    vectors are truncated and L2-normalised, which is valid for Matryoshka-trained models such
    as VT's Qwen3-Embedding-4B (native width 2560); OpenAI is asked for ``dimensions`` directly.
    Queries (not documents) get ``query_instruction`` as a prefix when one is set, as Qwen3
    embeddings expect.
    """

    def __init__(self, api_key: str, model: str, *, provider: str = "openai",
                 base_url: str | None = None, dimensions: int = EMBEDDING_DIMENSIONS,
                 query_instruction: str | None = None, timeout: float | None = None,
                 max_retries: int = 3, http_client: Any = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not api_key:
            raise RuntimeError(f"An API key is required for {provider} embeddings")
        self.model = model
        self.provider = provider
        self.dimensions = dimensions
        self.query_instruction = query_instruction
        self.max_retries = max_retries
        self.sleep = sleep
        # VT's server doesn't document ``dimensions``; truncate client-side instead.
        self.request_dimensions = provider == "openai"
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                             timeout=timeout, http_client=http_client)

    def embed(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict[str, Any] = {"model": self.model, "input": texts}
        if self.request_dimensions:
            kwargs["dimensions"] = self.dimensions
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.embeddings.create(**kwargs)
                break
            except openai.OpenAIError as exc:
                error = classify_openai_error(self.provider, exc)
                if not error.retryable or attempt == self.max_retries:
                    raise error from None
                # VT returns the wait as ``error.retry_after_s`` in the 429 body.
                self.sleep(error.retry_after if error.retry_after is not None
                           else min(30.0, 2.0 ** attempt))
        return [self._fit(item.embedding) for item in response.data]

    def embed_query(self, text: str) -> list[float]:
        if self.query_instruction:
            text = f"Instruct: {self.query_instruction}\nQuery: {text}"
        return self.embed([text])[0]

    def _fit(self, vector: list[float]) -> list[float]:
        if len(vector) == self.dimensions:
            return list(vector)
        if len(vector) < self.dimensions:  # model/config mismatch; retrying won't help
            raise LLMError(self.provider, LLMErrorKind.BAD_REQUEST,
                           f"{self.model} returned {len(vector)} dimensions, "
                           f"need {self.dimensions}")
        head = vector[:self.dimensions]
        norm = math.sqrt(sum(x * x for x in head)) or 1.0
        return [x / norm for x in head]

class OpenAIEmbeddingClient(OpenAICompatibleEmbeddingClient):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small",
                 dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for embeddings")
        super().__init__(api_key, model, provider="openai", dimensions=dimensions)
