"""The retrieval engine and the Evidence Pack.

The Evidence Pack is the *only* channel through which transcript content
reaches the model. Skills never query the database themselves. That single
constraint is what makes grounding checkable: whatever the model says, we can
compare it against exactly what we handed it.

Two strategies:

  chunk    — default. Hybrid search, fuse, rerank, take top-k chunks.
  episode  — for comparative / "across episodes" / "what do people say about"
             questions. First find which *episodes* are relevant, then pull a
             contiguous window of chunks from each so the model reasons over
             a coherent stretch of conversation rather than eight fragments.

Degradation is explicit: if embeddings are unavailable the vector leg is
skipped, the pack is marked `degraded`, and the UI says "keyword-only".
"""

from __future__ import annotations

import re
import time

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Chunk, Document
from app.embeddings.factory import embed_with_fallback
from app.errors import EmbeddingError, RetrievalError
from app.observability.logging import get_logger
from app.retrieval.keyword import keyword_search
from app.retrieval.reranker import merge_candidates, rerank
from app.retrieval.types import Candidate
from app.retrieval.vector import vector_search
from app.schemas.contracts import EvidenceItem, EvidencePack

log = get_logger("retrieval")

COMPARATIVE_PATTERNS = re.compile(
    r"\b(compare|comparison|versus|vs\.?|differ|difference|contrast|"
    r"across (?:episodes|guests|the podcast)|different (?:guests|people|approaches)|"
    r"who (?:else|all)|multiple|several (?:guests|founders)|both)\b",
    re.IGNORECASE,
)
SYNTHESIS_PATTERNS = re.compile(
    r"\b(what do .{0,30}(say|think)|common (themes|patterns|advice)|"
    r"consensus|overall|in general|summar(y|ise|ize)|playbook|framework for)\b",
    re.IGNORECASE,
)


def choose_strategy(query: str, route: str | None = None) -> str:
    if route == "SHIP30":
        # Essays need room to breathe; episode windows give narrative material.
        return "episode"
    if COMPARATIVE_PATTERNS.search(query) or SYNTHESIS_PATTERNS.search(query):
        return "episode"
    return "chunk"


def _to_evidence_item(candidate: Candidate, tag_index: int) -> EvidenceItem:
    text = candidate.content
    if len(text) > settings.retrieval_max_chars_per_chunk:
        text = text[: settings.retrieval_max_chars_per_chunk].rsplit(" ", 1)[0] + " …"
    return EvidenceItem(
        source_id=f"S{tag_index}",
        chunk_id=candidate.chunk_id,
        title=candidate.title,
        guest=candidate.guest,
        source_url=candidate.citation_url,
        chunk_index=candidate.chunk_index,
        text=text,
        score=round(candidate.fused_score, 6),
        vector_score=(
            round(candidate.vector_score, 4) if candidate.vector_score is not None else None
        ),
        keyword_score=(
            round(candidate.keyword_score, 4) if candidate.keyword_score is not None else None
        ),
        retrieval=candidate.retrieval,  # type: ignore[arg-type]
    )


