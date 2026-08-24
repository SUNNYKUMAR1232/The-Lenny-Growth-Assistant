"""Runtime model configuration.

The security-relevant assertions here are the ones worth reading: a key goes
in, it never comes back out, and it never reaches the logs.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings
from app.llm import runtime_config
from app.llm.factory import get_provider, set_provider_override
from app.llm.runtime_config import RuntimeModelConfig, clear_override, describe, set_override

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean_override():
    clear_override()
    yield
    clear_override()


async def test_options_describe_every_provider(client: AsyncClient) -> None:
    body = (await client.get("/api/model/options")).json()
    ids = {p["id"] for p in body["providers"]}
    assert ids == {"ollama", "anthropic", "openai", "pi"}

    openai = next(p for p in body["providers"] if p["id"] == "openai")
    assert openai["needs_api_key"] is True
    assert openai["needs_base_url"] is True  # OpenAI-compatible gateways

    # Pi drives another provider underneath, so it offers a backend list.
    pi = next(p for p in body["providers"] if p["id"] == "pi")
    assert "ollama" in pi["backends"]
    assert "anthropic" in pi["backends"]


async def test_setting_a_cloud_config_switches_the_active_provider(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/model/config",
        json={
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "api_key": "sk-ant-test-000000000000abcd",
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["model"]["provider"] == "cloud"
    assert body["model"]["cloud_provider"] == "anthropic"
    assert body["model"]["label"] == "anthropic/claude-sonnet-4-5"
    assert body["model"]["source"] == "runtime"
    assert body["config"]["api_key_set"] is True


async def test_api_key_is_never_returned_to_the_client(client: AsyncClient) -> None:
    secret = "sk-ant-supersecret-value-9999"
    body = (
        await client.post(
            "/api/model/config",
            json={"provider": "anthropic", "model": "claude-sonnet-4-5", "api_key": secret},
        )
    ).json()

    assert secret not in str(body)
    assert body["config"]["api_key_hint"] == "…9999"

    # And not through any other read path either.
    status_body = (await client.get("/api/model")).json()
    assert secret not in str(status_body)
    health_body = (await client.get("/health")).json()
    assert secret not in str(health_body)


def test_config_log_event_carries_no_key(monkeypatch) -> None:
    """The call site must log `api_key_set`, never the key itself.

    Asserted at the call site rather than on captured output: structlog caches
    bound loggers on first use, so a module-level logger created during an
    earlier test ignores `capture_logs`. Redaction of whatever does reach the
    renderer is covered separately below.
    """
    captured: list[tuple[str, dict]] = []

    class _Recorder:
        def info(self, event: str, **kwargs) -> None:
            captured.append((event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            captured.append((event, kwargs))

    monkeypatch.setattr(runtime_config, "log", _Recorder())

    secret = "sk-ant-must-not-be-logged-4242"
    set_override(
        RuntimeModelConfig(
            provider="cloud",
            cloud_provider="anthropic",
            model="claude-sonnet-4-5",
            api_key=secret,
        )
    )

    event, fields = captured[0]
    assert event == "model.config_updated"
    assert fields["api_key_set"] is True
    assert secret not in str(fields)


def test_redaction_processor_scrubs_credentials() -> None:
    """Defence in depth: even if a call site slipped, the renderer scrubs it."""
    import io

    import structlog

    from app.observability.logging import configure_logging

    buffer = io.StringIO()
    try:
        configure_logging(stream=buffer)
        structlog.get_logger("redaction-probe").warning(
            "model.config_updated",
            anthropic_api_key="sk-ant-leaked-0001",
            api_key="sk-leaked-0002",
            model="claude-sonnet-4-5",
        )
    finally:
        configure_logging()

    written = buffer.getvalue()
    assert "sk-ant-leaked-0001" not in written
    assert "sk-leaked-0002" not in written
    assert written.count("***redacted***") == 2
    assert "claude-sonnet-4-5" in written  # non-sensitive fields survive


async def test_resubmitting_without_a_key_keeps_the_existing_one(
    client: AsyncClient,
) -> None:
    """The panel shows a masked key; saving a model change must not wipe it."""
    await client.post(
        "/api/model/config",
        json={"provider": "anthropic", "model": "claude-sonnet-4-5", "api_key": "sk-ant-keep-me-1234"},
    )
    body = (
        await client.post(
            "/api/model/config",
            json={"provider": "anthropic", "model": "claude-haiku-4-5"},
        )
    ).json()

    assert body["config"]["api_key_set"] is True
    assert body["config"]["api_key_hint"] == "…1234"
    assert body["model"]["model"] == "claude-haiku-4-5"


async def test_reset_reverts_to_the_environment_configuration(
    client: AsyncClient,
) -> None:
    await client.post(
        "/api/model/config",
        json={"provider": "openai", "model": "gpt-4o", "api_key": "sk-test-1234"},
    )
    body = (await client.delete("/api/model/config")).json()

    assert body["config"]["source"] == "environment"
    assert body["model"]["source"] == "environment"
    assert body["model"]["provider"] == settings.llm_provider


async def test_base_url_must_be_a_url(client: AsyncClient) -> None:
    response = await client.post(
        "/api/model/config",
        json={"provider": "openai", "base_url": "not-a-url", "api_key": "sk-x"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_config_can_be_locked_down(client: AsyncClient, monkeypatch) -> None:
    """A shared deployment configures models through the environment only."""
    monkeypatch.setattr(settings, "allow_runtime_model_config", False)
    response = await client.post(
        "/api/model/config", json={"provider": "anthropic", "api_key": "sk-x"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MODEL_CONFIG_LOCKED"


async def test_failed_test_does_not_change_the_active_provider(
    client: AsyncClient, monkeypatch
) -> None:
    """`/test` verifies a *proposal*; a bad key must leave the app as it was."""
    set_override(
        RuntimeModelConfig(
            provider="cloud",
            cloud_provider="anthropic",
            model="claude-sonnet-4-5",
            api_key="sk-ant-good-1111",
        )
    )
    before = describe()

    response = await client.post(
        "/api/model/test",
        json={"provider": "openai", "model": "gpt-4o"},  # no key -> must fail
    )
    body = response.json()
    assert body["ok"] is False
    assert "api key" in body["detail"].lower()
    assert describe() == before


async def test_ollama_override_changes_base_url_without_a_restart() -> None:
    set_provider_override(None)
    set_override(
        RuntimeModelConfig(
            provider="ollama", model="qwen2.5:7b", base_url="http://192.168.1.50:11434"
        )
    )
    provider = get_provider()
    assert provider.model == "qwen2.5:7b"
    assert provider.base_url == "http://192.168.1.50:11434"


def test_describe_never_includes_the_key() -> None:
    set_override(
        RuntimeModelConfig(
            provider="cloud",
            cloud_provider="openai",
            model="gpt-4o",
            api_key="sk-secret-abcd",
        )
    )
    payload = describe()
    assert "sk-secret-abcd" not in str(payload)
    assert payload["api_key_set"] is True
    assert runtime_config.get_override().api_key == "sk-secret-abcd"  # still usable
