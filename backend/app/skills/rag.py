"""RAG skill — grounded question answering.

The default route. Builds context from evidence + history + memory and asks
the model for a direct answer with inline citations. If the Evidence Pack is
empty it does not call the model at all: there is nothing to ground an answer
in, and a refusal is both cheaper and more honest than a generated apology.
"""

from __future__ import annotations

import time

from app.agent.context_builder import build_context
from app.errors import LLMError
from app.observability.logging import get_logger
from app.skills.base import Skill, SkillContext, SkillResult

log = get_logger("skills.rag")

INSTRUCTIONS = """TASK: Answer the user's question from the evidence.

- Lead with the answer. No preamble.
- 150-400 words unless the user asked for more.
- Cite inline with [S#] right after each claim.
- Where guests disagree, say so and cite both.
- Use headings or bullets only when the answer genuinely has parts.
- If the evidence only partially covers the question, answer the covered part
  and name precisely what is missing."""

NO_EVIDENCE_REPLY = (
    "I couldn't find anything in the Lenny's Podcast transcripts that speaks to "
    "that. Rather than answer from general knowledge, here's what would help: "
    "try naming a specific guest, company, or framework, or ask about a core "
    "product/growth topic the show covers often — activation, retention, "
    "product-market fit, pricing, team structure, or hiring."
)


class RAGSkill(Skill):
    name = "rag"
    route = "KNOWLEDGE_Q"

    async def run(self, ctx: SkillContext) -> SkillResult:
        if ctx.evidence.is_empty:
            log.info("skill.short_circuit", skill=self.name, reason="empty_evidence")
            return SkillResult(
                text=NO_EVIDENCE_REPLY,
                metadata={"skill": self.name, "llm_called": False},
                require_evidence=False,
            )

        context = build_context(
            query=ctx.query,
            history=ctx.history,
            memories=ctx.memories,
            evidence=ctx.evidence,
            skill_instructions=INSTRUCTIONS,
        )

        started = time.perf_counter()
        log.info("llm.started", skill=self.name, provider=ctx.provider.name)
        try:
            if ctx.stream and ctx.emit is not None:
                # Stream tokens for perceived latency; the text is still
                # validated as a whole before it is persisted.
                pieces: list[str] = []
                async for piece in ctx.provider.stream(
                    context.messages, system=context.system
                ):
                    pieces.append(piece)
                    await ctx.send("token", {"text": piece})
                text = "".join(pieces)
                latency = round((time.perf_counter() - started) * 1000, 2)
                log.info(
                    "llm.completed",
                    skill=self.name,
                    provider=ctx.provider.name,
                    model=ctx.provider.model,
                    latency_ms=latency,
                    streamed=True,
                )
                return SkillResult(
                    text=text.strip(),
                    metadata={
                        "skill": self.name,
                        "llm_called": True,
                        "streamed": True,
                        "latency_ms": latency,
                        "evidence_used": context.evidence_tags,
                        "memory_keys": context.memory_keys,
                        "history_turns": context.history_turns,
                    },
                )
            response = await ctx.provider.generate(
                context.messages, system=context.system
            )
        except LLMError as exc:
            log.error(
                "llm.failed",
                skill=self.name,
                provider=ctx.provider.name,
                code=exc.code,
                error=exc.message,
            )
            raise
        latency = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "llm.completed",
            skill=self.name,
            provider=response.provider,
            model=response.model,
            latency_ms=latency,
            output_tokens=response.output_tokens,
        )

        return SkillResult(
            text=response.text.strip(),
            metadata={
                "skill": self.name,
                "llm_called": True,
                "latency_ms": latency,
                "evidence_used": context.evidence_tags,
                "memory_keys": context.memory_keys,
                "history_turns": context.history_turns,
            },
        )
