"""Semantic retrieval over pgvector.

Cosine distance against `chunks.embedding`. Similarity is reported as
`1 - distance` so every score in the system reads "higher is better".

Index choice: HNSW (`vector_cosine_ops`) built in the Alembic migration. At
this corpus size (~300 episodes, ~40k chunks) a flat scan would also be fast,
but HNSW keeps the shape of the query plan honest for a corpus 10x larger and
costs one index build at ingest time.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document
from app.observability.logging import get_logger
from app.retrieval.types import Candidate

log = get_logger("retrieval.vector")


async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    limit: int = 30,
    document_ids: list[str] | None = None,
) -> list[Candidate]:
    if not query_vector:
        return []

    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    stmt = (
        select(
            Chunk.id,
            Chunk.document_id,
            Chunk.chunk_index,
            Chunk.content,
            Chunk.meta,
            Document.title,
            Document.guest,
            Document.source_url,
            distance,
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    if document_ids:
        stmt = stmt.where(Chunk.document_id.in_(document_ids))

    rows = (await session.execute(stmt)).all()
    candidates: list[Candidate] = []
    for rank, row in enumerate(rows):
        candidates.append(
            Candidate(
                chunk_id=str(row.id),
                document_id=str(row.document_id),
                chunk_index=row.chunk_index,
                content=row.content,
                title=row.title,
                guest=row.guest,
                source_url=row.source_url,
                vector_score=max(0.0, 1.0 - float(row.distance)),
                vector_rank=rank + 1,
                retrieval="vector",
                metadata=row.meta or {},
            )
        )
    return candidates
