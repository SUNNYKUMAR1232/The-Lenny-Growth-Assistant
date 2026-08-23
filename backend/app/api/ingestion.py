"""Ingestion endpoints.

`POST /api/ingestion` runs the pipeline synchronously and returns real counts.
That is a deliberate choice over a fire-and-forget job: this is an operator
action performed a handful of times, and an evaluator running it wants the
numbers, not a job id to poll. The CLI (`python -m app.scripts.ingest`) is the
path for the full 300-episode run, which takes minutes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.errors import DatabaseError, IngestionError, ValidationError
from app.ingestion.indexer import corpus_stats, ingest_path
from app.observability.logging import get_logger
from app.schemas.contracts import CorpusStats, IngestionRequest, IngestionStats

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])
log = get_logger("api.ingestion")


@router.post("", response_model=IngestionStats)
async def run_ingestion(
    payload: IngestionRequest, db: AsyncSession = Depends(get_db)
) -> IngestionStats:
    target = Path(payload.path) if payload.path else settings.transcripts_path

    # Path containment: the API must not be a filesystem reader.
    root = settings.transcripts_path.resolve()
    resolved = target.expanduser().resolve()
    if not str(resolved).startswith(str(root)):
        raise ValidationError(
            "Ingestion paths must live inside TRANSCRIPTS_DIR.",
            details={"transcripts_dir": str(root)},
        )
    if not resolved.exists():
        raise ValidationError(
            "That transcript path does not exist. Run `make transcripts` first.",
            details={"path": str(resolved)},
        )

    try:
        return await ingest_path(
            db, resolved, limit=payload.limit, force=payload.force, embed=payload.embed
        )
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("database.error", stage="ingestion", error=str(exc))
        raise DatabaseError() from exc
    except Exception as exc:
        await db.rollback()
        log.error("ingestion.failed", error=str(exc))
        raise IngestionError() from exc


@router.get("/stats", response_model=CorpusStats)
async def stats(db: AsyncSession = Depends(get_db)) -> CorpusStats:
    try:
        return await corpus_stats(db)
    except SQLAlchemyError as exc:
        raise DatabaseError() from exc
