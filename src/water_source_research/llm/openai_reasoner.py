from typing import Any, TypeVar
import openai
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from water_source_research.core.config import get_settings
from .base import (
    Completion, LLMError, LLMErrorKind, StructuredLLM, classify_openai_error, usage_tokens,
)
from .config import LLMSettings

__all__ = ["OpenAIReasoner", "OpenAIResponsesBackend", "OpenAIStructuredClient",
           "StructuredLLM"]

T = TypeVar("T", bound=BaseModel)
PROVIDER = "openai"

class OpenAIReasoner:
    def __init__(self, api_key: str | None = None, model: str | None = None, *,
                 max_retries: int = 2, timeout: float | None = None,
                 http_client: Any = None) -> None:
        if api_key is None or model is None:
            settings = get_settings()
            api_key = settings.openai_api_key if api_key is None else api_key
            model = settings.openai_model if model is None else model
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, max_retries=max_retries,
                                  timeout=timeout, http_client=http_client)

    async def parse(self, input: list[dict[str, str]], schema: type[T]) -> Any:
        """Raw Responses API ``parse`` result (``output_parsed`` + ``usage``)."""
        return await self.client.responses.parse(model=self.model, input=input,
                                                 text_format=schema)

    async def structured(self, system: str, user: str, schema: type[T]) -> T:
        response = await self.parse([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ], schema)
        if response.output_parsed is None:
            raise RuntimeError("No structured output returned")
        return response.output_parsed

class OpenAIStructuredClient:
    def __init__(self, api_key: str, model: str, *, max_retries: int = 2,
                 timeout: float | None = None, http_client: Any = None) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for structured LLM calls")
        self.model = model
        self.client = OpenAI(api_key=api_key, max_retries=max_retries, timeout=timeout,
                             http_client=http_client)

    def parse(self, input: list[dict[str, str]], schema: type[T]) -> Any:
        """Raw Responses API ``parse`` result (``output_parsed`` + ``usage``)."""
        return self.client.responses.parse(model=self.model, input=input, text_format=schema)

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        response = self.parse([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ], schema)
        if response.output_parsed is None:
            raise RuntimeError("No structured output returned")
        return response.output_parsed

class OpenAIResponsesBackend:
    """Router backend: OpenAI Responses API with native schema enforcement (``parse``).

    SDK retries are off; the router owns retries, rate limiting and caching.
    """

    provider = PROVIDER

    def __init__(self, api_key: str, model: str, settings: LLMSettings, *,
                 http_client: Any = None, async_http_client: Any = None) -> None:
        if not api_key:
            raise LLMError(PROVIDER, LLMErrorKind.UNCONFIGURED, "set OPENAI_API_KEY")
        if not model:
            raise LLMError(PROVIDER, LLMErrorKind.UNCONFIGURED, "set OPENAI_MODEL")
        self.model = model
        self._sync = OpenAIStructuredClient(api_key, model, max_retries=0,
                                            timeout=settings.timeout_seconds,
                                            http_client=http_client)
        self._async = OpenAIReasoner(api_key, model, max_retries=0,
                                     timeout=settings.timeout_seconds,
                                     http_client=async_http_client)
        self.base_url = str(self._sync.client.base_url).rstrip("/")

    def complete(self, messages: list[dict[str, str]], schema: type[BaseModel]) -> Completion:
        try:
            response = self._sync.parse(messages, schema)
        except openai.OpenAIError as exc:
            raise classify_openai_error(PROVIDER, exc) from None
        return _completion(response)

    async def acomplete(self, messages: list[dict[str, str]],
                        schema: type[BaseModel]) -> Completion:
        try:
            response = await self._async.parse(messages, schema)
        except openai.OpenAIError as exc:
            raise classify_openai_error(PROVIDER, exc) from None
        return _completion(response)

def _completion(response: Any) -> Completion:
    prompt_tokens, completion_tokens = usage_tokens(getattr(response, "usage", None))
    parsed = getattr(response, "output_parsed", None)
    # No parsed output (e.g. a refusal) is treated like malformed output by the router.
    return Completion(parsed=parsed, text=None if parsed is not None else "",
                      prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
