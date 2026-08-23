"""Shared API dependencies."""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Message, Session as ChatSession, User
from app.errors import DatabaseError, SessionNotFoundError

DEFAULT_USER_EXTERNAL_ID = "local-demo-user"


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


async def get_or_create_user(
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
    external_id: str | None = None,
) -> User:
    """Single-tenant demo identity.

    There is no authentication in this build (documented as out of scope in the
    PRD). The client sends a stable `external_user_id`; the row is created on
    first use. Swapping this for real auth means replacing this function only.
    """
    try:
        if user_id:
            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user:
                return user

        key = external_id or DEFAULT_USER_EXTERNAL_ID
        user = (
            await db.execute(select(User).where(User.external_id == key))
        ).scalar_one_or_none()
        if user:
            return user

        user = User(external_id=key, display_name=key, meta={"created_by": "api"})
        db.add(user)
        await db.flush()
        return user
    except SQLAlchemyError as exc:
        raise DatabaseError() from exc


async def load_session(db: AsyncSession, session_id: uuid.UUID) -> ChatSession:
    try:
        chat_session = (
            await db.execute(select(ChatSession).where(ChatSession.id == session_id))
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise DatabaseError() from exc
    if chat_session is None:
        raise SessionNotFoundError(details={"session_id": str(session_id)})
    return chat_session


async def message_count(db: AsyncSession, session_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Message.id)).where(Message.session_id == session_id)
            )
        ).scalar_one()
    )


DbSession = Depends(get_db)
