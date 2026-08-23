"""Grounding validator — a practical safeguard, not a proof of truth.

Runs after generation, before the answer is persisted or streamed to the user.

What it does:
  1. Splits the answer into claim-sized units (sentences, minus questions,
     headings, and meta-statements about the assistant itself).
  2. Scores each claim against the Evidence Pack with a lexical overlap
     measure: rare-word containment + bigram overlap. A claim that cites [S2]
     is scored against S2 first, then against the whole pack.
  3. Removes citations that point at sources that were never retrieved
     (a hallucinated citation is worse than no citation).
  4. Decides an action:
       accepted  — enough claims are supported
       annotated — support is thin; the answer is kept but flagged in the UI
       refused   — the model answered despite an empty Evidence Pack; the
                   answer is replaced with an explicit "not covered" reply

What it explicitly does NOT do: verify factual truth, resolve paraphrase, or
catch a well-worded claim that reuses evidence vocabulary. It is a cheap,
deterministic net for the common failure — the model drifting off-corpus into
its own priors — and it adds no model call and ~1ms of latency. An NLI or
LLM-judge pass would catch more and is the documented upgrade path
(docs/architecture.md#grounding).
"""

from __future__ import annotations

import math
import re
from collections import Counter

from app.config import settings
from app.observability.logging import get_logger
from app.schemas.contracts import EvidencePack, GroundingClaim, GroundingReport

log = get_logger("grounding")

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_WORD_RE = re.compile(r"[a-z0-9']+")
_CITATION_RE = re.compile(r"\[(S\d+)\]")

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "for", "with", "as", "by", "at", "from", "it", "its",
    "you", "your", "they", "their", "we", "our", "i", "he", "she", "his", "her",
    "not", "no", "so", "do", "does", "did", "can", "will", "would", "should",
    "about", "into", "more", "most", "some", "any", "one", "two", "also", "how",
    "what", "when", "why", "who", "which", "there", "here", "up", "out", "just",
    "like", "get", "got", "make", "makes", "made", "have", "has", "had", "when",
}

META_PREFIXES = (
    "i don't have", "i do not have", "i can't", "i cannot", "the transcripts",
    "based on the retrieved", "the evidence", "here is", "here's", "note:",
    "sources:", "in short", "to summarise", "to summarize", "let me know",
)

REFUSAL_TEXT = (
    "I don't have transcript evidence for that. The excerpts retrieved from "
    "Lenny's Podcast don't cover this question, so I'd rather tell you that "
    "than answer from general knowledge.\n\n"
    "Try narrowing to something the corpus covers — for example a specific "
    "guest, company, or framework discussed on the show."
)


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in STOPWORDS and len(w) > 2]


def split_claims(answer: str) -> list[str]:
    claims: list[str] = []
    for block in answer.split("\n"):
        line = block.strip().lstrip("-*•0123456789. ").strip()
        if not line or line.startswith("#") or line.startswith("|"):
            continue
        if line.startswith("```"):
            continue
        for sentence in _SENTENCE_RE.split(line):
            sentence = sentence.strip()
            if len(sentence) < 25 or sentence.endswith("?"):
                continue
            lowered = sentence.lower()
            if any(lowered.startswith(prefix) for prefix in META_PREFIXES):
                continue
            claims.append(sentence)
    return claims


