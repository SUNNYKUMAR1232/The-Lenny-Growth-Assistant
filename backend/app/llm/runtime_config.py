"""Runtime model configuration — switching provider without a restart.

`.env` is the source of truth at boot. This module holds an optional
*in-process override* set through `POST /api/model/config`, so an evaluator can
paste an Anthropic key (or point at any OpenAI-compatible base URL) in the UI
and use it immediately, without editing files and restarting the stack.

Deliberate constraints, because this endpoint accepts a credential:

  * **Memory only.** The override lives in this process and dies with it. No
    database row, no file on disk, nothing to leak in a backup or a `git add`.
  * **Write-only keys.** A key goes in and never comes back out. Reads return
    `api_key_set: true` plus the last four characters, which is enough to tell
    two keys apart and useless to an attacker.
  * **Env wins on restart.** Restarting the backend always returns to the
    documented `.env` configuration — the override cannot silently become the
    permanent state of a deployment.
  * **Switchable off.** `ALLOW_RUNTIME_MODEL_CONFIG=false` (the default when
    `APP_ENV=production`) refuses the endpoint entirely. A shared deployment
    should configure models through the environment, not through a browser.

This is a forward-deployment convenience for a single-tenant local product,
and it is documented as such in README "Model configuration".
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.config import settings
from app.observability.logging import get_logger

log = get_logger("llm.runtime_config")


@dataclass(frozen=True, slots=True)
class RuntimeModelConfig:
    provider: str  # "ollama" | "cloud" | "stub"
    model: str | None = None
    cloud_provider: str | None = None  # "anthropic" | "openai"
    base_url: str | None = None  # OpenAI-compatible endpoints; Ollama host
    api_key: str | None = None

    def masked_hint(self) -> str | None:
        if not self.api_key:
            return None
        tail = self.api_key[-4:] if len(self.api_key) > 4 else "****"
        return f"…{tail}"


_override: RuntimeModelConfig | None = None


def get_override() -> RuntimeModelConfig | None:
    return _override


def set_override(config: RuntimeModelConfig) -> RuntimeModelConfig:
    """Replace the active override. Never logs the key itself."""
    global _override
    # Keep an existing key when the caller re-saves the form without re-typing
    # it. Matching on the VENDOR too is essential: without it, switching the
    # panel from Anthropic to OpenAI would carry the Anthropic key over and
    # send that credential to a different company's endpoint.
    if (
        not config.api_key
        and _override
        and _override.provider == config.provider
        and _override.cloud_provider == config.cloud_provider
    ):
        config = replace(config, api_key=_override.api_key)
    _override = config
    log.info(
        "model.config_updated",
        provider=config.provider,
        model=config.model,
        cloud_provider=config.cloud_provider,
        base_url=config.base_url,
        api_key_set=bool(config.api_key),
    )
    return config


def clear_override() -> None:
    global _override
    if _override is not None:
        log.info("model.config_cleared", provider=_override.provider)
    _override = None


def effective_provider_name() -> str:
    return _override.provider if _override else settings.llm_provider


def effective_cloud_vendor() -> str:
    if _override and _override.cloud_provider:
        return _override.cloud_provider
    return settings.cloud_provider


def api_key_for(vendor: str) -> str | None:
    """Override key first, then the environment."""
    if _override and _override.api_key and (_override.cloud_provider or "") == vendor:
        return _override.api_key
    if vendor == "anthropic":
        return settings.anthropic_api_key
    return settings.openai_api_key


def base_url_for(vendor: str) -> str | None:
    if _override and _override.base_url and (_override.cloud_provider or "") == vendor:
        return _override.base_url
    if vendor == "openai":
        return settings.openai_base_url
    return None


def describe() -> dict[str, object]:
    """Safe-to-serialise view of the override (never includes the key)."""
    if _override is None:
        return {"source": "environment", "api_key_set": False}
    return {
        "source": "runtime",
        "provider": _override.provider,
        "model": _override.model,
        "cloud_provider": _override.cloud_provider,
        "base_url": _override.base_url,
        "api_key_set": bool(_override.api_key),
        "api_key_hint": _override.masked_hint(),
    }
