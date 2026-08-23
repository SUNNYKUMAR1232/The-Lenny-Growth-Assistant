"""OpenAI embedding provider (optional cloud path)."""

from __future__ import annotations

from app.config import settings
from app.embeddings.base import EmbeddingProvider
from app.errors import EmbeddingError


class OpenAIEmbedder(EmbeddingProvider):
    name = "openai"

    def __init__(self, model: str = "text-embedding-3-small", dim: int | None = None) -> None:
        self.model = model
        self.dim = dim or settings.embedding_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not settings.openai_api_key:
            raise EmbeddingError(
                "OPENAI_API_KEY is not set, so cloud embeddings are unavailable."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise EmbeddingError("The `openai` package is not installed.") from exc

        client = AsyncOpenAI(
            api_key=settings.openai_api_key, base_url=settings.openai_base_url
        )
        try:
            # `dimensions` lets text-embedding-3-* match the schema's vector size.
            response = await client.embeddings.create(
                model=self.model, input=texts, dimensions=self.dim
            )
        except Exception as exc:
            raise EmbeddingError("The cloud embedding request failed.") from exc
        finally:
            await client.close()
        return [list(item.embedding) for item in response.data]
