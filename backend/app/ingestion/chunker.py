"""Chunking strategy.

Podcast transcripts are conversational: an idea is usually one long answer,
and cutting mid-answer destroys the thing you want to retrieve. So we chunk on
*utterance boundaries*, packing whole turns until the chunk reaches a target
size, with a small overlap of trailing turns to preserve continuity across the
seam.

Defaults: ~350 tokens per chunk, ~60 tokens overlap (CHUNK_TARGET_TOKENS /
CHUNK_OVERLAP_TOKENS). Rationale in docs/architecture.md#chunking:
350 tokens is long enough to carry a complete claim plus its qualifier, short
enough that 8 chunks fit comfortably in an 8B local model's context alongside
the system prompt and conversation history.

Token counts are estimated (words / 0.75) rather than tokenised: the estimate
is within ~10% for English speech and costs no dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ingestion.cleaner import Utterance


def estimate_tokens(text: str) -> int:
    words = len(text.split())
    return int(words / 0.75) + 1 if words else 0


@dataclass(slots=True)
class ChunkDraft:
    index: int
    text: str
    token_estimate: int
    start_seconds: int | None = None
    end_seconds: int | None = None
    speakers: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "speakers": self.speakers,
        }


def _speakers(units: list[Utterance]) -> list[str]:
    seen: list[str] = []
    for unit in units:
        if unit.speaker and unit.speaker not in seen:
            seen.append(unit.speaker)
    return seen


def _render(units: list[Utterance]) -> str:
    lines: list[str] = []
    last_speaker: str | None = None
    for unit in units:
        if unit.speaker and unit.speaker != last_speaker:
            lines.append(f"{unit.speaker}: {unit.text}")
            last_speaker = unit.speaker
        else:
            lines.append(unit.text)
    return "\n".join(lines).strip()


def _emit(units: list[Utterance], index: int) -> ChunkDraft:
    text = _render(units)
    starts = [u.start_seconds for u in units if u.start_seconds is not None]
    return ChunkDraft(
        index=index,
        text=text,
        token_estimate=estimate_tokens(text),
        start_seconds=starts[0] if starts else None,
        end_seconds=starts[-1] if starts else None,
        speakers=_speakers(units),
    )


def chunk_utterances(
    utterances: list[Utterance],
    target_tokens: int = 350,
    overlap_tokens: int = 60,
    max_chars: int = 4000,
) -> list[ChunkDraft]:
    if not utterances:
        return []

    chunks: list[ChunkDraft] = []
    buffer: list[Utterance] = []
    buffer_tokens = 0

    for unit in utterances:
        unit_tokens = estimate_tokens(unit.text)

        # A single very long turn becomes its own chunk, split on sentences.
        if unit_tokens > target_tokens * 2:
            if buffer:
                chunks.append(_emit(buffer, len(chunks)))
                buffer, buffer_tokens = [], 0
            for piece in _split_long(unit, target_tokens, max_chars):
                chunks.append(_emit([piece], len(chunks)))
            continue

        buffer.append(unit)
        buffer_tokens += unit_tokens

        if buffer_tokens >= target_tokens:
            chunks.append(_emit(buffer, len(chunks)))
            buffer, buffer_tokens = _carry_over(buffer, overlap_tokens)

    if buffer and estimate_tokens(_render(buffer)) > 20:
        chunks.append(_emit(buffer, len(chunks)))

    return chunks


def _carry_over(buffer: list[Utterance], overlap_tokens: int) -> tuple[list[Utterance], int]:
    """Keep trailing turns as the next chunk's lead-in."""
    if overlap_tokens <= 0:
        return [], 0
    carried: list[Utterance] = []
    total = 0
    for unit in reversed(buffer):
        unit_tokens = estimate_tokens(unit.text)
        if total + unit_tokens > overlap_tokens and carried:
            break
        carried.insert(0, unit)
        total += unit_tokens
        if total >= overlap_tokens:
            break
    return carried, total


def _split_long(unit: Utterance, target_tokens: int, max_chars: int) -> list[Utterance]:
    sentences = [s.strip() for s in unit.text.replace("? ", "?|").replace(". ", ".|").split("|") if s.strip()]
    pieces: list[Utterance] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        sentence_tokens = estimate_tokens(sentence)
        if current and (
            current_tokens + sentence_tokens > target_tokens
            or sum(len(s) for s in current) > max_chars
        ):
            pieces.append(Utterance(unit.speaker, unit.start_seconds, " ".join(current)))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        pieces.append(Utterance(unit.speaker, unit.start_seconds, " ".join(current)))
    return pieces or [unit]
