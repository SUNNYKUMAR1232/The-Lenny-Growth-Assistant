"""Embedding provider interface."""

from __future__ import annotations

import abc


class EmbeddingProvider(abc.ABC):
    name: str = "base"
    model: str = "unknown"
    dim: int = 768

    @abc.abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in order."""

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]

    async def health(self) -> tuple[bool, str]:
        """(available, detail)."""
        try:
            await self.embed_one("health check")
        except Exception as exc:  # pragma: no cover - provider specific
            return False, str(exc)[:200]
        return True, "ok"
