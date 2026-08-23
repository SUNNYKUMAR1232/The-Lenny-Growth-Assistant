"""Ingestion orchestration: load → clean → chunk → embed → store → index.

Re-ingestion is idempotent. A document is keyed by `source_key` (its path in
the transcript archive) and carries a `content_hash`; unchanged documents are
skipped unless `force=True`, so `make ingest` after a `git pull` of the
archive only pays for what actually changed.

Lexical indexing needs no work here: `chunks.content_tsv` is a Postgres
GENERATED column with a GIN index (see the Alembic migration), so full-text
search is always consistent with `content` by construction.
"""

from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Chunk, Document
from app.embeddings.factory import embed_with_fallback
from app.errors import EmbeddingError
from app.ingestion.chunker import chunk_utterances
from app.ingestion.cleaner import clean_document
from app.ingestion.loader import RawDocument, iter_documents
from app.observability.logging import get_logger
from app.schemas.contracts import CorpusStats, IngestionStats

log = get_logger("ingestion")


def build_deep_link(source_url: str | None, start_seconds: int | None) -> str | None:
    """YouTube deep link so a citation lands on the spoken sentence."""
    if not source_url or start_seconds is None:
        return source_url
    if "youtube.com" in source_url or "youtu.be" in source_url:
        joiner = "&" if "?" in source_url else "?"
        return f"{source_url}{joiner}t={int(start_seconds)}s"
    return source_url


async def ingest_document(
    session: AsyncSession,
    raw: RawDocument,
    *,
    embed: bool = True,
    force: bool = False,
) -> tuple[str, int, int, str | None]:
    """Ingest one transcript. Returns (status, chunks, embedded, model)."""
    utterances, cleaned = clean_document(raw.body)
    if not cleaned.strip():
        return "skipped_empty", 0, 0, None

    existing = (
        await session.execute(
            select(Document).where(Document.source_key == raw.source_key)
        )
    ).scalar_one_or_none()

    if existing and existing.content_hash == raw.content_hash and not force:
        return "skipped_unchanged", 0, 0, None

    if existing:
        document = existing
        document.title = raw.title
        document.guest = raw.guest
        document.source_url = raw.source_url
        document.content = cleaned
        document.content_hash = raw.content_hash
        document.meta = raw.metadata
        await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    else:
        document = Document(
            source_key=raw.source_key,
            title=raw.title,
            guest=raw.guest,
            source_url=raw.source_url,
            content=cleaned,
            content_hash=raw.content_hash,
            meta=raw.metadata,
        )
        session.add(document)
        await session.flush()

    drafts = chunk_utterances(
        utterances,
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    if not drafts:
        return "skipped_empty", 0, 0, None

    vectors: list[list[float] | None] = [None] * len(drafts)
    embedding_model: str | None = None
    embedded = 0
    if embed:
        try:
            result = await embed_with_fallback([d.text for d in drafts])
            vectors = list(result.vectors)
            embedding_model = result.model
            embedded = len(vectors)
            if result.degraded:
                log.warning("ingestion.embedding_degraded", reason=result.reason)
        except EmbeddingError as exc:
            # Store the chunks anyway: keyword search still works, and a later
            # `make ingest` with Ollama up will fill the vectors in.
            log.warning(
                "ingestion.embedding_failed",
                source_key=raw.source_key,
                reason=exc.message,
            )

    for draft, vector in zip(drafts, vectors):
        meta = draft.metadata()
        meta.update(
            {
                "title": raw.title,
                "guest": raw.guest,
                "source_url": raw.source_url,
                "deep_link": build_deep_link(raw.source_url, draft.start_seconds),
                "source_key": raw.source_key,
            }
        )
        session.add(
            Chunk(
                document_id=document.id,
                chunk_index=draft.index,
                content=draft.text,
                token_estimate=draft.token_estimate,
                embedding=vector,
                embedding_model=embedding_model if vector is not None else None,
                meta=meta,
            )
        )

    return ("updated" if existing else "created"), len(drafts), embedded, embedding_model


async def ingest_path(
    session: AsyncSession,
    path: Path | None = None,
    *,
    limit: int = 0,
    force: bool = False,
    embed: bool = True,
) -> IngestionStats:
    started = time.perf_counter()
    target = Path(path) if path else settings.transcripts_path
    stats = IngestionStats()

    log.info("ingestion.started", path=str(target), limit=limit, force=force, embed=embed)

    for raw in iter_documents(target, limit=limit or settings.ingestion_max_documents):
        stats.documents_found += 1
        try:
            status, chunks, embedded, model = await ingest_document(
                session, raw, embed=embed, force=force
            )
            if status.startswith("skipped"):
                stats.documents_skipped += 1
            else:
                stats.documents_ingested += 1
                stats.chunks_written += chunks
                stats.chunks_embedded += embedded
                stats.embedding_model = model or stats.embedding_model
            await session.commit()
        except Exception as exc:  # one bad transcript must not abort the run
            await session.rollback()
            stats.documents_failed += 1
            message = f"{raw.source_key}: {type(exc).__name__}: {exc}"
            stats.errors.append(message[:300])
            log.error("ingestion.document_failed", source_key=raw.source_key, error=str(exc))

        if stats.documents_found % 25 == 0:
            log.info(
                "ingestion.progress",
                found=stats.documents_found,
                ingested=stats.documents_ingested,
                chunks=stats.chunks_written,
            )

    stats.duration_seconds = round(time.perf_counter() - started, 2)
    log.info(
        "ingestion.completed",
        **stats.model_dump(exclude={"errors"}),
        error_count=len(stats.errors),
    )
    return stats


async def corpus_stats(session: AsyncSession) -> CorpusStats:
    documents = (await session.execute(select(func.count(Document.id)))).scalar_one()
    chunks = (await session.execute(select(func.count(Chunk.id)))).scalar_one()
    embedded = (
        await session.execute(
            select(func.count(Chunk.id)).where(Chunk.embedding.is_not(None))
        )
    ).scalar_one()
    guests = (
        await session.execute(select(func.count(distinct(Document.guest))))
    ).scalar_one()
    models = (
        (
            await session.execute(
                select(distinct(Chunk.embedding_model)).where(
                    Chunk.embedding_model.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    return CorpusStats(
        documents=documents,
        chunks=chunks,
        embedded_chunks=embedded,
        guests=guests,
        embedding_models=[m for m in models if m],
    )
