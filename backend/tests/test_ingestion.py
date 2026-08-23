"""Ingestion tests: loading, cleaning, chunking, idempotent indexing."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document
from app.ingestion.chunker import chunk_utterances, estimate_tokens
from app.ingestion.cleaner import clean_document, parse_utterances
from app.ingestion.indexer import build_deep_link, ingest_path
from app.ingestion.loader import iter_documents, load_file

TRANSCRIPT = """---
guest: Casey Winters
title: Why retention is the only growth metric that matters
youtube_url: https://www.youtube.com/watch?v=abc123
publish_date: 2023-04-21
keywords:
- retention
- growth
---

# Why retention is the only growth metric that matters

## Transcript

Lenny (00:00:00):
This episode is brought to you by Coda. Coda is an all-in-one doc that combines documents and spreadsheets.

Casey Winters (00:01:30):
Retention is the compounding engine of growth. If your retention curve does not flatten, acquisition is a leaky bucket.

Lenny (00:02:10):
How should teams look at the curve?

Casey Winters (00:02:15):
Look at it by cohort and by use case, never in aggregate. Aggregate retention hides the one segment that actually loves you.

Lenny (01:05:21):
Thank you so much for listening. If you found this valuable, you can subscribe to the show on Apple Podcast.
"""


def _write(tmp_path: Path) -> Path:
    root = tmp_path / "episodes" / "casey-winters"
    root.mkdir(parents=True)
    path = root / "transcript.md"
    path.write_text(TRANSCRIPT, encoding="utf-8")
    return tmp_path


def test_loader_parses_frontmatter_and_metadata(tmp_path: Path) -> None:
    root = _write(tmp_path)
    documents = list(iter_documents(root))
    assert len(documents) == 1
    document = documents[0]
    assert document.guest == "Casey Winters"
    assert document.title.startswith("Why retention")
    assert document.source_url == "https://www.youtube.com/watch?v=abc123"
    assert document.metadata["publish_date"] == "2023-04-21"
    assert document.content_hash


def test_loader_skips_index_and_readme_files(tmp_path: Path) -> None:
    root = _write(tmp_path)
    (root / "README.md").write_text("# Archive readme", encoding="utf-8")
    (root / "index").mkdir()
    (root / "index" / "growth.md").write_text("# Growth topics", encoding="utf-8")
    assert len(list(iter_documents(root))) == 1


def test_loader_handles_files_without_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "plain.txt"
    path.write_text("Speaker (00:00:10):\nJust some spoken words here.", encoding="utf-8")
    documents = load_file(path, tmp_path)
    assert len(documents) == 1
    assert "spoken words" in documents[0].body


def test_cleaner_extracts_utterances_with_timestamps() -> None:
    utterances = parse_utterances(TRANSCRIPT)
    speakers = {u.speaker for u in utterances}
    assert "Casey Winters" in speakers
    retention = next(u for u in utterances if "compounding engine" in u.text)
    assert retention.start_seconds == 90


def test_cleaner_drops_sponsor_reads_and_outro() -> None:
    _, cleaned = clean_document(TRANSCRIPT)
    assert "brought to you by" not in cleaned.lower()
    assert "subscribe to the show" not in cleaned.lower()
    assert "compounding engine" in cleaned


def test_chunker_packs_on_utterance_boundaries() -> None:
    utterances, _ = clean_document(TRANSCRIPT)
    chunks = chunk_utterances(utterances, target_tokens=30, overlap_tokens=5)
    assert chunks
    assert all(chunk.text.strip() for chunk in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert any(c.start_seconds is not None for c in chunks)
    assert all(c.token_estimate > 0 for c in chunks)


def test_token_estimate_is_reasonable() -> None:
    assert estimate_tokens("") == 0
    assert 10 <= estimate_tokens(" ".join(["word"] * 10)) <= 20


def test_deep_link_targets_the_spoken_second() -> None:
    assert build_deep_link("https://www.youtube.com/watch?v=abc", 90) == (
        "https://www.youtube.com/watch?v=abc&t=90s"
    )
    assert build_deep_link(None, 90) is None
    assert build_deep_link("https://example.com/x", None) == "https://example.com/x"


async def test_ingestion_is_idempotent(db: AsyncSession, tmp_path: Path) -> None:
    root = _write(tmp_path)

    first = await ingest_path(db, root)
    assert first.documents_ingested == 1
    assert first.chunks_written > 0
    assert first.chunks_embedded == first.chunks_written

    second = await ingest_path(db, root)
    assert second.documents_skipped == 1
    assert second.chunks_written == 0

    forced = await ingest_path(db, root, force=True)
    assert forced.documents_ingested == 1

    documents = (await db.execute(select(func.count(Document.id)))).scalar_one()
    assert documents == 1


async def test_chunks_carry_full_traceability(db: AsyncSession, tmp_path: Path) -> None:
    root = _write(tmp_path)
    await ingest_path(db, root)

    chunk = (await db.execute(select(Chunk).limit(1))).scalar_one()
    assert chunk.meta["title"].startswith("Why retention")
    assert chunk.meta["guest"] == "Casey Winters"
    assert chunk.meta["source_url"].startswith("https://www.youtube.com")
    assert chunk.meta["deep_link"].endswith("s")
    assert chunk.meta["source_key"].endswith("transcript.md")
    assert chunk.embedding is not None


async def test_ingestion_without_embeddings_still_indexes_text(
    db: AsyncSession, tmp_path: Path
) -> None:
    root = _write(tmp_path)
    stats = await ingest_path(db, root, embed=False)
    assert stats.chunks_written > 0
    assert stats.chunks_embedded == 0

    embedded = (
        await db.execute(
            select(func.count(Chunk.id)).where(Chunk.embedding.is_not(None))
        )
    ).scalar_one()
    assert embedded == 0

    # Keyword search must still work — that is the point of the degraded path.
    from app.retrieval.keyword import keyword_search

    hits = await keyword_search(db, "retention compounding engine", limit=5)
    assert hits


async def test_missing_path_is_reported_not_raised(db: AsyncSession, tmp_path: Path) -> None:
    stats = await ingest_path(db, tmp_path / "does-not-exist")
    assert stats.documents_found == 0
    assert stats.errors == []
