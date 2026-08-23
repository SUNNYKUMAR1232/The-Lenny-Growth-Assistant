"""Context Builder — the one place where the three inputs meet.

    user query
      + conversation context   (continuity)
      + user memory            (personalization only)
      + transcript evidence    (the only source of truth about Lenny's podcast)
      -> prompt

The three are rendered in *separately labelled blocks* with explicit rules
about what each may be used for. That separation is the core safety property
of this system: if memory and evidence were concatenated into one undifferen-
tiated context, the model could not tell "the user told me they work at Stripe"
apart from "a guest said Stripe does X", and neither could the grounding
validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import Memory, Message
from app.llm.base import ChatMessage
from app.schemas.contracts import EvidencePack

MAX_HISTORY_TURNS = 10
MAX_HISTORY_CHARS = 6000

BASE_RULES = """You are the Lenny Growth Assistant. You answer product-management and
growth questions for product teams using ONLY the transcript evidence provided.

Rules you must follow:
1. Ground every factual claim about Lenny's Podcast in the EVIDENCE block. Cite the
   source tag inline, like [S1] or [S2], immediately after the claim it supports.
2. Never invent quotes, guests, episodes, statistics, or frameworks. If the evidence
   does not cover the question, say so plainly and state what IS covered.
3. The USER CONTEXT block is personalization only. It tells you who you are talking to
   and how they like to be answered. It is NEVER evidence and must never be cited.
4. Prefer the guests' own words and concrete specifics over generic advice.
5. Be direct and useful. No filler, no throat-clearing, no restating the question."""

INSUFFICIENT_EVIDENCE_RULE = """
There is NO transcript evidence for this question. Do not answer from general
knowledge. Say clearly that the transcripts retrieved do not cover it, and suggest
one or two closely related questions the corpus is likely to cover."""


@dataclass(slots=True)
class BuiltContext:
    system: str
    messages: list[ChatMessage]
    evidence_tags: list[str] = field(default_factory=list)
    memory_keys: list[str] = field(default_factory=list)
    history_turns: int = 0


def render_evidence(pack: EvidencePack) -> str:
    if pack.is_empty:
        return "EVIDENCE (from Lenny's Podcast transcripts):\n(none retrieved)"
    blocks = ["EVIDENCE (from Lenny's Podcast transcripts):"]
    for item in pack.evidence:
        header = f"[{item.source_id}] {item.title}"
        if item.guest:
            header += f" — guest: {item.guest}"
        if item.source_url:
            header += f" — {item.source_url}"
        blocks.append(f'{header}\n"""\n{item.text}\n"""')
    return "\n\n".join(blocks)


def render_memories(memories: list[Memory]) -> str:
    if not memories:
        return ""
    lines = [
        "USER CONTEXT (personalization only — NOT evidence, never cite this):",
    ]
    for memory in memories:
        lines.append(
            f"- {memory.key}: {memory.value} "
            f"(confidence {memory.confidence:.2f}, {memory.type})"
        )
    return "\n".join(lines)


def render_history(messages: list[Message]) -> list[ChatMessage]:
    recent = [m for m in messages if m.role in {"user", "assistant"}][-MAX_HISTORY_TURNS:]
    budget = MAX_HISTORY_CHARS
    trimmed: list[ChatMessage] = []
    for message in reversed(recent):
        content = message.content
        if len(content) > budget:
            content = content[: max(0, budget)] + " …"
        if not content:
            break
        trimmed.append(ChatMessage(role=message.role, content=content))
        budget -= len(content)
        if budget <= 0:
            break
    return list(reversed(trimmed))


def build_context(
    *,
    query: str,
    history: list[Message],
    memories: list[Memory],
    evidence: EvidencePack,
    skill_instructions: str = "",
) -> BuiltContext:
    sections = [BASE_RULES]
    if skill_instructions:
        sections.append(skill_instructions.strip())

    memory_block = render_memories(memories)
    if memory_block:
        sections.append(memory_block)

    sections.append(render_evidence(evidence))
    if evidence.is_empty:
        sections.append(INSUFFICIENT_EVIDENCE_RULE.strip())
    if evidence.degraded and evidence.degraded_reason:
        sections.append(
            "RETRIEVAL NOTE (for your awareness, do not mention verbatim): "
            f"{evidence.degraded_reason}"
        )

    messages = render_history(history)
    messages.append(ChatMessage(role="user", content=query))

    return BuiltContext(
        system="\n\n".join(sections),
        messages=messages,
        evidence_tags=[item.source_id for item in evidence.evidence],
        memory_keys=[memory.key for memory in memories],
        history_turns=len(messages) - 1,
    )
