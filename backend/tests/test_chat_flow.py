"""End-to-end chat: routing, grounding, persistence, session isolation, SSE."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.usefixtures("corpus")


async def _new_session(client: AsyncClient, user: str = "alice") -> str:
    return (await client.post("/api/sessions", json={"external_user_id": user})).json()["id"]


async def test_knowledge_question_returns_grounded_answer(client: AsyncClient) -> None:
    session_id = await _new_session(client)
    response = await client.post(
        f"/api/sessions/{session_id}/messages",
        json={"content": "How do you know if you have product-market fit?"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["route"] == "KNOWLEDGE_Q"
    assert body["message"]["role"] == "assistant"
    assert body["evidence"], "a grounded answer must expose its sources"
    assert body["evidence"][0]["source_url"]
    assert body["grounding"]["action"] in {"accepted", "annotated"}
    assert body["model"]["label"] == "stub/deterministic"
    assert body["latency_ms"] >= 0


async def test_conversation_history_is_persisted_and_ordered(client: AsyncClient) -> None:
    session_id = await _new_session(client)
    await client.post(
        f"/api/sessions/{session_id}/messages", json={"content": "What drives retention?"}
    )
    await client.post(
        f"/api/sessions/{session_id}/messages", json={"content": "And how do teams measure it?"}
    )

    detail = (await client.get(f"/api/sessions/{session_id}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][0]["content"] == "What drives retention?"
    assert detail["session"]["title"].startswith("What drives retention")


async def test_sessions_are_isolated(client: AsyncClient) -> None:
    first = await _new_session(client, "alice")
    second = await _new_session(client, "alice")

    await client.post(f"/api/sessions/{first}/messages", json={"content": "What is PMF?"})

    assert len((await client.get(f"/api/sessions/{first}")).json()["messages"]) == 2
    assert (await client.get(f"/api/sessions/{second}")).json()["messages"] == []


async def test_assistant_message_metadata_is_traceable(client: AsyncClient) -> None:
    session_id = await _new_session(client)
    body = (
        await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "What is the retention curve?"},
        )
    ).json()
    meta = body["message"]["metadata"]
    assert meta["route"] == "KNOWLEDGE_Q"
    assert meta["provider"] == "stub"
    assert meta["evidence"]
    assert meta["grounding"]["checked_claims"] >= 0
    assert meta["request_id"]


async def test_artifact_route_produces_sanitized_artifact(client: AsyncClient) -> None:
    session_id = await _new_session(client)
    body = (
        await client.post(
            f"/api/sessions/{session_id}/messages",
            json={
                "content": "Build me an HTML one-pager about retention",
                "artifact_format": "html",
            },
        )
    ).json()

    assert body["route"] == "ARTIFACT"
    artifact = body["artifact"]
    assert artifact is not None
    assert artifact["type"] == "html"
    assert "<script" not in artifact["content"].lower()
    assert "Content-Security-Policy" in artifact["content"]

    listed = (
        await client.get("/api/artifacts", params={"session_id": session_id})
    ).json()
    assert len(listed["artifacts"]) == 1


async def test_ship30_route_produces_markdown_artifact(client: AsyncClient) -> None:
    session_id = await _new_session(client)
    body = (
        await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "Write a Ship 30 for 30 essay about retention"},
        )
    ).json()

    assert body["route"] == "SHIP30"
    assert body["artifact"]["type"] == "markdown"
    assert body["message"]["metadata"]["skill"]["skill"] == "ship30"
    assert body["message"]["metadata"]["skill"]["word_count"] > 0


async def test_question_with_no_evidence_refuses_instead_of_inventing(
    client: AsyncClient, db
) -> None:
    """With nothing retrievable, the assistant must refuse rather than answer.

    The corpus is emptied for this test so the Evidence Pack is genuinely
    empty — the condition the RAG skill short-circuits on.
    """
    from sqlalchemy import text

    await db.execute(text("DELETE FROM documents"))
    await db.commit()

    session_id = await _new_session(client)
    body = (
        await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "What is the airspeed velocity of an unladen swallow?"},
        )
    ).json()

    assert body["evidence"] == []
    assert "couldn't find" in body["message"]["content"].lower()
    assert "No transcript evidence matched this question." in body["warnings"]
    assert body["message"]["metadata"]["skill"]["llm_called"] is False


async def test_streaming_endpoint_emits_pipeline_events(client: AsyncClient) -> None:
    session_id = await _new_session(client)
    events: list[tuple[str, dict]] = []

    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages",
        json={"content": "What is product-market fit?", "stream": True},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: ") and name:
                events.append((name, json.loads(line[6:])))

    names = [name for name, _ in events]
    assert "route" in names
    assert "evidence" in names
    assert "token" in names
    assert names[-1] == "final"

    final = dict(events)["final"]
    assert final["message"]["content"]
    assert final["evidence"]
