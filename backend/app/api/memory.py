"""Memory inspection and control.

Personalization the user cannot see or delete is a dark pattern. These
endpoints back the memory panel in the UI: list what is remembered, add a
fact deliberately, delete one, or clear everything.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_or_create_user
from app.config import settings
from app.db.database import get_db
from app.errors import DatabaseError, NotFoundError
from app.memory import manager
from app.observability.logging import get_logger
from app.schemas.contracts import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryResponse,
)

router = APIRouter(prefix="/api/memories", tags=["memory"])
log = get_logger("api.memory")


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    external_user_id: str | None = Query(default=None, max_length=255),
    user_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> MemoryListResponse:
    user = await get_or_create_user(db, user_id, external_user_id)
    try:
        memories = await manager.list_memories(db, user.id)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise DatabaseError() from exc
    return MemoryListResponse(
        memories=[MemoryResponse.model_validate(m) for m in memories],
        enabled=settings.memory_enabled,
    )


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreateRequest, db: AsyncSession = Depends(get_db)
) -> MemoryResponse:
    try:
        memory = await manager.upsert_manual(
            db,
            payload.user_id,
            type_=payload.type,
            key=payload.key,
            value=payload.value,
            confidence=payload.confidence,
            importance=payload.importance,
        )
        await db.commit()
        await db.refresh(memory)
    except SQLAlchemyError as exc:
        await db.rollback()
        raise DatabaseError() from exc
    log.info("memory.created", memory_id=str(memory.id), key=memory.key)
    return MemoryResponse.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_memory(memory_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    try:
        deleted = await manager.delete_memory(db, memory_id)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise DatabaseError() from exc
    if not deleted:
        raise NotFoundError("That memory does not exist.")
    log.info("memory.deleted", memory_id=str(memory_id))


@router.delete("", status_code=status.HTTP_200_OK)
async def clear_memories(
    external_user_id: str | None = Query(default=None, max_length=255),
    user_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    user = await get_or_create_user(db, user_id, external_user_id)
    try:
        removed = await manager.clear_memories(db, user.id)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise DatabaseError() from exc
    log.info("memory.cleared", user_id=str(user.id), count=removed)
    return {"deleted": removed}
