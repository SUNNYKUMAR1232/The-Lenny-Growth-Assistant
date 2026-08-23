"""Shared retrieval value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Candidate:
    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    title: str
    guest: str | None = None
    source_url: str | None = None
    vector_score: float | None = None
    keyword_score: float | None = None
    fused_score: float = 0.0
    vector_rank: int | None = None
    keyword_rank: int | None = None
    retrieval: str = "hybrid"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def citation_url(self) -> str | None:
        return self.metadata.get("deep_link") or self.source_url
