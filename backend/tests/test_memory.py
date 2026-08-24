"""Memory tests: extraction filtering, query-dependent retrieval, isolation,
and the property that matters most — memory failure must not break RAG."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Memory, User
from app.memory import manager, retriever
from app.memory.extractor import MemoryCandidate, extract_memories, heuristic_extract
from app.llm.stub import StubProvider


async def test_heuristic_extraction_finds_role_and_preference() -> None:
    candidates = heuristic_extract(
        [
            "I'm a product manager at a seed stage startup.",
            "I prefer concise, practical answers.",
        ]
    )
    keys = {c.key for c in candidates}
    assert "role" in keys
    assert "preference" in keys
    assert all(0 <= c.confidence <= 1 for c in candidates)


async def test_extraction_ignores_transcript_content() -> None:
    candidates = heuristic_extract(
        ["What did Casey Winters say about retention curves on the podcast?"]
    )
    assert candidates == []


async def test_llm_extraction_failure_falls_back_to_heuristics() -> None:
    class Broken(StubProvider):
        async def structured_output(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("model down")

    candidates = await extract_memories(
        Broken(), [("user", "I'm a growth lead at a marketplace startup.")]
    )
    assert any(c.key == "role" for c in candidates)


async def test_low_confidence_candidates_are_not_stored(
    db: AsyncSession, user: User
) -> None:
    stored = await manager.store_candidates(
        db,
        user.id,
        [
            MemoryCandidate("semantic", "solid", "keeps things brief", 0.95, 0.9),
            MemoryCandidate("semantic", "shaky", "maybe likes charts", 0.2, 0.9),
            MemoryCandidate("semantic", "trivial", "mentioned coffee once", 0.95, 0.05),
        ],
    )
    await db.commit()
    assert [m.key for m in stored] == ["solid"]


async def test_repeated_extraction_upserts_rather_than_duplicating(
    db: AsyncSession, user: User
) -> None:
    await manager.store_candidates(
        db, user.id, [MemoryCandidate("semantic", "role", "PM", 0.7, 0.8)]
    )
    await db.commit()
    await manager.store_candidates(
        db, user.id, [MemoryCandidate("semantic", "role", "Head of Growth", 0.9, 0.8)]
    )
    await db.commit()

    memories = await manager.list_memories(db, user.id)
    assert len(memories) == 1
    assert memories[0].value == "Head of Growth"
    assert memories[0].confidence == pytest.approx(0.9)


async def test_duplicate_candidates_in_one_batch_do_not_lose_the_batch(
    db: AsyncSession, user: User
) -> None:
    """The extractor can state the same fact twice in a single batch.

    Both used to be INSERTed -- the loop only flushes at the end and the session
    is autoflush=False -- violating uq_memory_user_type_key and rolling back
    every memory in the batch, including the unrelated ones.
    """
    stored = await manager.store_candidates(
        db,
        user.id,
        [
            MemoryCandidate("semantic", "role", "PM", 0.7, 0.8),
            MemoryCandidate("semantic", "role", "Head of Growth", 0.9, 0.8),
            MemoryCandidate("semantic", "company_stage", "seed", 0.8, 0.8),
        ],
    )
    await db.commit()

    memories = {m.key: m for m in await manager.list_memories(db, user.id)}
    # The unrelated fact survived rather than being lost to the rollback.
    assert set(memories) == {"role", "company_stage"}
    # The more confident statement of the duplicated fact is the one kept.
    assert memories["role"].value == "Head of Growth"
    assert len(stored) == 2


async def test_memory_retrieval_is_query_dependent(db: AsyncSession, user: User) -> None:
    await manager.store_candidates(
        db,
        user.id,
        [
            MemoryCandidate(
                "semantic", "pricing_context", "works on pricing and packaging", 0.9, 0.9
            ),
            MemoryCandidate(
                "semantic", "hiring_context", "is hiring three engineers", 0.9, 0.5
            ),
        ],
    )
    await db.commit()

    top = await retriever.retrieve_memories(db, user.id, "help me with pricing", limit=1)
    assert top[0].key == "pricing_context"


async def test_memories_are_isolated_between_users(db: AsyncSession) -> None:
    alice = User(external_id="alice-mem", meta={})
    bob = User(external_id="bob-mem", meta={})
    db.add_all([alice, bob])
    await db.flush()

    await manager.store_candidates(
        db, alice.id, [MemoryCandidate("semantic", "role", "PM at Alice Co", 0.9, 0.9)]
    )
    await db.commit()

    assert len(await manager.list_memories(db, alice.id)) == 1
    assert await manager.list_memories(db, bob.id) == []


async def test_memory_retrieval_failure_does_not_break_chat(
    client: AsyncClient, corpus, monkeypatch
) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("memory subsystem offline")

    monkeypatch.setattr("app.agent.controller.retrieve_memories", boom)

    session_id = (
        await client.post("/api/sessions", json={"external_user_id": "alice"})
    ).json()["id"]
    response = await client.post(
        f"/api/sessions/{session_id}/messages", json={"content": "What drives retention?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["evidence"], "RAG must still work without memory"
    assert body["memories_used"] == []
    assert "Personalization is temporarily unavailable." in body["warnings"]


async def test_memory_api_roundtrip(client: AsyncClient) -> None:
    session = (
        await client.post("/api/sessions", json={"external_user_id": "memuser"})
    ).json()
    user_id = session["user_id"]

    created = await client.post(
        "/api/memories",
        json={
            "user_id": user_id,
            "key": "preferred_tone",
            "value": "direct, no fluff",
            "confidence": 0.95,
            "importance": 0.8,
        },
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    listed = (
        await client.get("/api/memories", params={"external_user_id": "memuser"})
    ).json()
    assert listed["enabled"] is True
    assert [m["key"] for m in listed["memories"]] == ["preferred_tone"]

    assert (await client.delete(f"/api/memories/{memory_id}")).status_code == 204
    after = (
        await client.get("/api/memories", params={"external_user_id": "memuser"})
    ).json()
    assert after["memories"] == []


async def test_memory_is_never_presented_as_evidence(
    client: AsyncClient, corpus
) -> None:
    """The prompt separation must survive into the response contract:
    memories appear under `memories_used`, never inside `evidence`."""
    session = (
        await client.post("/api/sessions", json={"external_user_id": "sep"})
    ).json()
    await client.post(
        "/api/memories",
        json={
            "user_id": session["user_id"],
            "key": "role",
            "value": "VP Product at a fintech",
            "confidence": 0.95,
            "importance": 0.9,
        },
    )
    body = (
        await client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"content": "What drives retention?"},
        )
    ).json()

    assert any(m["key"] == "role" for m in body["memories_used"])
    assert all("fintech" not in item["text"] for item in body["evidence"])
    assert all(item["chunk_id"] for item in body["evidence"])


async def test_cap_evicts_least_valuable_memories(db: AsyncSession, user: User, monkeypatch) -> None:
    monkeypatch.setattr(settings, "memory_max_per_user", 3)
    candidates = [
        MemoryCandidate("semantic", f"key_{i}", f"value {i}", 0.9, 0.4 + i * 0.1)
        for i in range(5)
    ]
    await manager.store_candidates(db, user.id, candidates)
    await db.commit()

    memories = await manager.list_memories(db, user.id)
    assert len(memories) == 3
    assert {m.key for m in memories} == {"key_2", "key_3", "key_4"}
