"""Pi Coding Agent provider.

The assignment asks for the agent layer to be built on the Anthropic Claude
Agent SDK **or** the Pi Coding Agent. This is that integration, satisfied with
Pi (https://pi.dev, `@earendil-works/pi-coding-agent`).

Pi ships as a Node CLI with a headless mode, so it is driven as a subprocess
speaking NDJSON rather than through a Python library. That is the supported
interface — the Python packages on PyPI named `pi-*` are unrelated projects —
and it has a real advantage here: the agent runs out-of-process, so a hung or
crashed agent cannot take the API down with it.

    pi -p --mode json --model <provider>/<model> --no-tools "<prompt>"

Invocation is deliberately bounded, which is what makes it fit the controlled
architecture rather than fighting it:

  --no-tools            Pi gets no read/bash/edit/write tools. Our controller
                        has already retrieved the evidence; the agent's job is
                        to reason over what it was given, not to roam a
                        filesystem. This is the single most important flag
                        here — it turns a coding agent into a bounded
                        generation step with a known blast radius.
  --no-session          No session files written; conversation state lives in
                        PostgreSQL, where the rest of the product keeps it.
  --no-extensions
  --no-skills
  --no-context-files    No ambient AGENTS.md/CLAUDE.md/extension discovery, so
  --no-prompt-templates behaviour depends only on what we pass in and is
                        reproducible across machines.
  --offline             Skips startup catalog fetches; the model call itself
                        still goes out.
  cwd = empty temp dir  Nothing local to discover even if a flag regresses.

Credentials go to the child through the **environment**, never through argv:
command lines are world-readable in `/proc`, environments are not.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.errors import LLMConfigError, LLMError, LLMTimeoutError
from app.llm.base import ChatMessage, LLMProvider, LLMResponse
from app.llm.runtime_config import api_key_for, base_url_for
from app.observability.logging import get_logger

log = get_logger("llm.pi")

# Linux caps a single argv entry at MAX_ARG_STRLEN (128 KiB). The system prompt
# carries the evidence pack, so it is the one that can realistically approach
# the limit; fail with an actionable error rather than a truncated prompt.
MAX_ARG_BYTES = 100_000

ENV_KEY_BY_PROVIDER = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
# Providers that run locally and need no credential.
LOCAL_PROVIDERS = {"ollama", "lmstudio", "llamacpp"}


@dataclass(slots=True)
class PiResult:
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    error_message: str | None = None
    events: int = 0
    cost_usd: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def parse_ndjson_events(lines: list[str]) -> PiResult:
    """Fold Pi's NDJSON event stream into one result.

    Pi emits: session, agent_start, turn_start, message_start, message_end,
    turn_end, agent_end, agent_settled. Assistant text lives in the `content`
    blocks of assistant messages; failures arrive as `errorMessage` on the
    message with `stopReason: "error"`.
    """
    result = PiResult()
    seen: set[int] = set()

    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        result.events += 1

        if event.get("type") not in {"message_end", "agent_end"}:
            continue

        messages = (
            event.get("messages", [])
            if event.get("type") == "agent_end"
            else [event.get("message", {})]
        )
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            # `agent_end` replays messages already seen at `message_end`;
            # dedupe on (timestamp, content) so text is not counted twice.
            marker = hash(
                (message.get("timestamp"), str(message.get("content"))[:200])
            )
            if marker in seen:
                continue
            seen.add(marker)

            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    result.text += block.get("text", "")

            usage = message.get("usage") or {}
            if usage:
                result.input_tokens = usage.get("input") or result.input_tokens
                result.output_tokens = usage.get("output") or result.output_tokens
                cost = usage.get("cost") or {}
                if isinstance(cost, dict) and cost.get("total") is not None:
                    result.cost_usd = cost.get("total")
            if message.get("stopReason"):
                result.stop_reason = message["stopReason"]
            if message.get("errorMessage"):
                result.error_message = message["errorMessage"]

    return result


class PiCodingAgentProvider(LLMProvider):
    name = "pi"

    def __init__(
        self,
        cli_path: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.cli_path = cli_path or settings.pi_cli_path
        self.provider_name = (provider or settings.pi_provider).lower()
        self.model = model or settings.pi_model
        self.timeout = timeout or settings.llm_timeout_seconds

    def label(self) -> str:
        return f"pi/{self.provider_name}/{self.model}"

    @property
    def model_ref(self) -> str:
        return f"{self.provider_name}/{self.model}"

    # ------------------------------------------------------------------ setup
    def _resolve_cli(self) -> str:
        found = shutil.which(self.cli_path) or (
            self.cli_path if os.path.isfile(self.cli_path) else None
        )
        if not found:
            raise LLMConfigError(
                "The Pi Coding Agent CLI was not found. Install it with "
                "`npm install -g @earendil-works/pi-coding-agent`, or set "
                "PI_CLI_PATH to its location.",
                details={"provider": "pi", "looked_for": self.cli_path},
            )
        return found

    def _child_env(self) -> dict[str, str]:
        """Credentials travel in the environment, never on the command line."""
        env = dict(os.environ)
        if self.provider_name in LOCAL_PROVIDERS:
            base_url = base_url_for(self.provider_name) or settings.ollama_base_url
            if self.provider_name == "ollama" and base_url:
                env["OLLAMA_HOST"] = base_url
            return env

        key = api_key_for(self.provider_name)
        env_name = ENV_KEY_BY_PROVIDER.get(self.provider_name)
        if key and env_name:
            env[env_name] = key
        elif env_name and not env.get(env_name):
            raise LLMConfigError(
                f"No API key configured for Pi's `{self.provider_name}` provider. "
                "Add one in the UI's model settings, set "
                f"{env_name}, or point PI_PROVIDER at a local model.",
                details={"provider": "pi", "vendor": self.provider_name},
            )
        return env

    def _argv(self, system: str, prompt: str, max_tokens: int | None) -> list[str]:
        for label, value in (("system prompt", system), ("prompt", prompt)):
            if len(value.encode("utf-8")) > MAX_ARG_BYTES:
                raise LLMError(
                    f"The {label} is too large to pass to the Pi CLI "
                    f"({len(value.encode('utf-8'))} bytes). Lower RETRIEVAL_TOP_K "
                    "or RETRIEVAL_MAX_CHARS_PER_CHUNK.",
                    details={"provider": "pi", "limit_bytes": MAX_ARG_BYTES},
                )

        argv = [
            self._resolve_cli(),
            "--print",
            "--mode", "json",
            "--model", self.model_ref,
            # Bounded by construction — see the module docstring.
            "--no-tools",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-context-files",
            "--no-prompt-templates",
            "--offline",
            "--thinking", settings.pi_thinking,
        ]
        if system:
            argv += ["--system-prompt", system]
        argv.append(prompt)
        return argv

    @staticmethod
    def _flatten(messages: list[ChatMessage]) -> str:
        """Pi's headless mode takes one prompt; conversation history is folded
        into it with explicit role labels so turn boundaries survive."""
        if len(messages) == 1:
            return messages[0].content
        parts = []
        for message in messages[:-1]:
            speaker = "User" if message.role == "user" else "Assistant"
            parts.append(f"{speaker}: {message.content}")
        parts.append(f"User: {messages[-1].content}")
        return "\n\n".join(parts)

    # --------------------------------------------------------------- generate
    async def _run(self, argv: list[str], env: dict[str, str]) -> tuple[list[str], str, int]:
        with tempfile.TemporaryDirectory(prefix="lga-pi-") as scratch:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=scratch,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.wait()
                raise LLMTimeoutError(
                    f"The Pi agent did not finish within {self.timeout:.0f}s.",
                    details={"provider": "pi", "model": self.model_ref},
                ) from exc

        return (
            stdout.decode("utf-8", errors="replace").splitlines(),
            stderr.decode("utf-8", errors="replace"),
            process.returncode or 0,
        )

    def _raise_for(self, result: PiResult, stderr: str, code: int) -> None:
        message = result.error_message or stderr.strip()
        lowered = message.lower()

        if "authentication" in lowered or "401" in lowered or "api key" in lowered:
            raise LLMConfigError(
                f"Pi's `{self.provider_name}` provider rejected the API key.",
                details={"provider": "pi", "vendor": self.provider_name},
            )
        if "provider_not_found" in lowered or "unknown model" in lowered:
            raise LLMConfigError(
                f"Pi does not know the model `{self.model_ref}`. Run `pi update` "
                "to refresh its model catalog, or check PI_PROVIDER/PI_MODEL.",
                details={"provider": "pi", "model": self.model_ref},
            )
        if "econnrefused" in lowered or "fetch failed" in lowered:
            raise LLMError(
                f"Pi could not reach the `{self.provider_name}` backend. "
                "If this is a local model, make sure it is running.",
                details={"provider": "pi", "vendor": self.provider_name},
            )
        raise LLMError(
            f"The Pi agent failed ({message[:160] or f'exit code {code}'}).",
            details={"provider": "pi", "model": self.model_ref},
        )

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # Pi owns sampling; `temperature` is accepted for interface parity and
        # deliberately not forwarded (its CLI exposes `--thinking`, not
        # temperature). PI_THINKING is the equivalent knob.
        env = self._child_env()
        argv = self._argv(system or "", self._flatten(messages), max_tokens)

        started = time.perf_counter()
        lines, stderr, code = await self._run(argv, env)
        result = parse_ndjson_events(lines)
        latency = (time.perf_counter() - started) * 1000

        if result.error_message or (code != 0 and not result.text):
            log.error(
                "llm.failed",
                provider="pi",
                model=self.model_ref,
                exit_code=code,
                error=(result.error_message or stderr)[:200],
            )
            self._raise_for(result, stderr, code)

        if not result.text.strip():
            raise LLMError(
                "The Pi agent returned an empty response.",
                details={"provider": "pi", "model": self.model_ref},
            )

        return LLMResponse(
            text=result.text,
            provider="pi",
            model=self.model_ref,
            latency_ms=round(latency, 2),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            finish_reason=result.stop_reason,
            meta={"agent": "pi", "events": result.events, "cost_usd": result.cost_usd},
        )

    # ----------------------------------------------------------------- stream
    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Coarse-grained streaming.

        Pi's `--mode json` emits whole messages, not token deltas, so this
        yields each assistant message as it completes rather than word by word.
        The SSE contract is unchanged; the UI simply fills in larger steps.
        """
        env = self._child_env()
        argv = self._argv(system or "", self._flatten(messages), max_tokens)
        cli = argv[0]

        with tempfile.TemporaryDirectory(prefix="lga-pi-") as scratch:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=scratch,
            )
            assert process.stdout is not None
            emitted = False
            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(
                            process.stdout.readline(), timeout=self.timeout
                        )
                    except asyncio.TimeoutError as exc:
                        process.kill()
                        await process.wait()
                        raise LLMTimeoutError(
                            f"The Pi agent stalled for {self.timeout:.0f}s.",
                            details={"provider": "pi", "model": self.model_ref},
                        ) from exc
                    if not raw:
                        break

                    piece = parse_ndjson_events([raw.decode("utf-8", errors="replace")])
                    if piece.error_message:
                        stderr = (await process.stderr.read()).decode(errors="replace")
                        self._raise_for(piece, stderr, process.returncode or 0)
                    if piece.text:
                        emitted = True
                        yield piece.text
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

            if not emitted:
                raise LLMError(
                    "The Pi agent produced no output.",
                    details={"provider": "pi", "cli": cli},
                )

    # ----------------------------------------------------------------- health
    async def health(self) -> tuple[bool, str]:
        """`pi auth check` — cheap, offline, and it verifies the real thing."""
        try:
            cli = self._resolve_cli()
        except LLMConfigError as exc:
            return False, exc.message

        try:
            env = self._child_env()
        except LLMConfigError as exc:
            return False, exc.message

        try:
            process = await asyncio.create_subprocess_exec(
                cli, "auth", "check", "--provider", self.provider_name, "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except asyncio.TimeoutError:
            return False, "`pi auth check` timed out."
        except Exception as exc:  # pragma: no cover - spawn failures
            return False, f"Could not run the Pi CLI: {type(exc).__name__}"

        try:
            payload = json.loads(stdout.decode().strip() or "{}")
        except json.JSONDecodeError:
            return False, "Unexpected output from `pi auth check`."

        if payload.get("status") == "ready":
            return True, f"pi:{self.model_ref} ({payload.get('authType', 'ready')})"

        reason = payload.get("reason", "not ready")
        if reason == "credentials_not_configured":
            return False, (
                f"Pi has no credentials for `{self.provider_name}`. Add a key in "
                "model settings or set the provider's environment variable."
            )
        if reason == "provider_not_found":
            return False, (
                f"Pi does not know the provider `{self.provider_name}`. Run "
                "`pi update` to refresh its model catalog."
            )
        return False, f"Pi provider `{self.provider_name}`: {reason}"
