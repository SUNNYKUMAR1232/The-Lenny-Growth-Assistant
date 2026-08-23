"""Transcript ingestion CLI.

    python -m app.scripts.ingest --limit 25          # quick demo corpus
    python -m app.scripts.ingest                     # full archive
    python -m app.scripts.ingest --force             # re-chunk + re-embed
    python -m app.scripts.ingest --no-embed          # keyword-only, no Ollama
    python -m app.scripts.ingest --stats             # what's in the DB now

Ingesting the full 303-episode archive with local embeddings takes a while
(dominated by the embedding model, not by Postgres). `--limit 25` is enough
for a convincing demo and finishes in a couple of minutes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import settings
from app.db.database import dispose_engine, get_sessionmaker
from app.ingestion.indexer import corpus_stats, ingest_path
from app.observability.logging import configure_logging, get_logger

log = get_logger("scripts.ingest")


async def _run(args: argparse.Namespace) -> int:
    maker = get_sessionmaker()
    async with maker() as session:
        if args.stats:
            stats = await corpus_stats(session)
            print(json.dumps(stats.model_dump(), indent=2))
            return 0

        path = Path(args.path) if args.path else settings.transcripts_path
        if not path.exists():
            print(
                f"Transcript path not found: {path}\n"
                "Run `make transcripts` to clone the archive, or pass --path.",
                file=sys.stderr,
            )
            return 2

        result = await ingest_path(
            session,
            path,
            limit=args.limit,
            force=args.force,
            embed=not args.no_embed,
        )
        print(json.dumps(result.model_dump(), indent=2))
        return 1 if result.documents_failed and not result.documents_ingested else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Lenny's Podcast transcripts.")
    parser.add_argument("--path", help="Directory or file to ingest.")
    parser.add_argument("--limit", type=int, default=0, help="Max documents (0 = all).")
    parser.add_argument("--force", action="store_true", help="Re-ingest unchanged documents.")
    parser.add_argument("--no-embed", action="store_true", help="Skip embedding generation.")
    parser.add_argument("--stats", action="store_true", help="Print corpus stats and exit.")
    args = parser.parse_args()

    configure_logging()
    try:
        return asyncio.run(_wrapper(args))
    except KeyboardInterrupt:  # pragma: no cover
        print("Interrupted.", file=sys.stderr)
        return 130


async def _wrapper(args: argparse.Namespace) -> int:
    try:
        return await _run(args)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
