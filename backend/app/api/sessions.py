"""Session and message endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_create_user, load_session
from app.db.database import get_db
from app.db.models import Artifact, Message, Session as ChatSession
from app.errors import DatabaseError
from app.observability.logging import get_logger
from app.schemas.contracts import (
    ArtifactSummary,
    MessageResponse,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionListResponse,
    SessionResponse,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
log = get_logger("api.sessions")


def _to_response(chat_session: ChatSession, count: int = 0) -> SessionResponse:
    return SessionResponse(
        id=chat_session.id,
        user_id=chat_session.user_id,
        title=chat_session.title,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
        metadata=chat_session.meta or {},
        message_count=count,
    )


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest, db: AsyncSession = Depends(get_db)
) -> SessionResponse:
    user = await get_or_create_user(db, payload.user_id, payload.external_user_id)
    chat_session = ChatSession(
        user_id=user.id,
        title=(payload.title or "New chat")[:255],
        meta=payload.metadata,
    )
    db.add(chat_session)
    try:
        await db.commit()
        await db.refresh(chat_session)
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("database.error", stage="create_session", error=str(exc))
        raise DatabaseError() from exc
    log.info("session.created", session_id=str(chat_session.id), user_id=str(user.id))
    return _to_response(chat_session)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    external_user_id: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SessionListResponse:
    user = await get_or_create_user(db, None, external_user_id)
    try:
        counts = (
            select(Message.session_id, func.count(Message.id).label("n"))
            .group_by(Message.session_id)
            .subquery()
        )
        rows = (
            await db.execute(
                select(ChatSession, func.coalesce(counts.c.n, 0))
                .outerjoin(counts, counts.c.session_id == ChatSession.id)
                .where(ChatSession.user_id == user.id)
                .order_by(ChatSession.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        total = (
            await db.execute(
                select(func.count(ChatSession.id)).where(ChatSession.user_id == user.id)
            )
        ).scalar_one()
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("database.error", stage="list_sessions", error=str(exc))
        raise DatabaseError() from exc

    return SessionListResponse(
        sessions=[_to_response(s, int(n)) for s, n in rows], total=int(total)
    )


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> SessionDetailResponse:
    chat_session = await load_session(db, session_id)
    try:
        messages = list(
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at.asc(), Message.id.asc())
                )
            )
            .scalars()
            .all()
        )
        artifacts = list(
            (
                await db.execute(
                    select(Artifact)
                    .where(Artifact.session_id == session_id)
                    .order_by(Artifact.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        log.error("database.error", stage="get_session", error=str(exc))
        raise DatabaseError() from exc

    return SessionDetailResponse(
        session=_to_response(chat_session, len(messages)),
        messages=[MessageResponse.model_validate(m) for m in messages],
        artifacts=[ArtifactSummary.model_validate(a) for a in artifacts],
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    chat_session = await load_session(db, session_id)
    try:
        await db.delete(chat_session)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("database.error", stage="delete_session", error=str(exc))
        raise DatabaseError() from exc
    log.info("session.deleted", session_id=str(session_id))
