"""Deterministic hashing embedder.

This is a *test double and last-resort fallback*, not a semantic model. It
projects hashed word n-grams into a fixed-dimension unit vector, which gives:

  * exact-repeat similarity  (identical text -> identical vector)
  * partial lexical overlap  (shared vocabulary -> non-zero cosine)
  * zero external dependencies, so CI and `pytest` run with no model server

It has **no semantic generalisation**: "churn" and "retention" are unrelated
to it. It exists so the system degrades to *lexical-only* quality instead of
failing outright, and so the retrieval/agent/grounding paths stay testable
without Ollama. Never present its results as vector search quality; the API
reports `embedding_provider=hash` and the UI surfaces a degraded badge.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.embeddings.base import EmbeddingProvider

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class HashEmbedder(EmbeddingProvider):
    name = "hash"

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.model = f"deterministic-hash-{dim}"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = _tokens(text)
        if not toks:
            return vec
        grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def health(self) -> tuple[bool, str]:
        return True, "deterministic fallback embedder (not semantic)"