class _EvidenceIndex:
    """Rare-word weighted index over the evidence pack."""

    def __init__(self, pack: EvidencePack) -> None:
        self.by_tag: dict[str, list[str]] = {}
        self.bigrams: dict[str, set[str]] = {}
        document_frequency: Counter[str] = Counter()
        for item in pack.evidence:
            tokens = _tokens(item.text)
            self.by_tag[item.source_id] = tokens
            self.bigrams[item.source_id] = {
                f"{a} {b}" for a, b in zip(tokens, tokens[1:])
            }
            document_frequency.update(set(tokens))
        total = max(1, len(pack.evidence))
        self.idf = {
            token: math.log(1 + total / (1 + count))
            for token, count in document_frequency.items()
        }
        self.all_tokens = set(document_frequency)

    def score(self, claim: str, prefer_tag: str | None = None) -> tuple[float, str | None]:
        claim_tokens = _tokens(claim)
        if not claim_tokens:
            return 1.0, None  # nothing checkable in this sentence
        claim_bigrams = {f"{a} {b}" for a, b in zip(claim_tokens, claim_tokens[1:])}

        weights = {t: self.idf.get(t, 0.4) for t in set(claim_tokens)}
        total_weight = sum(weights.values()) or 1.0

        best_score = 0.0
        best_tag: str | None = None
        tags = list(self.by_tag)
        if prefer_tag and prefer_tag in self.by_tag:
            tags = [prefer_tag] + [t for t in tags if t != prefer_tag]

        for tag in tags:
            source_tokens = set(self.by_tag[tag])
            overlap = sum(w for t, w in weights.items() if t in source_tokens)
            lexical = overlap / total_weight
            bigram_bonus = 0.0
            if claim_bigrams:
                shared = len(claim_bigrams & self.bigrams[tag]) / len(claim_bigrams)
                bigram_bonus = 0.25 * shared
            score = min(1.0, lexical + bigram_bonus)
            if prefer_tag == tag:
                score = min(1.0, score + 0.05)
            if score > best_score:
                best_score, best_tag = score, tag
        return best_score, best_tag


def strip_invalid_citations(answer: str, valid_tags: set[str]) -> tuple[str, int]:
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        if match.group(1) in valid_tags:
            return match.group(0)
        removed += 1
        return ""

    cleaned = _CITATION_RE.sub(replace, answer)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned, removed


def validate(
    answer: str, pack: EvidencePack, *, require_evidence: bool = True
) -> tuple[str, GroundingReport]:
    if not settings.grounding_enabled:
        return answer, GroundingReport(enabled=False, action="skipped")

    valid_tags = {item.source_id for item in pack.evidence}
    answer, removed = strip_invalid_citations(answer, valid_tags)

    claims = split_claims(answer)

    if pack.is_empty:
        if require_evidence and claims:
            log.info(
                "grounding.completed",
                action="refused",
                checked_claims=len(claims),
                supported_claims=0,
                reason="empty_evidence",
            )
            return REFUSAL_TEXT, GroundingReport(
                checked_claims=len(claims),
                supported_claims=0,
                supported_ratio=0.0,
                revised=True,
                action="refused",
            )
        return answer, GroundingReport(action="accepted", checked_claims=0)

    index = _EvidenceIndex(pack)
    results: list[GroundingClaim] = []
    for claim in claims:
        cited = _CITATION_RE.search(claim)
        score, tag = index.score(claim, prefer_tag=cited.group(1) if cited else None)
        results.append(
            GroundingClaim(
                claim=claim[:400],
                supported=score >= settings.grounding_min_support,
                support_score=round(score, 4),
                best_source_id=tag,
            )
        )

    checked = len(results)
    supported = sum(1 for r in results if r.supported)
    ratio = (supported / checked) if checked else 1.0

    action: str = "accepted"
    revised = removed > 0
    if checked and ratio < settings.grounding_min_supported_ratio:
        action = "annotated"
        revised = True
        answer = (
            f"{answer}\n\n> **Grounding note:** only {supported} of {checked} claims in "
            "this answer closely match the retrieved transcript excerpts. Treat the "
            "unmatched parts as interpretation, not as something a guest said."
        )

    log.info(
        "grounding.completed",
        action=action,
        checked_claims=checked,
        supported_claims=supported,
        supported_ratio=round(ratio, 3),
        citations_removed=removed,
    )

    return answer, GroundingReport(
        checked_claims=checked,
        supported_claims=supported,
        supported_ratio=round(ratio, 4),
        revised=revised,
        action=action,  # type: ignore[arg-type]
        claims=results[:25],
    )
