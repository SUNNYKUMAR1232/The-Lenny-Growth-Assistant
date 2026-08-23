"""Fusion and reranking.

Deliberately *not* a cross-encoder. A second neural model would add a second
model server, hundreds of milliseconds, and a second thing to fail — for a
corpus this size the ranking wins come from fusing two complementary signals
and enforcing diversity, not from a heavier scorer.

Ranking = weighted Reciprocal Rank Fusion + two cheap lexical priors:

    score = w_v * RRF(vector_rank) + w_k * RRF(keyword_rank)
            + 0.15 if the query names this episode's guest
            + 0.05 if query terms appear in the episode title

RRF (1/(k+rank), k=60) is used instead of blending raw scores because cosine
similarity and ts_rank_cd are not on comparable scales; ranks are.

Weights default to 0.6 vector / 0.4 keyword (RETRIEVAL_VECTOR_WEIGHT,
RETRIEVAL_KEYWORD_WEIGHT). Semantic search leads because most questions are
conceptual paraphrases; keyword is weighted heavily enough to rescue proper
nouns. Both are env-tunable, and the split is revisited in
docs/architecture.md#retrieval-weights.
"""

from __future__ import annotations

import re

from app.config import settings
from app.retrieval.types import Candidate

_WORD_RE = re.compile(r"[a-z0-9']+")


def _terms(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2}


def _rrf(rank: int | None, k: int) -> float:
    return 0.0 if rank is None else 1.0 / (k + rank)


def merge_candidates(
    vector_hits: list[Candidate], keyword_hits: list[Candidate]
) -> list[Candidate]:
    """Union the two result sets, keeping both scores per chunk."""
    merged: dict[str, Candidate] = {}
    for candidate in vector_hits:
        merged[candidate.chunk_id] = candidate
    for candidate in keyword_hits:
        existing = merged.get(candidate.chunk_id)
        if existing is None:
            merged[candidate.chunk_id] = candidate
        else:
            existing.keyword_score = candidate.keyword_score
            existing.keyword_rank = candidate.keyword_rank
            existing.retrieval = "hybrid"
    return list(merged.values())


def rerank(
    query: str,
    candidates: list[Candidate],
    top_k: int | None = None,
    max_per_document: int = 3,
) -> list[Candidate]:
    top_k = top_k or settings.retrieval_top_k
    query_terms = _terms(query)
    k = settings.retrieval_rrf_k

    for candidate in candidates:
        score = settings.retrieval_vector_weight * _rrf(candidate.vector_rank, k)
        score += settings.retrieval_keyword_weight * _rrf(candidate.keyword_rank, k)

        if candidate.guest and _terms(candidate.guest) & query_terms:
            score += 0.15
        title_overlap = _terms(candidate.title or "") & query_terms
        if title_overlap:
            score += 0.05

        candidate.fused_score = round(score, 6)

    ranked = sorted(candidates, key=lambda c: c.fused_score, reverse=True)

    # Diversity: one episode should not monopolise the evidence pack, or a
    # comparative question ("how do X and Y differ") gets a one-sided answer.
    selected: list[Candidate] = []
    per_document: dict[str, int] = {}
    for candidate in ranked:
        used = per_document.get(candidate.document_id, 0)
        if used >= max_per_document:
            continue
        per_document[candidate.document_id] = used + 1
        selected.append(candidate)
        if len(selected) >= top_k:
            break

    # If diversity starved the pack, top it up with the best remaining chunks.
    if len(selected) < top_k:
        chosen = {c.chunk_id for c in selected}
        for candidate in ranked:
            if candidate.chunk_id in chosen:
                continue
            selected.append(candidate)
            if len(selected) >= top_k:
                break

    return selected
