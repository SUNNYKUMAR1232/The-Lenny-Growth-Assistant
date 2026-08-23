"""Query-dependent memory retrieval.

Injecting every memory into every prompt is the failure mode this module
exists to avoid: it burns context, and it makes the model drag irrelevant
personal facts into unrelated answers. Memories are ranked per query.

    relevance = 0.65 * cosine(query, memory) + 0.25 * importance + 0.10 * recency

If memory embeddings are missing (embedding model was down at write time) the
query falls back to importance-ordered retrieval, and if the memory subsystem
fails entirely it returns [] — the assistant still answers from transcript
evidence. Memory is an enhancement, never a dependency.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Memory
from app.embeddings.factory import embed_with_fallback
from app.observability.logging import get_logger

log = get_logger("memory.retriever")

VECTOR_WEIGHT = 0.65
IMPORTANCE_WEIGHT = 0.25
RECENCY_WEIGHT = 0.10
RECENCY_HALF_LIFE_DAYS = 30.0


def _recency(updated_at: datetime | None) -> float:
    if updated_at is None:
        return 0.0
    now = datetime.now(timezone.utc)
    reference = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - reference).total_seconds() / 86400)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


async def retrieve_memories(
    session: AsyncSession, user_id: uuid.UUID, query: str, limit: int | None = None
) -> list[Memory]:
    if not settings.memory_enabled:
        return []
    limit = limit or settings.memory_top_k

    try:
        now = datetime.now(timezone.utc)
        rows = list(
            (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == user_id,
                        (Memory.expires_at.is_(None)) | (Memory.expires_at > now),
                    )
                )
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        log.error("database.error", stage="memory_retrieval", error=str(exc))
        return []

    if not rows:
        return []

    query_vector: list[float] | None = None
    try:
        result = await embed_with_fallback([query])
        query_vector = result.vectors[0]
    except Exception as exc:
        log.warning("memory.query_embedding_failed", error=str(exc)[:200])

    scored: list[tuple[float, Memory]] = []
    for memory in rows:
        similarity = 0.0
        if query_vector is not None and memory.embedding is not None:
            similarity = _cosine(query_vector, list(memory.embedding))
        score = (
            VECTOR_WEIGHT * similarity
            + IMPORTANCE_WEIGHT * float(memory.importance)
            + RECENCY_WEIGHT * _recency(memory.updated_at)
        )
        scored.append((score, memory))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected = [memory for _, memory in scored[:limit]]
    log.info("memory.retrieved", count=len(selected), available=len(rows))
    return selected


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))
