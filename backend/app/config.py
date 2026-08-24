"""Application configuration.

Every knob the evaluator may need to turn lives here and is driven by the
environment, so switching models or retrieval weights never requires a code
change. See `.env.example` at the repository root for documented defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

LLMProviderName = Literal["ollama", "cloud", "pi", "stub"]
CloudProviderName = Literal["anthropic", "openai"]
EmbeddingProviderName = Literal["ollama", "openai", "hash"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "Lenny Growth Assistant"
    app_env: Literal["local", "docker", "test", "production"] = "local"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Lets the UI switch model provider / paste an API key at runtime. Handy
    # for a local evaluation, wrong for a shared deployment — so it defaults
    # off in production. Keys set this way live in memory only.
    allow_runtime_model_config: bool = True

    # ------------------------------------------------------------- database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/lenny"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 15_000

    # ------------------------------------------------------------ llm layer
    llm_provider: LLMProviderName = "ollama"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_embedding_model: str = "nomic-embed-text"

    cloud_provider: CloudProviderName = "anthropic"
    cloud_model: str = "claude-sonnet-4-5"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None

    # ---------------------------------------------------- pi coding agent
    # The agent-layer integration required by the assignment (Claude Agent SDK
    # or Pi Coding Agent). Pi is a Node CLI driven headlessly; see
    # app/llm/pi_agent.py for why it is invoked with every tool disabled.
    pi_cli_path: str = "pi"
    pi_provider: str = "anthropic"
    pi_model: str = "claude-sonnet-4-5"
    pi_thinking: Literal["off", "minimal", "low", "medium", "high"] = "off"

    llm_timeout_seconds: float = 120.0
    llm_max_output_tokens: int = 4096
    llm_temperature: float = 0.3

    # ----------------------------------------------------------- embeddings
    # `hash` is a deterministic, dependency-free embedder used by the test
    # suite and as a last-resort fallback. It is NOT semantically meaningful;
    # see docs/architecture.md ("Embeddings and the honest fallback").
    embedding_provider: EmbeddingProviderName = "ollama"
    embedding_dim: int = 768
    embedding_batch_size: int = 16
    embedding_allow_fallback: bool = True

    # ------------------------------------------------------------ retrieval
    retrieval_top_k: int = 8
    retrieval_candidate_k: int = 30
    retrieval_vector_weight: float = 0.6
    retrieval_keyword_weight: float = 0.4
    retrieval_rrf_k: int = 60
    retrieval_min_score: float = 0.0
    # Junk floor for the vector leg. Vector search always returns k nearest
    # neighbours, even for a query with nothing to match, so a floor is what
    # lets "no evidence" actually happen. The right value is model-dependent:
    # 0.05 removes near-orthogonal matches; with a semantic embedder whose
    # unrelated-pair similarity sits high, raise it (0.4-0.6). Grounding
    # validation is the model-independent backstop.
    retrieval_min_vector_similarity: float = 0.05
    retrieval_max_chars_per_chunk: int = 2200
    episode_context_max_chunks: int = 6
    episode_context_max_episodes: int = 3

    # --------------------------------------------------------------- memory
    memory_enabled: bool = True
    memory_min_confidence: float = 0.6
    memory_min_importance: float = 0.4
    memory_top_k: int = 5
    memory_max_per_user: int = 200
    memory_extract_every_n_turns: int = 2

    # ------------------------------------------------------------- grounding
    grounding_enabled: bool = True
    grounding_min_support: float = 0.28
    grounding_min_supported_ratio: float = 0.5

    # ------------------------------------------------------------ ingestion
    transcripts_dir: str = str(REPO_ROOT / "data" / "transcripts")
    # Skill files are data, not code: mounted read-only in Docker so a writer
    # can change the Ship 30 standard without rebuilding the image.
    skills_dir: str = str(REPO_ROOT / "skills")
    chunk_target_tokens: int = 350
    chunk_overlap_tokens: int = 60
    ingestion_max_documents: int = 0  # 0 = no limit

    # ------------------------------------------------------------ artifacts
    artifact_max_bytes: int = 400_000

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def transcripts_path(self) -> Path:
        return Path(self.transcripts_dir).expanduser()

    @property
    def sync_database_url(self) -> str:
        """Alembic runs synchronously; translate the async DSN."""
        return self.database_url.replace("+asyncpg", "").replace(
            "postgresql://", "postgresql+psycopg2://", 1
        ) if "+asyncpg" in self.database_url else self.database_url

    def active_model_label(self) -> str:
        if self.llm_provider == "ollama":
            return f"ollama/{self.ollama_model}"
        if self.llm_provider == "cloud":
            return f"{self.cloud_provider}/{self.cloud_model}"
        if self.llm_provider == "pi":
            return f"pi/{self.pi_provider}/{self.pi_model}"
        return "stub/deterministic"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
