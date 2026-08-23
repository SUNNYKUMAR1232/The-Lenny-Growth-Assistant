"""Deterministic stub provider — a test double, not a language model.

Enabled only with `LLM_PROVIDER=stub`. It exists so the *whole* pipeline
(routing → retrieval → evidence → skill → grounding → persistence → artifact
sanitization) can be exercised in CI with no model server and no API key, and
so tests assert on pipeline behaviour rather than on model prose.

It writes answers by quoting the evidence block it was given, which means a
grounded answer stays grounded and an evidence-free prompt produces the
"insufficient evidence" refusal — exactly the two behaviours the tests care
about. It is never used for the demo, and the API reports it as
`stub/deterministic` so it can never be mistaken for a real model.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from app.llm.base import ChatMessage, LLMProvider, LLMResponse

# Matches exactly the block shape produced by context_builder.render_evidence:
#   [S1] Title — guest: X — url\n"""\n<text>\n"""
_EVIDENCE_RE = re.compile(
    r"^\[(S\d+)\]\s*(.+?)\n\"\"\"\n(.*?)\n\"\"\"", re.DOTALL | re.MULTILINE
)
INSUFFICIENT = (
    "I don't have transcript evidence to answer that. The retrieved excerpts "
    "from Lenny's Podcast don't cover this question, so I'd rather say so than "
    "guess."
)


class StubProvider(LLMProvider):
    name = "stub"
    model = "deterministic"

    def label(self) -> str:
        return "stub/deterministic"

    @staticmethod
    def _evidence(prompt: str) -> list[tuple[str, str, str]]:
        return [
            (tag, header.strip(), " ".join(body.split()))
            for tag, header, body in _EVIDENCE_RE.findall(prompt)
        ]

    def _compose(self, messages: list[ChatMessage], system: str | None) -> str:
        prompt = "\n\n".join(m.content for m in messages)
        full = f"{system or ''}\n\n{prompt}"
        evidence = self._evidence(full)
        question = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        ).strip()

        if not evidence:
            return INSUFFICIENT

        lines = [
            f"Here is what Lenny's guests actually said about this.",
            "",
        ]
        for tag, header, body in evidence[:3]:
            snippet = body[:320].rsplit(" ", 1)[0] if len(body) > 320 else body
            lines.append(f"- {header}: \"{snippet}\" [{tag}]")
        lines += [
            "",
            f"Taken together, the retrieved excerpts speak directly to: {question[:180]}",
        ]
        return "\n".join(lines)

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            text=self._compose(messages, system),
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            finish_reason="stop",
            meta={"stub": True},
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        for token in self._compose(messages, system).split(" "):
            yield token + " "

    async def structured_output(
        self,
        messages: list[ChatMessage],
        *,
        schema_hint: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ):
        # The stub never fabricates structure it cannot justify: callers all
        # have deterministic fallbacks, so an empty result is the honest answer.
        if "memories" in schema_hint:
            return {"memories": []}
        if "route" in schema_hint:
            return {"route": "KNOWLEDGE_Q", "confidence": 0.5}
        if "claims" in schema_hint:
            return {"claims": []}
        return {}

    async def health(self) -> tuple[bool, str]:
        return True, "deterministic stub provider (testing only)"
