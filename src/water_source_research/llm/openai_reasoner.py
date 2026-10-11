from typing import TypeVar
from openai import AsyncOpenAI
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
