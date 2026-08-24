"""Pi Coding Agent provider tests.

The NDJSON fixtures below are **real output** captured from
`@earendil-works/pi-coding-agent` v0.84.2 running headlessly:

    pi --print --mode json --no-session --no-tools --offline \
       --model anthropic/claude-sonnet-4-5 --api-key <bad> "say ok"

Recording the actual event stream (rather than inventing one) is the point:
the parser is only useful if it matches what the CLI really emits.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.errors import LLMConfigError, LLMError, LLMTimeoutError
from app.llm.base import ChatMessage
from app.llm.pi_agent import PiCodingAgentProvider, parse_ndjson_events

pytestmark = pytest.mark.anyio


# --- recorded: authentication failure -------------------------------------
AUTH_FAILURE = [
    '{"type":"session","version":3,"id":"01a032e3","timestamp":"2026-08-24T08:29:54.433Z","cwd":"/tmp"}',
    '{"type":"agent_start"}',
    '{"type":"turn_start"}',
    '{"type":"message_start","message":{"role":"user","content":[{"type":"text","text":"say ok"}],"timestamp":1787560194478}}',
    '{"type":"message_end","message":{"role":"assistant","content":[],"api":"anthropic-messages",'
    '"provider":"anthropic","model":"claude-sonnet-4-5","usage":{"input":0,"output":0,"totalTokens":0},'
    '"stopReason":"error","timestamp":1787560194501,'
    '"errorMessage":"401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"API key is invalid.\\"}}"}}',
    '{"type":"agent_settled"}',
]

# --- same event shape, successful turn ------------------------------------
def _success_lines(text: str = "Retention compounds [S1].") -> list[str]:
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "api": "anthropic-messages",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "usage": {
            "input": 1204,
            "output": 87,
            "totalTokens": 1291,
            "cost": {"input": 0.0036, "output": 0.0013, "total": 0.0049},
        },
        "stopReason": "end_turn",
        "timestamp": 1787560194501,
    }
    return [
        '{"type":"session","version":3,"id":"01a0","cwd":"/tmp"}',
        '{"type":"agent_start"}',
        '{"type":"turn_start"}',
        json.dumps({"type": "message_end", "message": message}),
        # `agent_end` replays the same message — the parser must not double it.
        json.dumps({"type": "agent_end", "messages": [message], "willRetry": False}),
        '{"type":"agent_settled"}',
    ]


def test_parser_extracts_text_usage_and_cost() -> None:
    result = parse_ndjson_events(_success_lines())
    assert result.text == "Retention compounds [S1]."
    assert result.input_tokens == 1204
    assert result.output_tokens == 87
    assert result.stop_reason == "end_turn"
    assert result.cost_usd == pytest.approx(0.0049)
    assert result.error_message is None


def test_parser_does_not_duplicate_replayed_messages() -> None:
    """`agent_end` repeats every message; naive folding doubles the answer."""
    result = parse_ndjson_events(_success_lines("one."))
    assert result.text == "one."


def test_parser_surfaces_the_error_message() -> None:
    result = parse_ndjson_events(AUTH_FAILURE)
    assert result.text == ""
    assert "authentication_error" in (result.error_message or "")


def test_parser_ignores_noise_and_partial_lines() -> None:
    result = parse_ndjson_events(
        ["", "not json at all", "{broken", *_success_lines("kept.")]
    )
    assert result.text == "kept."


# ---------------------------------------------------------------- invocation
def test_invocation_disables_every_tool_and_all_ambient_discovery(monkeypatch) -> None:
    """The security-relevant assertion: Pi gets no tools and no local context."""
    provider = PiCodingAgentProvider(provider="anthropic", model="claude-sonnet-4-5")
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/usr/bin/pi")

    argv = provider._argv("SYSTEM RULES", "the question", None)

    assert argv[0] == "/usr/bin/pi"
    assert "--no-tools" in argv
    assert "--no-session" in argv
    assert "--no-extensions" in argv
    assert "--no-skills" in argv
    assert "--no-context-files" in argv
    assert "--no-prompt-templates" in argv
    assert argv[argv.index("--mode") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "anthropic/claude-sonnet-4-5"
    assert argv[argv.index("--system-prompt") + 1] == "SYSTEM RULES"
    assert argv[-1] == "the question"


def test_api_key_never_appears_on_the_command_line(monkeypatch) -> None:
    """argv is world-readable in /proc; credentials go through the env instead."""
    from app.llm.runtime_config import RuntimeModelConfig, clear_override, set_override

    set_override(
        RuntimeModelConfig(
            provider="pi",
            cloud_provider="anthropic",
            model="claude-sonnet-4-5",
            api_key="sk-ant-secret-0000",
        )
    )
    try:
        provider = PiCodingAgentProvider(provider="anthropic")
        monkeypatch.setattr(provider, "_resolve_cli", lambda: "/usr/bin/pi")

        argv = provider._argv("sys", "prompt", None)
        env = provider._child_env()

        assert "sk-ant-secret-0000" not in " ".join(argv)
        assert "--api-key" not in argv
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-secret-0000"
    finally:
        clear_override()


def test_local_backend_needs_no_key(monkeypatch) -> None:
    provider = PiCodingAgentProvider(provider="ollama", model="llama3.1:8b")
    env = provider._child_env()  # must not raise
    assert env["OLLAMA_HOST"] == settings.ollama_base_url


def test_missing_key_for_a_cloud_backend_is_a_config_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = PiCodingAgentProvider(provider="anthropic")
    with pytest.raises(LLMConfigError) as exc:
        provider._child_env()
    assert "ANTHROPIC_API_KEY" in exc.value.message


def test_oversized_prompt_fails_with_an_actionable_error(monkeypatch) -> None:
    """A CLI argv entry is capped at 128 KiB; say so instead of truncating."""
    provider = PiCodingAgentProvider(provider="anthropic")
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/usr/bin/pi")
    with pytest.raises(LLMError) as exc:
        provider._argv("x" * 200_000, "q", None)
    assert "RETRIEVAL_TOP_K" in exc.value.message


def test_missing_cli_explains_how_to_install() -> None:
    provider = PiCodingAgentProvider(cli_path="definitely-not-installed-pi")
    with pytest.raises(LLMConfigError) as exc:
        provider._resolve_cli()
    assert "npm install -g @earendil-works/pi-coding-agent" in exc.value.message


def test_history_is_folded_into_one_prompt_with_roles() -> None:
    provider = PiCodingAgentProvider()
    prompt = provider._flatten(
        [
            ChatMessage(role="user", content="what is activation?"),
            ChatMessage(role="assistant", content="It is the first value moment."),
            ChatMessage(role="user", content="how do I measure it?"),
        ]
    )
    assert prompt.startswith("User: what is activation?")
    assert "Assistant: It is the first value moment." in prompt
    assert prompt.endswith("User: how do I measure it?")


# ------------------------------------------------------------------ run paths
async def test_generate_parses_a_successful_run(monkeypatch) -> None:
    provider = PiCodingAgentProvider(provider="anthropic", model="claude-sonnet-4-5")
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/usr/bin/pi")
    monkeypatch.setattr(provider, "_child_env", lambda: {})

    async def fake_run(argv, env):
        return _success_lines(), "", 0

    monkeypatch.setattr(provider, "_run", fake_run)

    response = await provider.generate([ChatMessage(role="user", content="q")])
    assert response.text == "Retention compounds [S1]."
    assert response.provider == "pi"
    assert response.model == "anthropic/claude-sonnet-4-5"
    assert response.output_tokens == 87
    assert response.meta["agent"] == "pi"


async def test_generate_maps_auth_failure_to_a_config_error(monkeypatch) -> None:
    provider = PiCodingAgentProvider(provider="anthropic")
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/usr/bin/pi")
    monkeypatch.setattr(provider, "_child_env", lambda: {})

    async def fake_run(argv, env):
        return AUTH_FAILURE, "", 1

    monkeypatch.setattr(provider, "_run", fake_run)

    with pytest.raises(LLMConfigError) as exc:
        await provider.generate([ChatMessage(role="user", content="q")])
    assert exc.value.code == "MODEL_NOT_CONFIGURED"
    assert "rejected the API key" in exc.value.message


async def test_generate_maps_unreachable_backend(monkeypatch) -> None:
    provider = PiCodingAgentProvider(provider="ollama", model="llama3.1:8b")
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/usr/bin/pi")
    monkeypatch.setattr(provider, "_child_env", lambda: {})

    async def fake_run(argv, env):
        return [], "fetch failed: ECONNREFUSED 127.0.0.1:11434", 1

    monkeypatch.setattr(provider, "_run", fake_run)

    with pytest.raises(LLMError) as exc:
        await provider.generate([ChatMessage(role="user", content="q")])
    assert "make sure it is running" in exc.value.message


async def test_timeout_is_typed_and_kills_the_process(monkeypatch) -> None:
    provider = PiCodingAgentProvider(provider="anthropic", timeout=0.05)
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/bin/sleep")
    monkeypatch.setattr(provider, "_child_env", lambda: dict())
    monkeypatch.setattr(provider, "_argv", lambda system, prompt, max_tokens: ["/bin/sleep", "5"])

    with pytest.raises(LLMTimeoutError) as exc:
        await provider.generate([ChatMessage(role="user", content="q")])
    assert exc.value.code == "MODEL_TIMEOUT"


async def test_health_reads_pi_auth_check(monkeypatch) -> None:
    """`pi auth check --json` is the health probe — no model call, no cost."""
    provider = PiCodingAgentProvider(provider="anthropic")
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/bin/echo")
    monkeypatch.setattr(provider, "_child_env", lambda: dict())

    class _Process:
        returncode = 0

        async def communicate(self):
            return b'{"status":"ready","provider":"anthropic","authType":"api_key"}', b""

    async def fake_exec(*args, **kwargs):
        return _Process()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    ok, detail = await provider.health()
    assert ok is True
    assert "anthropic/claude-sonnet-4-5" in detail


async def test_health_explains_missing_credentials(monkeypatch) -> None:
    provider = PiCodingAgentProvider(provider="anthropic")
    monkeypatch.setattr(provider, "_resolve_cli", lambda: "/bin/echo")
    monkeypatch.setattr(provider, "_child_env", lambda: dict())

    class _Process:
        returncode = 0

        async def communicate(self):
            return (
                b'{"status":"not_ready","provider":"anthropic",'
                b'"reason":"credentials_not_configured"}',
                b"",
            )

    async def fake_exec(*args, **kwargs):
        return _Process()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    ok, detail = await provider.health()
    assert ok is False
    assert "no credentials" in detail.lower()
