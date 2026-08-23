"""Lexical retrieval over PostgreSQL full-text search.

`websearch_to_tsquery` is used rather than `plainto_tsquery` because it accepts
the punctuation people actually type ("product-market fit", quoted phrases,
`-negation`) without a parsing layer of our own. If the query degenerates to an
empty tsquery (all stopwords), we fall back to an OR of its content words so a
question like "what is retention?" still returns something.

Why keyword search at all, next to vectors: transcripts are full of proper
nouns — "Superhuman", "PMF survey", "Sean Ellis" — where exact lexical match
beats embedding similarity, and it is the only leg that still works when the
embedding model is unavailable.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger
from app.retrieval.types import Candidate

log = get_logger("retrieval.keyword")

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]{2,}")
_STOPWORDS = {
    "the", "and", "for", "with", "what", "how", "why", "does", "did", "was",
    "are", "you", "your", "that", "this", "from", "about", "into", "when",
    "their", "there", "here", "have", "has", "had", "can", "should", "would",
    "tell", "give", "show", "make", "get", "say", "said", "who", "which",
}

SQL = text(
    """
    WITH q AS (SELECT websearch_to_tsquery('english', :query) AS tsq)
    SELECT c.id, c.document_id, c.chunk_index, c.content, c.metadata,
           d.title, d.guest, d.source_url,
           ts_rank_cd(c.content_tsv, q.tsq) AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id, q
    WHERE c.content_tsv @@ q.tsq
    ORDER BY rank DESC
    LIMIT :limit
    """
)


def fallback_query(query: str) -> str:
    """Turn a natural question into an OR-query of its content words."""
    words = [
        w.lower()
        for w in _WORD_RE.findall(query)
        if w.lower() not in _STOPWORDS
    ]
    return " OR ".join(dict.fromkeys(words))


async def keyword_search(
    session: AsyncSession, query: str, limit: int = 30
) -> list[Candidate]:
    rows = (
        await session.execute(SQL, {"query": query, "limit": limit})
    ).all()

    if not rows:
        relaxed = fallback_query(query)
        if relaxed:
            rows = (
                await session.execute(SQL, {"query": relaxed, "limit": limit})
            ).all()

    candidates: list[Candidate] = []
    top_rank = float(rows[0].rank) if rows else 0.0
    for rank, row in enumerate(rows):
        raw = float(row.rank)
        candidates.append(
            Candidate(
                chunk_id=str(row.id),
                document_id=str(row.document_id),
                chunk_index=row.chunk_index,
                content=row.content,
                title=row.title,
                guest=row.guest,
                source_url=row.source_url,
                # Normalised against the best hit: ts_rank_cd is unbounded, so
                # the absolute value is only meaningful within one query.
                keyword_score=(raw / top_rank) if top_rank > 0 else 0.0,
                keyword_rank=rank + 1,
                retrieval="keyword",
                metadata=row.metadata or {},
            )
        )
    return candidates
