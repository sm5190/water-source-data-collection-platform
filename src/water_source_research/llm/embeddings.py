from typing import Protocol
from openai import OpenAI
from water_source_research.core.versions import EMBEDDING_DIMENSIONS

class EmbeddingClient(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

class OpenAIEmbeddingClient:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small",
                 dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for embeddings")
        self.model = model
        self.dimensions = dimensions
        self.client = OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=self.model, input=texts, dimensions=self.dimensions,
        )
        return [item.embedding for item in response.data]
