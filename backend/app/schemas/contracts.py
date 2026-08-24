"""Typed request/response contracts.

These Pydantic models are the API's public surface. The frontend mirrors them
in `frontend/lib/types.ts`; if you change one, change both.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["user", "assistant", "system"]
RouteName = Literal["KNOWLEDGE_Q", "SHIP30", "ARTIFACT"]
ArtifactType = Literal["markdown", "html"]
MemoryType = Literal["semantic", "episodic"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------- health
class ComponentHealth(BaseModel):
    status: Literal["ok", "degraded", "down"]
    detail: str | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    environment: str
    components: dict[str, ComponentHealth]


class ModelInfo(BaseModel):
    provider: Literal["ollama", "cloud", "pi", "stub"]
    model: str
    label: str
    cloud_provider: str | None = None
    embedding_provider: str
    embedding_model: str
    available: bool
    detail: str | None = None
    fallback: str | None = None
    # Whether the active configuration came from `.env` or from the UI, and
    # whether the UI is allowed to change it on this deployment.
    source: Literal["environment", "runtime"] = "environment"
    configurable: bool = True


class ModelConfigRequest(BaseModel):
    """A proposed provider configuration from the model-settings panel."""

    provider: Literal["ollama", "anthropic", "openai", "pi"]
    model: str | None = Field(default=None, max_length=200)
    # Pi only: which backend Pi should drive (anthropic, openai, ollama, …).
    agent_backend: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=500)
    # Write-only. Never echoed back by any response model.
    api_key: str | None = Field(default=None, max_length=500, repr=False)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        candidate = value.strip().rstrip("/")
        if not candidate.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return candidate

    @field_validator("api_key")
    @classmethod
    def _strip_key(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class ModelProviderOption(BaseModel):
    id: Literal["ollama", "anthropic", "openai", "pi"]
    label: str
    needs_api_key: bool
    needs_base_url: bool
    default_base_url: str | None = None
    models: list[str] = Field(default_factory=list)
    help: str | None = None
    # Pi drives another provider underneath; the panel renders this as a
    # second select ("agent backend") when the list is non-empty.
    backends: list[str] = Field(default_factory=list)


class ModelOptionsResponse(BaseModel):
    configurable: bool
    providers: list[ModelProviderOption]


class ModelConfigResponse(BaseModel):
    config: dict[str, Any]
    model: ModelInfo


class ModelTestResponse(BaseModel):
    ok: bool
    detail: str
    label: str


# ------------------------------------------------------------------- sessions
class SessionCreateRequest(BaseModel):
    user_id: uuid.UUID | None = None
    external_user_id: str | None = Field(
        default=None,
        max_length=255,
        description="Stable client-side identity; a user row is created on first use.",
    )
    title: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="meta", serialization_alias="metadata"
    )
    message_count: int = 0

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int


class MessageResponse(ORMModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: Role
    content: str
    created_at: datetime
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="meta", serialization_alias="metadata"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SessionDetailResponse(BaseModel):
    session: SessionResponse
    messages: list[MessageResponse]
    artifacts: list["ArtifactSummary"] = Field(default_factory=list)


# ----------------------------------------------------------------- evidence
class EvidenceItem(BaseModel):
    source_id: str
    chunk_id: str
    title: str
    guest: str | None = None
    source_url: str | None = None
    chunk_index: int
    text: str
    score: float
    vector_score: float | None = None
    keyword_score: float | None = None
    retrieval: Literal["vector", "keyword", "hybrid", "episode"] = "hybrid"


class EvidencePack(BaseModel):
    query: str
    strategy: Literal["chunk", "episode"] = "chunk"
    evidence: list[EvidenceItem] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)
    total_candidates: int = 0
    latency_ms: float = 0.0
    degraded: bool = False
    degraded_reason: str | None = None
    # True only when the chunks table itself is empty — "nothing is indexed",
    # not "nothing matched". The two need different answers and different fixes.
    corpus_empty: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.evidence


class GroundingClaim(BaseModel):
    claim: str
    supported: bool
    support_score: float
    best_source_id: str | None = None


class GroundingReport(BaseModel):
    enabled: bool = True
    checked_claims: int = 0
    supported_claims: int = 0
    supported_ratio: float = 1.0
    revised: bool = False
    action: Literal["accepted", "annotated", "refused", "skipped"] = "accepted"
    claims: list[GroundingClaim] = Field(default_factory=list)


class MemoryUsed(BaseModel):
    id: uuid.UUID
    type: MemoryType
    key: str
    value: str
    confidence: float
    importance: float


# --------------------------------------------------------------------- chat
class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    route_hint: RouteName | None = None
    artifact_format: ArtifactType | None = None
    stream: bool = False


class ChatResponse(BaseModel):
    session_id: uuid.UUID
    request_id: str
    user_message: MessageResponse
    message: MessageResponse
    route: RouteName
    evidence: list[EvidenceItem] = Field(default_factory=list)
    memories_used: list[MemoryUsed] = Field(default_factory=list)
    grounding: GroundingReport
    artifact: "ArtifactResponse | None" = None
    model: ModelInfo
    latency_ms: float
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- artifacts
class ArtifactCreateRequest(BaseModel):
    session_id: uuid.UUID
    type: ArtifactType
    title: str = Field(default="Artifact", max_length=512)
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactSummary(ORMModel):
    id: uuid.UUID
    session_id: uuid.UUID
    type: ArtifactType
    title: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ArtifactResponse(ORMModel):
    id: uuid.UUID
    session_id: uuid.UUID
    type: ArtifactType
    title: str
    content: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="meta", serialization_alias="metadata"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactSummary]


# ------------------------------------------------------------------- memory
class MemoryResponse(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    type: MemoryType
    key: str
    value: str
    confidence: float
    importance: float
    created_at: datetime
    updated_at: datetime
    source_session_id: uuid.UUID | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class MemoryListResponse(BaseModel):
    memories: list[MemoryResponse]
    enabled: bool


class MemoryCreateRequest(BaseModel):
    user_id: uuid.UUID
    type: MemoryType = "semantic"
    key: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    importance: float = Field(default=0.7, ge=0.0, le=1.0)


# ---------------------------------------------------------------- ingestion
class IngestionRequest(BaseModel):
    path: str | None = Field(
        default=None, description="Directory or file to ingest. Defaults to TRANSCRIPTS_DIR."
    )
    limit: int = Field(default=0, ge=0, description="0 = ingest everything found.")
    force: bool = Field(
        default=False, description="Re-chunk and re-embed documents whose content is unchanged."
    )
    embed: bool = True


class IngestionStats(BaseModel):
    documents_found: int = 0
    documents_ingested: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    chunks_written: int = 0
    chunks_embedded: int = 0
    embedding_model: str | None = None
    duration_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)


class CorpusStats(BaseModel):
    documents: int
    chunks: int
    embedded_chunks: int
    guests: int
    embedding_models: list[str] = Field(default_factory=list)


class ErrorPayload(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorPayload


SessionDetailResponse.model_rebuild()
ChatResponse.model_rebuild()
