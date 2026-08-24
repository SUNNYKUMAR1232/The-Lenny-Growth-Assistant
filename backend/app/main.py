"""FastAPI application factory.

Boot order matters: logging is configured before anything else so that even
startup failures are structured, and the DB connection is *not* required for
the process to come up — `/health` reporting `down` is more useful to an
operator than a container that will not start.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api import artifacts, chat, health, ingestion, memory, model, sessions
from app.config import settings
from app.db.database import dispose_engine
from app.errors import AppError, DatabaseError, ValidationError
from app.observability.logging import configure_logging, get_logger, set_request_context

configure_logging()
log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "app.startup",
        environment=settings.app_env,
        llm_provider=settings.llm_provider,
        model=settings.active_model_label(),
        embedding_provider=settings.embedding_provider,
    )
    yield
    await dispose_engine()
    log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=health.VERSION,
        description=(
            "A controlled AI knowledge assistant over Lenny's Podcast transcripts: "
            "evidence-grounded retrieval, persistent user context, specialized "
            "skills, model flexibility, and sandboxed artifact generation."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_context(request_id=request_id, session_id=None)
        started = time.perf_counter()
        log.info(
            "request.started",
            method=request.method,
            path=request.url.path,
            request_id=request_id,
        )
        try:
            response = await call_next(request)
        except Exception:
            log.error(
                "request.failed",
                method=request.method,
                path=request.url.path,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                exc_info=True,
            )
            raise
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    # ------------------------------------------------------ error handlers
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "request.error",
            code=exc.code,
            path=request.url.path,
            status_code=exc.status_code,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        error = ValidationError(
            "The request payload is invalid.",
            details={
                "fields": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "issue": e["msg"]}
                    for e in exc.errors()[:10]
                ]
            },
        )
        return JSONResponse(status_code=error.status_code, content=error.to_payload())

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.error("database.error", path=request.url.path, error=str(exc))
        error = DatabaseError()
        return JSONResponse(status_code=error.status_code, content=error.to_payload())

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak a stack trace or a provider message to the client.
        log.error("request.failed", path=request.url.path, error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Something went wrong. The incident was logged.",
                }
            },
        )

    for router in (
        health.router,
        model.router,
        sessions.router,
        chat.router,
        artifacts.router,
        memory.router,
        ingestion.router,
    ):
        app.include_router(router)

    return app


app = create_app()
