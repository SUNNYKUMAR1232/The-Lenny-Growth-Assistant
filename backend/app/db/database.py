"""Async SQLAlchemy engine/session wiring.

The engine is created lazily so the process can boot (and `/health` can report
`degraded`) even when PostgreSQL is not reachable yet — important for Docker
Compose cold starts and for an evaluator who has not started the DB.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.errors import DatabaseError
from app.observability.logging import get_logger

log = get_logger("db")


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _connect_args() -> dict[str, Any]:
    if "asyncpg" not in settings.database_url:
        return {}
    return {
        "server_settings": {
            "application_name": "lenny-growth-assistant",
            "statement_timeout": str(settings.db_statement_timeout_ms),
        },
        "timeout": 10,
    }


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            future=True,
            connect_args=_connect_args(),
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, autoflush=False
        )
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction-scoped session per request."""
    maker = get_sessionmaker()
    try:
        async with maker() as session:
            yield session
    except DatabaseError:
        raise
    except Exception as exc:  # pragma: no cover - connection-level failures
        log.error("database.error", error=str(exc), stage="session")
        raise DatabaseError() from exc


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_engine_for_tests() -> None:
    """Drop cached engine/sessionmaker so tests can rebind the DSN."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
