"""Transcript cleaning and utterance extraction.

Two jobs:

1. Parse `Speaker (00:12:34):` blocks into structured utterances. Keeping the
   timestamp is what lets a citation deep-link into the YouTube episode at the
   second the quote was said — the cheapest possible way to make grounding
   verifiable by a human.
2. Drop non-evidence text: sponsor reads and the standard outro. These are
   high-frequency, semantically distinctive, and would otherwise pollute
   retrieval with "this episode is brought to you by..." for growth queries.

Cleaning is conservative: when a rule is unsure, the text is kept. We never
rewrite what a guest said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SPEAKER_RE = re.compile(
    r"^(?P<speaker>[^\n(]{1,80}?)\s*\((?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\):\s*$"
)
INLINE_SPEAKER_RE = re.compile(
    r"^(?P<speaker>[^\n(]{1,80}?)\s*\((?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\):\s*(?P<text>.+)$"
)
BARE_TIMESTAMP_RE = re.compile(r"\[?\(?\b\d{1,2}:\d{2}(?::\d{2})?\b\)?\]?")

SPONSOR_PATTERNS = (
    "this episode is brought to you by",
    "today's episode is brought to you by",
    "this entire episode is brought to you by",
    "brought to you by",
)
OUTRO_PATTERNS = (
    "thank you so much for listening. if you found this valuable",
    "you can subscribe to the show on apple podcast",
    "please consider giving us a rating or leaving a review",
    "you can find all past episodes or learn more about the show",
)
PROMO_PATTERNS = (
    "subscribe at lennysnewsletter.com",
    "find the full episode here",
)


@dataclass(slots=True)
class Utterance:
    speaker: str | None
    start_seconds: int | None
    text: str


def _timestamp_to_seconds(value: str) -> int | None:
    parts = value.split(":")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    if len(numbers) == 3:
        h, m, s = numbers
    elif len(numbers) == 2:
        h, m, s = 0, numbers[0], numbers[1]
    else:
        return None
    return h * 3600 + m * 60 + s


def _is_noise(text: str) -> bool:
    lowered = text.lower().strip()
    if not lowered:
        return True
    if any(p in lowered for p in SPONSOR_PATTERNS):
        return True
    if any(p in lowered for p in OUTRO_PATTERNS):
        return True
    if any(p in lowered for p in PROMO_PATTERNS):
        return True
    return False


def parse_utterances(body: str) -> list[Utterance]:
    """Turn a transcript body into utterances, dropping headings and noise."""
    utterances: list[Utterance] = []
    pending_speaker: str | None = None
    pending_seconds: int | None = None

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):  # markdown headings ("# Title", "## Transcript")
            continue

        block = SPEAKER_RE.match(line)
        if block:
            pending_speaker = block.group("speaker").strip()
            pending_seconds = _timestamp_to_seconds(block.group("ts"))
            continue

        inline = INLINE_SPEAKER_RE.match(line)
        if inline:
            text = inline.group("text").strip()
            if not _is_noise(text):
                utterances.append(
                    Utterance(
                        speaker=inline.group("speaker").strip(),
                        start_seconds=_timestamp_to_seconds(inline.group("ts")),
                        text=text,
                    )
                )
            pending_speaker, pending_seconds = None, None
            continue

        text = line.lstrip("-• ").strip()
        if _is_noise(text):
            pending_speaker, pending_seconds = None, None
            continue
        utterances.append(
            Utterance(speaker=pending_speaker, start_seconds=pending_seconds, text=text)
        )
        pending_speaker, pending_seconds = None, None

    return utterances


def normalise_text(text: str) -> str:
    text = BARE_TIMESTAMP_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_document(body: str) -> tuple[list[Utterance], str]:
    """Return (utterances, cleaned full text)."""
    utterances = [
        Utterance(u.speaker, u.start_seconds, normalise_text(u.text))
        for u in parse_utterances(body)
    ]
    utterances = [u for u in utterances if len(u.text) > 1]
    full_text = "\n".join(
        f"{u.speaker}: {u.text}" if u.speaker else u.text for u in utterances
    )
    return utterances, full_text
