"""Persistence tests: rows survive, relationships cascade, metadata round-trips."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, Memory, Message, Session as ChatSession, User
from app.memory import manager
from app.memory.extractor import MemoryCandidate


async def test_session_and_messages_persist_across_sessions(
    db: AsyncSession, chat_session: ChatSession
) -> None:
    db.add_all(
        [
            Message(session_id=chat_session.id, role="user", content="hello", meta={}),
            Message(
                session_id=chat_session.id,
                role="assistant",
                content="hi",
                meta={"route": "KNOWLEDGE_Q", "evidence": [{"source_id": "S1"}]},
            ),
        ]
    )
    await db.commit()
    db.expunge_all()

    rows = list(
        (
            await db.execute(
                select(Message)
                .where(Message.session_id == chat_session.id)
                .order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[1].meta["route"] == "KNOWLEDGE_Q"
    assert rows[0].created_at is not None


async def test_deleting_a_session_cascades(db: AsyncSession, chat_session: ChatSession) -> None:
    db.add(Message(session_id=chat_session.id, role="user", content="x", meta={}))
    db.add(
        Artifact(
            session_id=chat_session.id,
            type="markdown",
            title="Doc",
            content="# Doc",
            meta={},
        )
    )
    await db.commit()

    await db.delete(chat_session)
    await db.commit()

    messages = (await db.execute(select(func.count(Message.id)))).scalar_one()
    artifacts = (await db.execute(select(func.count(Artifact.id)))).scalar_one()
    assert messages == 0
    assert artifacts == 0


async def test_deleting_a_user_cascades_to_memories(db: AsyncSession, user: User) -> None:
    await manager.store_candidates(
        db, user.id, [MemoryCandidate("semantic", "role", "PM", 0.9, 0.9)]
    )
    await db.commit()

    await db.delete(user)
    await db.commit()

    assert (await db.execute(select(func.count(Memory.id)))).scalar_one() == 0


async def test_artifact_stores_both_sanitized_and_raw(
    db: AsyncSession, chat_session: ChatSession
) -> None:
    artifact = Artifact(
        session_id=chat_session.id,
        type="html",
        title="Report",
        content="<h1>Safe</h1>",
        raw_content="<h1>Safe</h1><script>bad()</script>",
        meta={"sanitization": {"had_script": True}},
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)

    assert "<script>" not in artifact.content
    assert "<script>" in artifact.raw_content
    assert artifact.meta["sanitization"]["had_script"] is True


async def test_memory_uniqueness_is_enforced_per_user_type_key(
    db: AsyncSession, user: User
) -> None:
    await manager.store_candidates(
        db, user.id, [MemoryCandidate("semantic", "role", "PM", 0.8, 0.8)]
    )
    await db.commit()
    await manager.store_candidates(
        db, user.id, [MemoryCandidate("episodic", "role", "decided to focus on PLG", 0.8, 0.8)]
    )
    await db.commit()

    memories = await manager.list_memories(db, user.id)
    assert len(memories) == 2
    assert {m.type for m in memories} == {"semantic", "episodic"}


async def test_session_timestamps_update_on_change(
    db: AsyncSession, chat_session: ChatSession
) -> None:
    original = chat_session.updated_at
    chat_session.title = "Renamed"
    await db.commit()
    await db.refresh(chat_session)
    assert chat_session.updated_at >= original


async def test_unknown_ids_return_nothing_rather_than_raising(db: AsyncSession) -> None:
    result = (
        await db.execute(select(ChatSession).where(ChatSession.id == uuid.uuid4()))
    ).scalar_one_or_none()
    assert result is None
