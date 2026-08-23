"""Model gateway tests.

Ollama is exercised against an httpx MockTransport rather than a live server:
the contract we depend on is the shape of `/api/chat` and `/api/tags`, and a
mock lets CI assert the timeout, missing-model and unreachable-server paths
that are painful to reproduce for real.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.errors import LLMConfigError, LLMError, LLMTimeoutError
from app.llm.base import ChatMessage, extract_json
from app.llm.cloud import CloudProvider, _split_system
from app.llm.factory import get_provider
from app.llm.ollama import OllamaProvider
from app.llm.stub import StubProvider


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ollama_generate_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "Retention compounds."},
                "prompt_eval_count": 120,
                "eval_count": 8,
                "done_reason": "stop",
            },
        )

    provider = OllamaProvider(client=_client(handler), model="llama3.1:8b")
    response = await provider.generate(
        [ChatMessage(role="user", content="hi")], system="be brief"
    )
    assert response.text == "Retention compounds."
    assert response.provider == "ollama"
    assert response.output_tokens == 8
    assert response.latency_ms >= 0


async def test_ollama_stream_yields_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(
            json.dumps({"message": {"content": token}, "done": False}) + "\n"
            for token in ["Retention ", "compounds."]
        ) + json.dumps({"message": {"content": ""}, "done": True}) + "\n"
        return httpx.Response(200, content=body.encode())

    provider = OllamaProvider(client=_client(handler))
    tokens = [t async for t in provider.stream([ChatMessage(role="user", content="hi")])]
    assert "".join(tokens) == "Retention compounds."


async def test_ollama_missing_model_gives_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    provider = OllamaProvider(client=_client(handler), model="llama3.1:8b")
    with pytest.raises(LLMError) as exc:
        await provider.generate([ChatMessage(role="user", content="hi")])
    assert "ollama pull llama3.1:8b" in exc.value.message
    assert exc.value.code == "MODEL_UNAVAILABLE"


async def test_ollama_timeout_maps_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    provider = OllamaProvider(client=_client(handler))
    with pytest.raises(LLMTimeoutError) as exc:
        await provider.generate([ChatMessage(role="user", content="hi")])
    assert exc.value.code == "MODEL_TIMEOUT"
    assert exc.value.status_code == 504


async def test_ollama_unreachable_server_explains_how_to_fix() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider(client=_client(handler))
    with pytest.raises(LLMError) as exc:
        await provider.generate([ChatMessage(role="user", content="hi")])
    assert "ollama serve" in exc.value.message


async def test_ollama_health_checks_that_the_model_is_pulled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "mistral:latest"}]})

    provider = OllamaProvider(client=_client(handler), model="llama3.1:8b")
    ok, detail = await provider.health()
    assert ok is False
    assert "not pulled" in detail


async def test_ollama_health_accepts_matching_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

    provider = OllamaProvider(client=_client(handler), model="llama3.1:8b")
    ok, detail = await provider.health()
    assert ok is True
    assert "llama3.1:8b" in detail


async def test_cloud_provider_without_key_is_a_config_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    provider = CloudProvider(vendor="anthropic", model="claude-sonnet-4-5")
    with pytest.raises(LLMConfigError) as exc:
        await provider.generate([ChatMessage(role="user", content="hi")])
    assert exc.value.code == "MODEL_NOT_CONFIGURED"
    assert "ANTHROPIC_API_KEY" in exc.value.message


async def test_cloud_health_without_key_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
    ok, detail = await CloudProvider(vendor="openai").health()
    assert ok is False
    assert "OPENAI" in detail


def test_anthropic_system_prompts_are_hoisted() -> None:
    system, wire = _split_system(
        [ChatMessage(role="system", content="rule"), ChatMessage(role="user", content="q")],
        "base",
    )
    assert system == "base\n\nrule"
    assert wire == [{"role": "user", "content": "q"}]


def test_factory_never_silently_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    assert isinstance(get_provider(), OllamaProvider)
    monkeypatch.setattr(settings, "llm_provider", "cloud")
    assert isinstance(get_provider(), CloudProvider)
    monkeypatch.setattr(settings, "llm_provider", "stub")
    assert isinstance(get_provider(), StubProvider)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"route": "SHIP30"}', {"route": "SHIP30"}),
        ('```json\n{"route": "SHIP30"}\n```', {"route": "SHIP30"}),
        ('Sure! Here you go: {"route": "SHIP30"} — hope that helps', {"route": "SHIP30"}),
        ('[{"key": "a"}]', [{"key": "a"}]),
    ],
)
def test_structured_output_survives_chatty_models(raw: str, expected) -> None:
    assert extract_json(raw) == expected


def test_structured_output_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        extract_json("no json here at all")


async def test_stub_refuses_without_evidence() -> None:
    response = await StubProvider().generate(
        [ChatMessage(role="user", content="anything")], system="no evidence block here"
    )
    assert "don't have transcript evidence" in response.text
