"""Local model provider backed by Ollama's HTTP API.

This is the provider the submitted demo runs on. It targets `/api/chat`, which
exists in every currently supported Ollama release and supports streaming.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx

from app.config import settings
from app.errors import LLMError, LLMTimeoutError
from app.llm.base import ChatMessage, LLMProvider, LLMResponse
from app.observability.logging import get_logger

log = get_logger("llm.ollama")


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.llm_timeout_seconds
        self._client = client

    # ------------------------------------------------------------------ util
    def _payload(
        self,
        messages: list[ChatMessage],
        system: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict:
        wire: list[dict] = []
        if system:
            wire.append({"role": "system", "content": system})
        wire.extend({"role": m.role, "content": m.content} for m in messages)
        return {
            "model": self.model,
            "messages": wire,
            "stream": stream,
            "options": {
                "temperature": (
                    settings.llm_temperature if temperature is None else temperature
                ),
                "num_predict": max_tokens or settings.llm_max_output_tokens,
            },
        }

    def _wrap_error(self, exc: Exception) -> LLMError:
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError(
                f"The local model `{self.model}` did not respond in "
                f"{self.timeout:.0f}s.",
                details={"provider": "ollama", "model": self.model},
            )
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 404:
                return LLMError(
                    f"Ollama does not have `{self.model}` pulled. "
                    f"Run: ollama pull {self.model}",
                    details={"provider": "ollama", "model": self.model},
                )
            return LLMError(
                f"Ollama returned HTTP {status}.",
                details={"provider": "ollama", "model": self.model},
            )
        return LLMError(
            f"Could not reach Ollama at {self.base_url}. Is `ollama serve` running?",
            details={"provider": "ollama", "base_url": self.base_url},
        )

    # -------------------------------------------------------------- generate
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = self._payload(messages, system, temperature, max_tokens, stream=False)
        started = time.perf_counter()
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        finally:
            if self._client is None:
                await client.aclose()

        text = (data.get("message") or {}).get("content", "")
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            finish_reason=data.get("done_reason"),
        )

    # ---------------------------------------------------------------- stream
    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, system, temperature, max_tokens, stream=True)
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = (event.get("message") or {}).get("content")
                    if piece:
                        yield piece
                    if event.get("done"):
                        break
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        finally:
            if self._client is None:
                await client.aclose()

    # ---------------------------------------------------------------- health
    async def health(self) -> tuple[bool, str]:
        client = self._client or httpx.AsyncClient(timeout=5.0)
        try:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            tags = response.json().get("models", [])
            names = {t.get("name", "") for t in tags}
            base_names = {n.split(":")[0] for n in names}
            if self.model in names or self.model.split(":")[0] in base_names:
                return True, f"ollama:{self.model}"
            return False, (
                f"Ollama is running but `{self.model}` is not pulled "
                f"(ollama pull {self.model})."
            )
        except Exception as exc:
            return False, self._wrap_error(exc).message
        finally:
            if self._client is None:
                await client.aclose()
