"""Ship 30 for 30 essay skill.

The writing standard is `skills/ship30/SKILL.md`, loaded from disk — not a
prompt buried in this module. This class handles orchestration only:

  * ask for the essay with the skill file as the system standard
  * measure the result against the file's own `target_words`
  * run ONE bounded expansion pass if the draft came back short (small local
    models routinely under-write on long-form requests)
  * return the essay as both a chat message and a Markdown artifact, so it
    lands in the Artifact Viewer where it can be read and copied

The expansion pass is capped at one attempt: a retry loop that chases a word
count would trade unbounded latency for a cosmetic metric.
"""

from __future__ import annotations

import time

from app.agent.context_builder import build_context
from app.errors import LLMError
from app.llm.base import ChatMessage
from app.observability.logging import get_logger
from app.skills.base import Skill, SkillContext, SkillResult, load_skill_file
from app.skills.rag import EMPTY_CORPUS_REPLY

log = get_logger("skills.ship30")

NO_EVIDENCE_REPLY = (
    "I can't write this essay yet — the transcripts I retrieved don't contain "
    "enough material on that topic, and a Ship 30 essay built on invented "
    "claims would be worse than none. Give me a topic the podcast covers "
    "(activation, retention, PMF, pricing, growth teams, hiring) and I'll draft it."
)


def word_count(text: str) -> int:
    return len(text.split())


class Ship30Skill(Skill):
    name = "ship30"
    route = "SHIP30"

    async def run(self, ctx: SkillContext) -> SkillResult:
        skill = load_skill_file("ship30")
        target = skill.target_words or 1250
        tolerance = int(skill.metadata.get("tolerance_words", 150))

        if ctx.evidence.is_empty:
            log.info("skill.short_circuit", skill=self.name, reason="empty_evidence")
            return SkillResult(
                text=(
                    EMPTY_CORPUS_REPLY
                    if ctx.evidence.corpus_empty
                    else NO_EVIDENCE_REPLY
                ),
                metadata={
                    "skill": self.name,
                    "llm_called": False,
                    "corpus_empty": ctx.evidence.corpus_empty,
                },
                require_evidence=False,
            )

        instructions = (
            f"TASK: Write a Ship 30 for 30-style essay of about {target} words.\n"
            "Follow the writing standard below exactly. Return ONLY the essay in "
            "Markdown — no preamble, no meta-commentary about the task.\n\n"
            f"--- WRITING STANDARD ({skill.name} v{skill.metadata.get('version', '1')}) ---\n"
            f"{skill.body}"
        )

        context = build_context(
            query=ctx.query,
            history=ctx.history,
            memories=ctx.memories,
            evidence=ctx.evidence,
            skill_instructions=instructions,
        )

        started = time.perf_counter()
        log.info("llm.started", skill=self.name, provider=ctx.provider.name, target_words=target)
        try:
            response = await ctx.provider.generate(
                context.messages,
                system=context.system,
                temperature=0.6,
                max_tokens=max(2600, int(target * 2.2)),
            )
        except LLMError as exc:
            log.error("llm.failed", skill=self.name, code=exc.code, error=exc.message)
            raise

        essay = response.text.strip()
        words = word_count(essay)
        expanded = False

        if words < target - tolerance:
            log.info("ship30.expansion_pass", words=words, target=target)
            try:
                followup = await ctx.provider.generate(
                    context.messages
                    + [
                        ChatMessage(role="assistant", content=essay),
                        ChatMessage(
                            role="user",
                            content=(
                                f"That draft is {words} words; the standard is ~{target}. "
                                "Expand it to length by deepening the existing sections with "
                                "more specifics from the evidence — a concrete example, a "
                                "step-by-step, the objection and its answer. Do not add new "
                                "claims that the evidence does not support, and do not pad. "
                                "Return the complete revised essay only."
                            ),
                        ),
                    ],
                    system=context.system,
                    temperature=0.6,
                    max_tokens=max(3000, int(target * 2.4)),
                )
                if word_count(followup.text) > words:
                    essay = followup.text.strip()
                    words = word_count(essay)
                    expanded = True
            except LLMError as exc:
                # Keep the short draft rather than failing the request.
                log.warning("ship30.expansion_failed", error=exc.message)

        latency = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "llm.completed",
            skill=self.name,
            provider=response.provider,
            model=response.model,
            latency_ms=latency,
            words=words,
            expanded=expanded,
        )

        title = _extract_title(essay) or "Ship 30 essay"
        return SkillResult(
            text=essay,
            artifact={"type": "markdown", "title": title, "content": essay},
            metadata={
                "skill": self.name,
                "llm_called": True,
                "latency_ms": latency,
                "word_count": words,
                "target_words": target,
                "within_tolerance": abs(words - target) <= tolerance,
                "expansion_pass": expanded,
                "evidence_used": context.evidence_tags,
            },
        )


def _extract_title(essay: str) -> str | None:
    for line in essay.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip()[:200]
        if stripped:
            return stripped[:200]
    return None
