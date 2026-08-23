"""Embedding provider selection + graceful degradation.

`get_embedder()` returns the configured provider. `embed_with_fallback()` is
what callers use at query time: if the configured provider is unavailable and
`EMBEDDING_ALLOW_FALLBACK=true`, it degrades to the deterministic embedder and
tells the caller it did, so the UI can say so instead of silently lying about
search quality.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.hash_embedder import HashEmbedder
from app.embeddings.ollama_embedder import OllamaEmbedder
from app.embeddings.openai_embedder import OpenAIEmbedder
from app.errors import EmbeddingError
from app.observability.logging import get_logger

log = get_logger("embeddings")

_override: EmbeddingProvider | None = None


def set_embedder_override(provider: EmbeddingProvider | None) -> None:
    """Used by tests to inject a deterministic embedder."""
    global _override
    _override = provider


def get_embedder(name: str | None = None) -> EmbeddingProvider:
    if _override is not None:
        return _override
    choice = (name or settings.embedding_provider).lower()
    if choice == "ollama":
        return OllamaEmbedder()
    if choice == "openai":
        return OpenAIEmbedder(dim=settings.embedding_dim)
    return HashEmbedder(dim=settings.embedding_dim)


@dataclass(slots=True)
class EmbedResult:
    vectors: list[list[float]]
    model: str
    provider: str
    degraded: bool = False
    reason: str | None = None


async def embed_with_fallback(texts: list[str]) -> EmbedResult:
    primary = get_embedder()
    try:
        vectors = await primary.embed(texts)
        return EmbedResult(vectors, primary.model, primary.name)
    except EmbeddingError as exc:
        if not settings.embedding_allow_fallback or primary.name == "hash":
            raise
        log.warning(
            "embedding.fallback",
            primary=primary.name,
            model=primary.model,
            reason=exc.message,
        )
        fallback = HashEmbedder(dim=settings.embedding_dim)
        vectors = await fallback.embed(texts)
        return EmbedResult(
            vectors, fallback.model, fallback.name, degraded=True, reason=exc.message
        )
