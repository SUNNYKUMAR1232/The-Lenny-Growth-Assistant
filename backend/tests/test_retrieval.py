"""Retrieval tests: vector leg, keyword leg, hybrid fusion, empty results."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings.factory import embed_with_fallback
from app.retrieval.evidence import choose_strategy, retrieve_evidence
from app.retrieval.keyword import fallback_query, keyword_search
from app.retrieval.reranker import merge_candidates, rerank
from app.retrieval.types import Candidate
from app.retrieval.vector import vector_search

pytestmark = pytest.mark.usefixtures("corpus")


async def test_keyword_search_finds_exact_terms(db: AsyncSession) -> None:
    hits = await keyword_search(db, "Sean Ellis product-market fit survey", limit=10)
    assert hits, "expected lexical hits for a distinctive phrase"
    assert "Sean Ellis" in hits[0].content
    assert hits[0].keyword_score == pytest.approx(1.0)


async def test_keyword_search_returns_nothing_for_absent_terms(db: AsyncSession) -> None:
    hits = await keyword_search(db, "quantum chromodynamics lattice gauge", limit=10)
    assert hits == []


async def test_keyword_fallback_query_strips_stopwords() -> None:
    assert fallback_query("What is the retention curve about?") == "retention OR curve"


async def test_vector_search_returns_ranked_candidates(db: AsyncSession) -> None:
    vector = (await embed_with_fallback(["retention curve cohort compounding"])).vectors[0]
    hits = await vector_search(db, vector, limit=5)
    assert hits
    assert all(0.0 <= h.vector_score <= 1.0 for h in hits)
    scores = [h.vector_score for h in hits]
    assert scores == sorted(scores, reverse=True)


async def test_vector_search_with_empty_vector_is_safe(db: AsyncSession) -> None:
    assert await vector_search(db, [], limit=5) == []


async def test_hybrid_pack_carries_full_traceability(db: AsyncSession) -> None:
    pack = await retrieve_evidence(db, "How do I know if I have product-market fit?")
    assert pack.evidence
    top = pack.evidence[0]
    assert top.source_id.startswith("S")
    assert top.chunk_id and top.title and top.guest
    assert top.source_url and "youtube.com" in top.source_url
    assert top.chunk_index >= 0
    assert pack.total_candidates >= len(pack.evidence)
    assert pack.latency_ms >= 0


async def test_hybrid_merge_keeps_both_scores() -> None:
    vector_hit = Candidate(
        chunk_id="c1", document_id="d1", chunk_index=0, content="x", title="t",
        vector_score=0.9, vector_rank=1,
    )
    keyword_hit = Candidate(
        chunk_id="c1", document_id="d1", chunk_index=0, content="x", title="t",
        keyword_score=0.8, keyword_rank=2,
    )
    merged = merge_candidates([vector_hit], [keyword_hit])
    assert len(merged) == 1
    assert merged[0].retrieval == "hybrid"
    assert merged[0].vector_score == 0.9
    assert merged[0].keyword_score == 0.8


async def test_reranker_enforces_episode_diversity() -> None:
    candidates = [
        Candidate(
            chunk_id=f"c{i}", document_id="same-doc", chunk_index=i,
            content="text", title="Episode", vector_rank=i + 1,
        )
        for i in range(6)
    ] + [
        Candidate(
            chunk_id="other", document_id="other-doc", chunk_index=0,
            content="text", title="Other", vector_rank=7,
        )
    ]
    ranked = rerank("question", candidates, top_k=3, max_per_document=2)
    from_same = [c for c in ranked if c.document_id == "same-doc"]
    assert len(from_same) == 2
    assert any(c.document_id == "other-doc" for c in ranked)


async def test_guest_name_in_query_boosts_that_episode(db: AsyncSession) -> None:
    pack = await retrieve_evidence(db, "What did Rahul Vohra say about surveys?")
    assert pack.evidence[0].guest == "Rahul Vohra"


async def test_empty_retrieval_produces_empty_pack(db: AsyncSession) -> None:
    """Vector search always returns neighbours, so a query with nothing to match
    must be filtered by the similarity floor rather than by luck."""
    pack = await retrieve_evidence(db, "zzzz qqqq xxxx nonexistent terminology")
    assert pack.is_empty
    assert pack.evidence == []


def test_strategy_selection_is_deterministic() -> None:
    assert choose_strategy("What is retention?") == "chunk"
    assert choose_strategy("Compare how Rahul and Casey think about PMF") == "episode"
    assert choose_strategy("What do guests say about onboarding?") == "episode"
    assert choose_strategy("anything", route="SHIP30") == "episode"


async def test_episode_strategy_expands_context(db: AsyncSession) -> None:
    pack = await retrieve_evidence(
        db, "Compare what different guests say about retention", strategy="episode"
    )
    assert pack.strategy == "episode"
    assert pack.evidence
    assert all(item.retrieval == "episode" for item in pack.evidence)
