"""Model status and runtime model configuration.

  GET    /api/model          — what the UI's badge reads
  GET    /api/model/options  — providers and preset models the UI offers
  POST   /api/model/config   — switch provider / model / base URL / API key
  POST   /api/model/test     — verify the *proposed* config with one real call
  DELETE /api/model/config   — revert to the `.env` configuration

The write endpoints exist so an evaluator can turn on a cloud model from the
browser instead of editing `.env` and restarting. They are guarded by
`ALLOW_RUNTIME_MODEL_CONFIG` and store nothing on disk — see
`app/llm/runtime_config.py` for the full reasoning.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.config import settings
from app.embeddings.factory import get_embedder
from app.errors import AppError
from app.llm.cloud import CloudProvider
from app.llm.factory import get_provider
from app.llm.ollama import OllamaProvider
from app.llm.pi_agent import PiCodingAgentProvider
from app.llm.runtime_config import (
    RuntimeModelConfig,
    clear_override,
    describe,
    get_override,
    set_override,
)
from app.observability.logging import get_logger
from app.schemas.contracts import (
    ModelConfigRequest,
    ModelConfigResponse,
    ModelInfo,
    ModelOptionsResponse,
    ModelProviderOption,
    ModelTestResponse,
)

router = APIRouter(prefix="/api/model", tags=["model"])
log = get_logger("api.model")


class ConfigLockedError(AppError):
    code = "MODEL_CONFIG_LOCKED"
    status_code = status.HTTP_403_FORBIDDEN
    message = (
        "Runtime model configuration is disabled on this deployment. "
        "Configure the model through environment variables instead."
    )


def _guard() -> None:
    if not settings.allow_runtime_model_config:
        raise ConfigLockedError()


def _embedding_model_name() -> str:
    if settings.embedding_provider == "ollama":
        return settings.ollama_embedding_model
    if settings.embedding_provider == "openai":
        return "text-embedding-3-small"
    return f"deterministic-hash-{settings.embedding_dim}"


async def _model_info() -> ModelInfo:
    provider = get_provider()
    available, detail = await provider.health()
    runtime = get_override()
    active = runtime.provider if runtime else settings.llm_provider

    fallback = None
    if not available and active == "ollama":
        fallback = (
            "No automatic cloud fallback: switch provider explicitly in model "
            "settings (or set LLM_PROVIDER=cloud)."
        )

    return ModelInfo(
        provider=active,  # type: ignore[arg-type]
        model=provider.model,
        label=provider.label(),
        cloud_provider=(
            ((runtime.cloud_provider if runtime else None) or settings.cloud_provider)
            if active == "cloud"
            else (
                ((runtime.cloud_provider if runtime else None) or settings.pi_provider)
                if active == "pi"
                else None
            )
        ),
        embedding_provider=settings.embedding_provider,
        embedding_model=_embedding_model_name(),
        available=available,
        detail=detail,
        fallback=fallback,
        source="runtime" if runtime else "environment",
        configurable=settings.allow_runtime_model_config,
    )


@router.get("", response_model=ModelInfo)
async def model_status() -> ModelInfo:
    return await _model_info()


@router.get("/options", response_model=ModelOptionsResponse)
async def model_options() -> ModelOptionsResponse:
    """Presets the settings panel offers. Any model string is still accepted —
    these are conveniences, not an allowlist."""
    return ModelOptionsResponse(
        configurable=settings.allow_runtime_model_config,
        providers=[
            ModelProviderOption(
                id="ollama",
                label="Ollama (local)",
                needs_api_key=False,
                needs_base_url=True,
                default_base_url=settings.ollama_base_url,
                models=["llama3.1:8b", "llama3.2:3b", "qwen2.5:7b", "mistral:7b"],
                help=(
                    "Runs entirely on your machine. Pull the model first: "
                    "`ollama pull llama3.1:8b`."
                ),
            ),
            ModelProviderOption(
                id="anthropic",
                label="Anthropic Claude",
                needs_api_key=True,
                needs_base_url=False,
                default_base_url=None,
                models=["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
                help="Paste an API key from console.anthropic.com. Kept in memory only.",
            ),
            ModelProviderOption(
                id="pi",
                label="Pi Coding Agent",
                needs_api_key=True,
                needs_base_url=False,
                default_base_url=None,
                models=["claude-sonnet-4-5", "gpt-4o", "llama3.1:8b"],
                backends=["anthropic", "openai", "ollama", "google"],
                help=(
                    "Runs generation through the Pi Coding Agent CLI with every "
                    "tool disabled. Requires `npm install -g "
                    "@earendil-works/pi-coding-agent`. Pick the backend Pi drives; "
                    "`ollama` needs no key."
                ),
            ),
            ModelProviderOption(
                id="openai",
                label="OpenAI / OpenAI-compatible",
                needs_api_key=True,
                needs_base_url=True,
                default_base_url="https://api.openai.com/v1",
                models=["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
                help=(
                    "Change the base URL to reach any OpenAI-compatible gateway "
                    "— OpenRouter, Together, Groq, vLLM, LM Studio."
                ),
            ),
        ],
    )


def _to_runtime(payload: ModelConfigRequest) -> RuntimeModelConfig:
    if payload.provider == "pi":
        # `cloud_provider` carries Pi's backend vendor, so the API-key lookup
        # in runtime_config resolves against the right credential.
        return RuntimeModelConfig(
            provider="pi",
            cloud_provider=(payload.agent_backend or settings.pi_provider).lower(),
            model=payload.model or settings.pi_model,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    if payload.provider == "ollama":
        return RuntimeModelConfig(
            provider="ollama",
            model=payload.model or settings.ollama_model,
            base_url=payload.base_url or settings.ollama_base_url,
        )
    return RuntimeModelConfig(
        provider="cloud",
        cloud_provider=payload.provider,
        model=payload.model or settings.cloud_model,
        base_url=payload.base_url,
        api_key=payload.api_key,
    )


def _provider_from(config: RuntimeModelConfig):
    if config.provider == "ollama":
        return OllamaProvider(base_url=config.base_url, model=config.model)
    if config.provider == "pi":
        return PiCodingAgentProvider(provider=config.cloud_provider, model=config.model)
    return CloudProvider(vendor=config.cloud_provider, model=config.model)


@router.post("/test", response_model=ModelTestResponse)
async def test_model(payload: ModelConfigRequest) -> ModelTestResponse:
    """Verify a configuration with one real, minimal round trip.

    Tests the *submitted* configuration, not the saved one, so the user finds
    out a key is wrong before it becomes the active provider.
    """
    _guard()
    candidate = _to_runtime(payload)

    # Temporarily apply so the provider picks up the submitted key, then always
    # restore — a failed test must not change the active configuration.
    previous = get_override()
    try:
        set_override(candidate)
        provider = _provider_from(candidate)
        verify = getattr(provider, "verify", None)
        ok, detail = await (verify() if verify else provider.health())
    finally:
        if previous is None:
            clear_override()
        else:
            set_override(previous)

    log.info(
        "model.test",
        provider=payload.provider,
        model=candidate.model,
        ok=ok,
    )
    label = (
        f"pi/{candidate.cloud_provider}/{candidate.model}"
        if payload.provider == "pi"
        else f"{payload.provider}/{candidate.model}"
    )
    return ModelTestResponse(ok=ok, detail=detail, label=label)


@router.post("/config", response_model=ModelConfigResponse)
async def set_model_config(payload: ModelConfigRequest) -> ModelConfigResponse:
    _guard()
    set_override(_to_runtime(payload))
    return ModelConfigResponse(config=describe(), model=await _model_info())


@router.delete("/config", response_model=ModelConfigResponse)
async def reset_model_config() -> ModelConfigResponse:
    _guard()
    clear_override()
    return ModelConfigResponse(config=describe(), model=await _model_info())
