"""Model gateway interface.

Application code never imports a provider directly — it asks
`app.llm.factory.get_provider()` for something that satisfies this interface.
Switching from a local Ollama model to Claude is therefore an environment
change, not a code change.
"""

from __future__ import annotations

import abc
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class LLMProvider(abc.ABC):
    name: str = "base"
    model: str = "unknown"

    @abc.abstractmethod
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    @abc.abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]: ...

    async def structured_output(
        self,
        messages: list[ChatMessage],
        *,
        schema_hint: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Any:
        """Ask for JSON and parse it defensively.

        Small local models routinely wrap JSON in prose or fences, so the
        default implementation extracts the first balanced JSON value rather
        than trusting the model to obey formatting instructions.
        """
        instruction = (
            f"{system + chr(10) + chr(10) if system else ''}"
            "Respond with a single JSON value and nothing else. No prose, no code fences.\n"
            f"Required shape:\n{schema_hint}"
        )
        response = await self.generate(
            messages,
            system=instruction,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return extract_json(response.text)

    async def health(self) -> tuple[bool, str]:
        try:
            await self.generate(
                [ChatMessage(role="user", content="Reply with the single word: ok")],
                max_tokens=8,
                temperature=0.0,
            )
        except Exception as exc:
            return False, str(exc)[:200]
        return True, "ok"

    def label(self) -> str:
        return f"{self.name}/{self.model}"


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a chatty completion."""
    if text is None:
        raise ValueError("empty completion")
    candidate = text.strip()
    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(candidate)):
            ch = candidate[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : idx + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("no JSON value found in completion")
