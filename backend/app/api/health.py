"""Health and model-status endpoints.

`/health` is a *dependency* health check, not a liveness ping: it reports the
database, the model provider and the embedding provider separately, so an
evaluator whose Ollama is not running sees exactly that instead of a generic
failure once they send their first message.

Status semantics:
  ok       — every dependency reachable
  degraded — the app runs but something is impaired (no embeddings, no model)
  down     — the database is unreachable; nothing meaningful works
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.embeddings.factory import get_embedder
from app.llm.factory import get_provider
from app.observability.logging import get_logger
from app.schemas.contracts import ComponentHealth, HealthResponse, ModelInfo

router = APIRouter(tags=["health"])
log = get_logger("api.health")

VERSION = "1.0.0"


async def _check_database(db: AsyncSession) -> ComponentHealth:
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        extension = (
            await db.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one()
        latency = round((time.perf_counter() - started) * 1000, 2)
        if not extension:
            return ComponentHealth(
                status="degraded",
                detail="pgvector extension is not installed; run migrations.",
                latency_ms=latency,
            )
        return ComponentHealth(status="ok", detail="postgres + pgvector", latency_ms=latency)
    except Exception as exc:
        log.error("database.error", stage="health", error=str(exc))
        return ComponentHealth(status="down", detail="PostgreSQL is unreachable.")


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    components: dict[str, ComponentHealth] = {"database": await _check_database(db)}

    provider = get_provider()
    available, detail = await provider.health()
    components["model"] = ComponentHealth(
        status="ok" if available else "degraded", detail=detail
    )

    embedder = get_embedder()
    embed_ok, embed_detail = await embedder.health()
    components["embeddings"] = ComponentHealth(
        status="ok" if embed_ok else "degraded", detail=embed_detail
    )

    if components["database"].status == "down":
        overall = "down"
    elif any(c.status != "ok" for c in components.values()):
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(
        status=overall,  # type: ignore[arg-type]
        version=VERSION,
        environment=settings.app_env,
        components=components,
    )


@router.get("/api/model", response_model=ModelInfo, tags=["model"])
async def model_status() -> ModelInfo:
    """What the UI's model badge reads."""
    provider = get_provider()
    available, detail = await provider.health()
    fallback = None
    if not available and settings.llm_provider == "ollama":
        fallback = (
            "No automatic cloud fallback: set LLM_PROVIDER=cloud explicitly to switch."
        )
    return ModelInfo(
        provider=settings.llm_provider,  # type: ignore[arg-type]
        model=provider.model,
        label=provider.label(),
        cloud_provider=settings.cloud_provider if settings.llm_provider == "cloud" else None,
        embedding_provider=settings.embedding_provider,
        embedding_model=(
            settings.ollama_embedding_model
            if settings.embedding_provider == "ollama"
            else settings.embedding_provider
        ),
        available=available,
        detail=detail,
        fallback=fallback,
    )
