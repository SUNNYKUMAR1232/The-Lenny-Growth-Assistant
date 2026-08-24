"""Memory persistence and lifecycle.

Writes are upserts on (user_id, type, key): a user who says "actually, make it
detailed" should not accumulate two contradictory memories. On conflict the
higher-confidence statement wins, and `updated_at` moves so recency ranking
stays honest.

The store is capped (MEMORY_MAX_PER_USER). When the cap is hit, the lowest
`importance * confidence` rows are evicted — a bounded store is one an
operator can reason about and a user can read in one screen.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Memory
from app.embeddings.factory import embed_with_fallback
from app.memory.extractor import MemoryCandidate
from app.observability.logging import get_logger

log = get_logger("memory")


async def _embed_value(text: str) -> list[float] | None:
    try:
        result = await embed_with_fallback([text])
        return result.vectors[0]
    except Exception as exc:
        log.warning("memory.embedding_failed", error=str(exc)[:200])
        return None


def passes_filter(candidate: MemoryCandidate) -> bool:
    return (
        candidate.confidence >= settings.memory_min_confidence
        and candidate.importance >= settings.memory_min_importance
    )


async def store_candidates(
    session: AsyncSession,
    user_id: uuid.UUID,
    candidates: list[MemoryCandidate],
    source_session_id: uuid.UUID | None = None,
) -> list[Memory]:
    """Persist the candidates that clear the confidence/importance filter."""
    accepted = [c for c in candidates if passes_filter(c)]
    rejected = len(candidates) - len(accepted)

    # One batch can state the same fact twice ("role: PM" from two turns). The
    # loop below reads existing rows but only flushes at the end, and the
    # session runs with autoflush=False, so a second candidate with the same
    # (type, key) still looks new -- both get INSERTed and violate
    # uq_memory_user_type_key, rolling back every memory in the batch. Collapse
    # duplicates first, keeping the most confident statement of each fact,
    # which is the same rule the upsert below applies across batches.
    by_identity: dict[tuple[str, str], MemoryCandidate] = {}
    for candidate in accepted:
        prior = by_identity.get((candidate.type, candidate.key))
        if prior is None or candidate.confidence > prior.confidence:
            by_identity[(candidate.type, candidate.key)] = candidate
    accepted = list(by_identity.values())

    stored: list[Memory] = []

    for candidate in accepted:
        try:
            existing = (
                await session.execute(
                    select(Memory).where(
                        Memory.user_id == user_id,
                        Memory.type == candidate.type,
                        Memory.key == candidate.key,
                    )
                )
            ).scalar_one_or_none()

            embedding = await _embed_value(f"{candidate.key}: {candidate.value}")

            if existing:
                if candidate.confidence >= existing.confidence:
                    existing.value = candidate.value
                    existing.confidence = candidate.confidence
                    existing.importance = max(existing.importance, candidate.importance)
                    existing.embedding = embedding
                    existing.source_session_id = source_session_id
                    existing.updated_at = datetime.now(timezone.utc)
                    stored.append(existing)
                continue

            memory = Memory(
                user_id=user_id,
                type=candidate.type,
                key=candidate.key,
                value=candidate.value,
                confidence=candidate.confidence,
                importance=candidate.importance,
                source_session_id=source_session_id,
                embedding=embedding,
                meta={"source": candidate.source},
            )
            session.add(memory)
            stored.append(memory)
        except SQLAlchemyError as exc:  # pragma: no cover
            log.error("database.error", stage="memory_store", error=str(exc))
            raise

    if stored:
        await session.flush()
        await _enforce_cap(session, user_id)

    log.info(
        "memory.extracted",
        candidates=len(candidates),
        stored=len(stored),
        filtered_out=rejected,
    )
    return stored


async def _enforce_cap(session: AsyncSession, user_id: uuid.UUID) -> None:
    total = (
        await session.execute(
            select(func.count(Memory.id)).where(Memory.user_id == user_id)
        )
    ).scalar_one()
    if total <= settings.memory_max_per_user:
        return
    surplus = total - settings.memory_max_per_user
    victims = (
        (
            await session.execute(
                select(Memory.id)
                .where(Memory.user_id == user_id)
                .order_by((Memory.importance * Memory.confidence).asc(), Memory.updated_at.asc())
                .limit(surplus)
            )
        )
        .scalars()
        .all()
    )
    if victims:
        await session.execute(delete(Memory).where(Memory.id.in_(victims)))
        log.info("memory.evicted", count=len(victims), user_id=str(user_id))


async def list_memories(session: AsyncSession, user_id: uuid.UUID) -> list[Memory]:
    return list(
        (
            await session.execute(
                select(Memory)
                .where(Memory.user_id == user_id)
                .order_by(Memory.importance.desc(), Memory.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def delete_memory(session: AsyncSession, memory_id: uuid.UUID) -> bool:
    result = await session.execute(delete(Memory).where(Memory.id == memory_id))
    return bool(result.rowcount)


async def clear_memories(session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await session.execute(delete(Memory).where(Memory.user_id == user_id))
    return int(result.rowcount or 0)


async def upsert_manual(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    type_: str,
    key: str,
    value: str,
    confidence: float,
    importance: float,
) -> Memory:
    """User-authored memory from the memory panel — always accepted."""
    existing = (
        await session.execute(
            select(Memory).where(
                Memory.user_id == user_id, Memory.type == type_, Memory.key == key
            )
        )
    ).scalar_one_or_none()
    embedding = await _embed_value(f"{key}: {value}")
    if existing:
        existing.value = value
        existing.confidence = confidence
        existing.importance = importance
        existing.embedding = embedding
        existing.meta = {**(existing.meta or {}), "source": "user"}
        await session.flush()
        return existing
    memory = Memory(
        user_id=user_id,
        type=type_,
        key=key,
        value=value,
        confidence=confidence,
        importance=importance,
        embedding=embedding,
        meta={"source": "user"},
    )
    session.add(memory)
    await session.flush()
    return memory
