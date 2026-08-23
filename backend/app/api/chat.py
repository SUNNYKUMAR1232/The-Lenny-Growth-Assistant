"""Chat endpoint — JSON and SSE.

Both transports run the identical controller pipeline. SSE exists because a
local 8B model on a laptop takes seconds to answer, and a UI that shows
"routing → retrieving 8 excerpts → writing" is honest about where that time
goes instead of showing an opaque spinner.

Event stream:
    event: route      {route, method, confidence, model}
    event: memory     {count, keys}
    event: retrieval  {status: "started"}
    event: evidence   {count, strategy, items[...]}
    event: token      {text}            # RAG route only
    event: final      ChatResponse
    event: error      {error: {code, message}}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.controller import handle_turn
from app.api.deps import get_request_id, load_session
from app.db.database import get_db, get_sessionmaker
from app.errors import AppError
from app.observability.logging import get_logger
from app.schemas.contracts import ChatResponse, MessageCreateRequest

router = APIRouter(prefix="/api/sessions", tags=["chat"])
log = get_logger("api.chat")

SENTINEL = object()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/{session_id}/messages", response_model=ChatResponse)
async def post_message(
    session_id: uuid.UUID,
    payload: MessageCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    chat_session = await load_session(db, session_id)
    request_id = get_request_id(request)

    if not payload.stream:
        return await handle_turn(
            db,
            chat_session,
            payload.content,
            request_id=request_id,
            route_hint=payload.route_hint,
            artifact_format=payload.artifact_format,
        )

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        await queue.put((event, data))

    async def run() -> None:
        # The streaming worker owns its own database session with an explicit
        # lifecycle. Relying on the request-scoped dependency here is unsafe:
        # if the client disconnects mid-stream, dependency teardown races with
        # task cancellation and can leak a checked-out connection.
        try:
            maker = get_sessionmaker()
            async with maker() as stream_db:
                stream_session = await load_session(stream_db, session_id)
                response = await handle_turn(
                    stream_db,
                    stream_session,
                    payload.content,
                    request_id=request_id,
                    route_hint=payload.route_hint,
                    artifact_format=payload.artifact_format,
                    stream=True,
                    on_event=emit,
                )
            await queue.put(("final", response.model_dump()))
        except AppError as exc:
            log.warning("request.failed", code=exc.code, error=exc.message)
            await queue.put(("error", exc.to_payload()))
        except Exception as exc:  # pragma: no cover - unexpected
            log.error("request.failed", error=str(exc), exc_info=True)
            await queue.put(
                (
                    "error",
                    {
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "Something went wrong handling this message.",
                        }
                    },
                )
            )
        finally:
            await queue.put((SENTINEL, {}))

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            while True:
                event, data = await queue.get()
                if event is SENTINEL:
                    break
                yield _sse(event, data)
        finally:
            # Always let the worker unwind. If the client disconnects mid-stream
            # we cancel it, but we still await it so its database session is
            # returned to the pool instead of leaking a checked-out connection.
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Request-ID": request_id,
        },
    )
