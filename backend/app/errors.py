"""Typed application errors and the single structured error envelope.

Every failure the client can see is `{"error": {"code", "message", "details"}}`.
Stack traces and provider messages stay in the logs; the UI gets a stable code
it can branch on and a sentence a human can act on.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, user-visible failures."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return {"error": payload}


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422
    message = "The request payload is invalid."


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    message = "The requested resource does not exist."


class SessionNotFoundError(NotFoundError):
    code = "SESSION_NOT_FOUND"
    message = "That chat session does not exist."


class ArtifactNotFoundError(NotFoundError):
    code = "ARTIFACT_NOT_FOUND"
    message = "That artifact does not exist."


class DatabaseError(AppError):
    code = "DATABASE_UNAVAILABLE"
    status_code = 503
    message = "The database is temporarily unavailable."


class RetrievalError(AppError):
    code = "RETRIEVAL_UNAVAILABLE"
    status_code = 503
    message = "Knowledge retrieval is temporarily unavailable."


class EmptyRetrievalError(AppError):
    code = "NO_EVIDENCE"
    status_code = 200  # handled in-band: the assistant answers by refusing
    message = "No transcript evidence matched this question."


class EmbeddingError(AppError):
    code = "EMBEDDING_UNAVAILABLE"
    status_code = 503
    message = "The embedding model is unavailable."


class LLMError(AppError):
    code = "MODEL_UNAVAILABLE"
    status_code = 503
    message = "The language model is unavailable."


class LLMTimeoutError(LLMError):
    code = "MODEL_TIMEOUT"
    status_code = 504
    message = "The language model took too long to respond."


class LLMConfigError(LLMError):
    code = "MODEL_NOT_CONFIGURED"
    status_code = 503
    message = "The selected model provider is not configured."


class ArtifactError(AppError):
    code = "ARTIFACT_INVALID"
    status_code = 422
    message = "The generated artifact could not be prepared safely."


class SanitizationError(ArtifactError):
    code = "SANITIZATION_FAILED"
    message = "The generated HTML could not be sanitized and was not rendered."


class IngestionError(AppError):
    code = "INGESTION_FAILED"
    status_code = 500
    message = "Transcript ingestion failed."
