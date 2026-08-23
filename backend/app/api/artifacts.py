"""Artifact endpoints.

`POST /api/artifacts` accepts artifact content from a client and runs it
through the *same* sanitizer the agent uses. There is no path into storage
that skips sanitization — that is the point of routing everything through one
module.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import load_session
from app.db.database import get_db
from app.db.models import Artifact
from app.errors import ArtifactNotFoundError, DatabaseError
from app.observability.logging import get_logger
from app.schemas.contracts import (
    ArtifactCreateRequest,
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactSummary,
)
from app.security.sanitizer import sanitize_html, sanitize_markdown, wrap_document

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])
log = get_logger("api.artifacts")


@router.post("", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    payload: ArtifactCreateRequest, db: AsyncSession = Depends(get_db)
) -> ArtifactResponse:
    await load_session(db, payload.session_id)

    if payload.type == "html":
        cleaned, report = sanitize_html(payload.content)
        content = wrap_document(cleaned, title=payload.title)
        sanitization = report.as_dict()
    else:
        content = sanitize_markdown(payload.content)
        sanitization = {"markdown_html_stripped": len(content) != len(payload.content)}

    artifact = Artifact(
        session_id=payload.session_id,
        type=payload.type,
        title=payload.title[:512],
        content=content,
        raw_content=payload.content,
        meta={**payload.metadata, "sanitization": sanitization, "source": "api"},
    )
    db.add(artifact)
    try:
        await db.commit()
        await db.refresh(artifact)
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("database.error", stage="create_artifact", error=str(exc))
        raise DatabaseError() from exc

    log.info("artifact.generated", artifact_id=str(artifact.id), artifact_type=artifact.type)
    return ArtifactResponse.model_validate(artifact)


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    session_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)
) -> ArtifactListResponse:
    await load_session(db, session_id)
    try:
        rows = (
            (
                await db.execute(
                    select(Artifact)
                    .where(Artifact.session_id == session_id)
                    .order_by(Artifact.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        raise DatabaseError() from exc
    return ArtifactListResponse(
        artifacts=[ArtifactSummary.model_validate(a) for a in rows]
    )


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ArtifactResponse:
    try:
        artifact = (
            await db.execute(select(Artifact).where(Artifact.id == artifact_id))
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise DatabaseError() from exc
    if artifact is None:
        raise ArtifactNotFoundError(details={"artifact_id": str(artifact_id)})
    return ArtifactResponse.model_validate(artifact)
