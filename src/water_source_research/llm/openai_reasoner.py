from typing import Protocol, TypeVar
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel
from water_source_research.core.config import get_settings

T = TypeVar("T", bound=BaseModel)

class OpenAIReasoner:
    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.openai_model
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def structured(self, system: str, user: str, schema: type[T]) -> T:
        response = await self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=schema,
        )
        if response.output_parsed is None:
            raise RuntimeError("No structured output returned")
        return response.output_parsed

class StructuredLLM(Protocol):
    """Synchronous structured-output model call (used by sync workers, e.g. distillation)."""

    model: str

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...

class OpenAIStructuredClient:
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for structured LLM calls")
        self.model = model
        self.client = OpenAI(api_key=api_key)

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text_format=schema,
        )
        if response.output_parsed is None:
            raise RuntimeError("No structured output returned")
        return response.output_parsed
