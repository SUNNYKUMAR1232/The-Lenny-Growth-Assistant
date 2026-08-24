"""API contract tests: health, sessions, messages, validation, errors."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def test_health_reports_components(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["components"]["database"]["status"] == "ok"
    assert set(body["components"]) == {
        "database",
        "knowledge_base",
        "model",
        "embeddings",
    }


async def test_model_endpoint_exposes_active_provider(client: AsyncClient) -> None:
    body = (await client.get("/api/model")).json()
    assert body["provider"] == "stub"
    assert body["label"] == "stub/deterministic"
    assert body["embedding_provider"] == "hash"


async def test_create_and_fetch_session(client: AsyncClient) -> None:
    created = await client.post("/api/sessions", json={"external_user_id": "alice"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    fetched = await client.get(f"/api/sessions/{session_id}")
    assert fetched.status_code == 200
    assert fetched.json()["session"]["id"] == session_id
    assert fetched.json()["messages"] == []


async def test_sessions_are_listed_per_user(client: AsyncClient) -> None:
    await client.post("/api/sessions", json={"external_user_id": "alice"})
    await client.post("/api/sessions", json={"external_user_id": "alice"})
    await client.post("/api/sessions", json={"external_user_id": "bob"})

    alice = (await client.get("/api/sessions", params={"external_user_id": "alice"})).json()
    bob = (await client.get("/api/sessions", params={"external_user_id": "bob"})).json()

    assert alice["total"] == 2
    assert bob["total"] == 1


async def test_unknown_session_returns_structured_error(client: AsyncClient) -> None:
    response = await client.get(f"/api/sessions/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


async def test_message_validation_rejects_empty_content(client: AsyncClient) -> None:
    session_id = (
        await client.post("/api/sessions", json={"external_user_id": "alice"})
    ).json()["id"]
    response = await client.post(
        f"/api/sessions/{session_id}/messages", json={"content": ""}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["fields"]


async def test_message_to_missing_session_is_404(client: AsyncClient) -> None:
    response = await client.post(
        f"/api/sessions/{uuid.uuid4()}/messages", json={"content": "hello"}
    )
    assert response.status_code == 404


async def test_request_id_header_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "req-123"})
    assert response.headers["X-Request-ID"] == "req-123"


async def test_delete_session_removes_it(client: AsyncClient) -> None:
    session_id = (
        await client.post("/api/sessions", json={"external_user_id": "alice"})
    ).json()["id"]
    assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 204
    assert (await client.get(f"/api/sessions/{session_id}")).status_code == 404
