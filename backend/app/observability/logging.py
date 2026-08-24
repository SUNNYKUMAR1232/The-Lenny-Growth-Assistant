"""Structured JSON logging.

One log line per event, machine-parseable, with a request-scoped correlation
id bound via contextvars so every downstream component (retrieval, LLM,
grounding, artifacts) is traceable without threading a logger through calls.

Event names are stable and documented in docs/architecture.md#observability.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from app.config import settings

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)

SENSITIVE_KEYS = {
    "anthropic_api_key",
    "openai_api_key",
    "api_key",
    "authorization",
    "password",
    "token",
    "secret",
}


def set_request_context(request_id: str | None = None, session_id: str | None = None) -> None:
    if request_id is not None:
        _request_id.set(request_id)
    if session_id is not None:
        _session_id.set(session_id)


def get_request_id() -> str | None:
    return _request_id.get()


def _inject_context(_logger: Any, _name: str, event_dict: dict) -> dict:
    rid = _request_id.get()
    sid = _session_id.get()
    if rid:
        event_dict.setdefault("request_id", rid)
    if sid:
        event_dict.setdefault("session_id", sid)
    return event_dict


def _redact(_logger: Any, _name: str, event_dict: dict) -> dict:
    """Defence in depth: never let a credential reach the log stream."""
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "***redacted***"
    return event_dict


def configure_logging(stream: Any = None) -> None:
    """Configure structured logging.

    `stream` redirects rendered output (tests assert on what is actually
    written; an embedding host may want its own sink). When a custom stream is
    given, logger caching is disabled so the redirect takes effect immediately.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=stream or sys.stdout,
        level=getattr(logging, settings.log_level, logging.INFO),
        force=True,
    )
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_context,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level, logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=stream is None,
    )


def get_logger(name: str = "app") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
