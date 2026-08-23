"""Ollama embedding provider (default: nomic-embed-text, 768 dims)."""

from __future__ import annotations

import asyncio

import httpx

from app.config import settings
from app.embeddings.base import EmbeddingProvider
from app.errors import EmbeddingError
from app.observability.logging import get_logger

log = get_logger("embeddings.ollama")


class OllamaEmbedder(EmbeddingProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_embedding_model
        self.dim = dim or settings.embedding_dim
        self.timeout = timeout
        self._client = client

    async def _post(self, payload: dict) -> dict:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(f"{self.base_url}/api/embed", json=payload)
            response.raise_for_status()
            return response.json()
        finally:
            if self._client is None:
                await client.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        batch_size = max(1, settings.embedding_batch_size)
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            try:
                data = await self._post({"model": self.model, "input": batch})
            except httpx.TimeoutException as exc:
                raise EmbeddingError(
                    "The embedding model timed out.", details={"model": self.model}
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise EmbeddingError(
                    f"Ollama rejected the embedding request ({exc.response.status_code}). "
                    f"Is `{self.model}` pulled?",
                    details={"model": self.model},
                ) from exc
            except httpx.HTTPError as exc:
                raise EmbeddingError(
                    "Could not reach Ollama for embeddings.",
                    details={"base_url": self.base_url},
                ) from exc

            batch_vectors = data.get("embeddings") or (
                [data["embedding"]] if "embedding" in data else []
            )
            if len(batch_vectors) != len(batch):
                raise EmbeddingError("Ollama returned an unexpected number of vectors.")
            for vec in batch_vectors:
                if len(vec) != self.dim:
                    raise EmbeddingError(
                        f"Embedding dimension mismatch: model returned {len(vec)}, "
                        f"schema expects {self.dim}. Set EMBEDDING_DIM and re-run migrations.",
                        details={"model": self.model, "returned_dim": len(vec)},
                    )
                vectors.append(list(vec))
            await asyncio.sleep(0)
        return vectors

    async def health(self) -> tuple[bool, str]:
        try:
            await self.embed_one("ping")
        except EmbeddingError as exc:
            return False, exc.message
        except Exception as exc:  # pragma: no cover
            return False, str(exc)[:200]
        return True, f"ollama:{self.model}"