async def _expand_episodes(
    session: AsyncSession, seeds: list[Candidate]
) -> list[Candidate]:
    """Pull a contiguous window of chunks around the best hit per episode."""
    by_document: dict[str, Candidate] = {}
    for candidate in seeds:
        current = by_document.get(candidate.document_id)
        if current is None or candidate.fused_score > current.fused_score:
            by_document[candidate.document_id] = candidate

    top_documents = sorted(
        by_document.values(), key=lambda c: c.fused_score, reverse=True
    )[: settings.episode_context_max_episodes]

    window = max(1, settings.episode_context_max_chunks // 2)
    expanded: list[Candidate] = []
    for seed in top_documents:
        low = max(0, seed.chunk_index - window)
        high = seed.chunk_index + window
        rows = (
            await session.execute(
                select(
                    Chunk.id,
                    Chunk.document_id,
                    Chunk.chunk_index,
                    Chunk.content,
                    Chunk.meta,
                    Document.title,
                    Document.guest,
                    Document.source_url,
                )
                .join(Document, Document.id == Chunk.document_id)
                .where(
                    Chunk.document_id == seed.document_id,
                    Chunk.chunk_index >= low,
                    Chunk.chunk_index <= high,
                )
                .order_by(Chunk.chunk_index)
            )
        ).all()
        for row in rows:
            expanded.append(
                Candidate(
                    chunk_id=str(row.id),
                    document_id=str(row.document_id),
                    chunk_index=row.chunk_index,
                    content=row.content,
                    title=row.title,
                    guest=row.guest,
                    source_url=row.source_url,
                    fused_score=seed.fused_score,
                    vector_score=seed.vector_score if row.chunk_index == seed.chunk_index else None,
                    keyword_score=seed.keyword_score if row.chunk_index == seed.chunk_index else None,
                    retrieval="episode",
                    metadata=row.meta or {},
                )
            )
    return expanded


async def retrieve_evidence(
    session: AsyncSession,
    query: str,
    *,
    strategy: str | None = None,
    top_k: int | None = None,
    route: str | None = None,
) -> EvidencePack:
    started = time.perf_counter()
    strategy = strategy or choose_strategy(query, route)
    top_k = top_k or settings.retrieval_top_k
    candidate_k = max(settings.retrieval_candidate_k, top_k * 3)

    log.info("retrieval.started", strategy=strategy, top_k=top_k, query_chars=len(query))

    degraded = False
    degraded_reason: str | None = None

    # ---- vector leg -------------------------------------------------------
    vector_hits: list[Candidate] = []
    try:
        embedded = await embed_with_fallback([query])
        if embedded.degraded:
            degraded = True
            degraded_reason = (
                f"Embeddings degraded to the deterministic fallback: {embedded.reason}"
            )
        vector_hits = await vector_search(session, embedded.vectors[0], limit=candidate_k)
        floor = settings.retrieval_min_vector_similarity
        vector_hits = [
            hit for hit in vector_hits if (hit.vector_score or 0.0) >= floor
        ]
    except EmbeddingError as exc:
        degraded = True
        degraded_reason = f"Semantic search unavailable ({exc.message}); keyword-only results."
        log.warning("retrieval.vector_unavailable", reason=exc.message)
    except SQLAlchemyError as exc:
        log.error("database.error", stage="vector_search", error=str(exc))
        raise RetrievalError() from exc

    # ---- keyword leg ------------------------------------------------------
    try:
        keyword_hits = await keyword_search(session, query, limit=candidate_k)
    except SQLAlchemyError as exc:
        log.error("database.error", stage="keyword_search", error=str(exc))
        if not vector_hits:
            raise RetrievalError() from exc
        keyword_hits = []
        degraded = True
        degraded_reason = degraded_reason or "Keyword search unavailable; vector-only results."

    merged = merge_candidates(vector_hits, keyword_hits)
    total_candidates = len(merged)
    ranked = rerank(query, merged, top_k=top_k)

    if strategy == "episode" and ranked:
        try:
            ranked = await _expand_episodes(session, ranked)
        except SQLAlchemyError as exc:  # pragma: no cover - fall back to chunks
            log.error("database.error", stage="episode_expansion", error=str(exc))
            strategy = "chunk"

    evidence = [_to_evidence_item(c, i + 1) for i, c in enumerate(ranked)]
    if settings.retrieval_min_score > 0:
        evidence = [e for e in evidence if e.score >= settings.retrieval_min_score]

    pack = EvidencePack(
        query=query,
        strategy=strategy,  # type: ignore[arg-type]
        evidence=evidence,
        episode_ids=sorted({c.document_id for c in ranked}),
        total_candidates=total_candidates,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        degraded=degraded,
        degraded_reason=degraded_reason,
    )

    log.info(
        "retrieval.completed",
        strategy=strategy,
        retrieval_count=len(pack.evidence),
        candidates=total_candidates,
        vector_hits=len(vector_hits),
        keyword_hits=len(keyword_hits),
        latency_ms=pack.latency_ms,
        degraded=degraded,
    )
    log.info(
        "evidence.created",
        evidence_count=len(pack.evidence),
        episodes=len(pack.episode_ids),
    )
    return pack
