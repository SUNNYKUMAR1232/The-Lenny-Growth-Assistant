"""Memory extraction.

We do not store every message. Storing everything produces a junk drawer that
retrieval cannot rank and a user cannot audit. Instead a small extraction pass
proposes *candidate* memories, each with a confidence and an importance, and
only candidates above both thresholds are persisted
(MEMORY_MIN_CONFIDENCE / MEMORY_MIN_IMPORTANCE).

Two extractors, in order:

1. LLM extraction via `structured_output` — catches paraphrased, implicit
   preferences ("keep it short" -> preferred_response_length).
2. Deterministic patterns — cheap, exact, and the only thing that runs when
   the model is unavailable. Extraction failure must never break the chat.

Everything here is *personalization*. None of it is ever cited as evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.llm.base import ChatMessage, LLMProvider
from app.observability.logging import get_logger

log = get_logger("memory.extractor")

SCHEMA_HINT = """{
  "memories": [
    {"type": "semantic|episodic", "key": "snake_case_key", "value": "short factual statement",
     "confidence": 0.0-1.0, "importance": 0.0-1.0}
  ]
}"""

SYSTEM = """You extract durable personalization facts about a user from a conversation.

Extract ONLY:
- semantic: stable preferences, role, company stage, domain, writing-style preferences
- episodic: a decision the user made or a concrete goal they stated in this conversation

Never extract:
- anything about Lenny's Podcast content or its guests (that is knowledge, not user memory)
- one-off questions, transient context, or anything you inferred without evidence
- sensitive personal data (health, finance, credentials, precise location)

Be conservative. Zero memories is a valid, common answer. Confidence is how sure
you are the fact is true; importance is how much it should shape future replies."""


@dataclass(slots=True)
class MemoryCandidate:
    type: str
    key: str
    value: str
    confidence: float
    importance: float
    source: str = "llm"


_PATTERNS: tuple[tuple[str, str, float, float], ...] = (
    (r"\bi(?:'m| am) a[n]? ([a-z ]{3,40}?)(?:\.|,| at | in | working|$)", "role", 0.85, 0.75),
    (r"\bi work (?:at|for) ([A-Za-z0-9&.\- ]{2,40})", "employer", 0.8, 0.6),
    (r"\bwe(?:'re| are) (?:a|an) ([a-z\- ]{3,40}?)(?: startup| company)", "company_type", 0.75, 0.6),
    (r"\bwe(?:'re| are) (?:pre-seed|seed|series [a-d]|bootstrapped)", "company_stage", 0.8, 0.7),
    (r"\bi prefer ([a-z ,\-]{3,60})", "preference", 0.8, 0.7),
    (r"\b(?:keep it|make it|be) (concise|brief|short|detailed|thorough)\b", "preferred_response_length", 0.75, 0.65),
    (r"\bi(?:'m| am) (?:building|working on) ([a-z0-9 \-]{3,50})", "current_project", 0.8, 0.75),
    (r"\bmy (?:team|company) (?:is|has) ([a-z0-9 \-]{3,50})", "team_context", 0.7, 0.55),
)


def heuristic_extract(user_turns: list[str]) -> list[MemoryCandidate]:
    found: dict[str, MemoryCandidate] = {}
    for turn in user_turns:
        lowered = turn.lower()
        for pattern, key, confidence, importance in _PATTERNS:
            match = re.search(pattern, lowered)
            if not match:
                continue
            value = (match.group(1) if match.groups() else match.group(0)).strip(" .,")
            if not value or len(value) > 120:
                continue
            found[key] = MemoryCandidate(
                type="semantic",
                key=key,
                value=value,
                confidence=confidence,
                importance=importance,
                source="heuristic",
            )
    return list(found.values())


def _coerce(raw: dict) -> MemoryCandidate | None:
    key = str(raw.get("key", "")).strip().lower().replace(" ", "_")[:255]
    value = str(raw.get("value", "")).strip()[:2000]
    if not key or not value:
        return None
    mtype = str(raw.get("type", "semantic")).lower()
    if mtype not in {"semantic", "episodic"}:
        mtype = "semantic"
    try:
        confidence = float(raw.get("confidence", 0.5))
        importance = float(raw.get("importance", 0.5))
    except (TypeError, ValueError):
        return None
    return MemoryCandidate(
        type=mtype,
        key=key,
        value=value,
        confidence=min(max(confidence, 0.0), 1.0),
        importance=min(max(importance, 0.0), 1.0),
    )


async def extract_memories(
    provider: LLMProvider, conversation: list[tuple[str, str]]
) -> list[MemoryCandidate]:
    """`conversation` is [(role, content), ...] for the recent window."""
    user_turns = [content for role, content in conversation if role == "user"]
    if not user_turns:
        return []

    transcript = "\n".join(f"{role}: {content}" for role, content in conversation[-8:])
    candidates: list[MemoryCandidate] = []
    try:
        payload = await provider.structured_output(
            [ChatMessage(role="user", content=transcript)],
            schema_hint=SCHEMA_HINT,
            system=SYSTEM,
            temperature=0.0,
            max_tokens=600,
        )
        raw_items = payload.get("memories", []) if isinstance(payload, dict) else []
        for raw in raw_items[:8]:
            if isinstance(raw, dict):
                candidate = _coerce(raw)
                if candidate:
                    candidates.append(candidate)
    except Exception as exc:
        # Extraction is best-effort by design: never fail a chat over memory.
        log.warning("memory.extraction_failed", error=str(exc)[:200])

    known = {c.key for c in candidates}
    candidates.extend(c for c in heuristic_extract(user_turns) if c.key not in known)
    return candidates
