"""Cloud model provider (Anthropic Claude by default, OpenAI supported).

Selected with `LLM_PROVIDER=cloud` + `CLOUD_PROVIDER=anthropic|openai`.
SDK imports are lazy so a deployment that only ever runs Ollama never needs
the cloud SDKs to be importable, and a missing API key produces a typed
`MODEL_NOT_CONFIGURED` error instead of a traceback.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from app.config import settings
from app.errors import LLMConfigError, LLMError, LLMTimeoutError
from app.llm.base import ChatMessage, LLMProvider, LLMResponse
from app.observability.logging import get_logger

log = get_logger("llm.cloud")


def _split_system(messages: list[ChatMessage], system: str | None) -> tuple[str | None, list[dict]]:
    """Anthropic takes `system` out-of-band; fold any system turns into it."""
    system_parts = [system] if system else []
    wire: list[dict] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
        else:
            wire.append({"role": message.role, "content": message.content})
    if not wire:
        wire = [{"role": "user", "content": "(no user message)"}]
    return ("\n\n".join(p for p in system_parts if p) or None), wire


class CloudProvider(LLMProvider):
    name = "cloud"

    def __init__(self, vendor: str | None = None, model: str | None = None) -> None:
        self.vendor = (vendor or settings.cloud_provider).lower()
        self.model = model or settings.cloud_model
        self.timeout = settings.llm_timeout_seconds

    def label(self) -> str:
        return f"{self.vendor}/{self.model}"

    # ------------------------------------------------------------- clients
    def _anthropic(self):
        if not settings.anthropic_api_key:
            raise LLMConfigError(
                "ANTHROPIC_API_KEY is not set. Set it, or run locally with "
                "LLM_PROVIDER=ollama.",
                details={"provider": "anthropic"},
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("The `anthropic` package is not installed.") from exc
        return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=self.timeout)

    def _openai(self):
        if not settings.openai_api_key:
            raise LLMConfigError(
                "OPENAI_API_KEY is not set. Set it, or run locally with "
                "LLM_PROVIDER=ollama.",
                details={"provider": "openai"},
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("The `openai` package is not installed.") from exc
        return AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=self.timeout,
        )

    def _wrap_error(self, exc: Exception) -> LLMError:
        name = type(exc).__name__.lower()
        if "timeout" in name:
            return LLMTimeoutError(
                f"The cloud model `{self.model}` timed out.",
                details={"provider": self.vendor, "model": self.model},
            )
        if "authentication" in name or "permission" in name:
            return LLMConfigError(
                "The cloud provider rejected the API key.",
                details={"provider": self.vendor},
            )
        if "ratelimit" in name:
            return LLMError(
                "The cloud provider is rate limiting this key. Try again shortly.",
                details={"provider": self.vendor},
            )
        return LLMError(
            f"The cloud model request failed ({type(exc).__name__}).",
            details={"provider": self.vendor, "model": self.model},
        )

    # ------------------------------------------------------------ generate
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        temp = settings.llm_temperature if temperature is None else temperature
        tokens = max_tokens or settings.llm_max_output_tokens

        if self.vendor == "anthropic":
            client = self._anthropic()
            sys_prompt, wire = _split_system(messages, system)
            try:
                result = await client.messages.create(
                    model=self.model,
                    max_tokens=tokens,
                    temperature=temp,
                    system=sys_prompt or "",
                    messages=wire,
                )
            except Exception as exc:
                raise self._wrap_error(exc) from exc
            text = "".join(
                block.text for block in result.content if getattr(block, "type", "") == "text"
            )
            return LLMResponse(
                text=text,
                provider=self.vendor,
                model=self.model,
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=getattr(result.usage, "input_tokens", None),
                output_tokens=getattr(result.usage, "output_tokens", None),
                finish_reason=getattr(result, "stop_reason", None),
            )

        client = self._openai()
        wire = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m.role, "content": m.content} for m in messages
        ]
        try:
            result = await client.chat.completions.create(
                model=self.model, messages=wire, temperature=temp, max_tokens=tokens
            )
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        choice = result.choices[0]
        return LLMResponse(
            text=choice.message.content or "",
            provider=self.vendor,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=getattr(result.usage, "prompt_tokens", None),
            output_tokens=getattr(result.usage, "completion_tokens", None),
            finish_reason=choice.finish_reason,
        )

    # -------------------------------------------------------------- stream
    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        temp = settings.llm_temperature if temperature is None else temperature
        tokens = max_tokens or settings.llm_max_output_tokens

        if self.vendor == "anthropic":
            client = self._anthropic()
            sys_prompt, wire = _split_system(messages, system)
            try:
                async with client.messages.stream(
                    model=self.model,
                    max_tokens=tokens,
                    temperature=temp,
                    system=sys_prompt or "",
                    messages=wire,
                ) as stream:
                    async for piece in stream.text_stream:
                        yield piece
            except Exception as exc:
                raise self._wrap_error(exc) from exc
            return

        client = self._openai()
        wire = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m.role, "content": m.content} for m in messages
        ]
        try:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=wire,
                temperature=temp,
                max_tokens=tokens,
                stream=True,
            )
            async for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    yield delta
        except Exception as exc:
            raise self._wrap_error(exc) from exc

    # -------------------------------------------------------------- health
    async def health(self) -> tuple[bool, str]:
        key = (
            settings.anthropic_api_key
            if self.vendor == "anthropic"
            else settings.openai_api_key
        )
        if not key:
            return False, f"{self.vendor.upper()} API key is not configured."
        # Deliberately no network call: health should be cheap and free.
        return True, f"{self.vendor}:{self.model} (key present, not verified)"
