"""Provider selection and documented fallback behaviour.

Fallback policy (documented in README "Model configuration"):

  LLM_PROVIDER=ollama  -> local model; NO automatic fallback to cloud.
  LLM_PROVIDER=cloud   -> cloud model; NO automatic fallback to local.
  LLM_PROVIDER=pi      -> Pi Coding Agent CLI (the agent-layer integration),
                          itself pointed at a cloud or local model.
  LLM_PROVIDER=stub    -> deterministic double (tests/CI only).

Silent cross-provider fallback is deliberately *not* implemented: an operator
who chose a local model for data-residency reasons must never have their
prompt quietly shipped to a cloud API because the local server hiccuped. When
the selected provider is down the request fails with a typed, actionable error
and the UI shows it.
"""

from __future__ import annotations

from app.config import settings
from app.llm.base import LLMProvider
from app.llm.cloud import CloudProvider
from app.llm.ollama import OllamaProvider
from app.llm.pi_agent import PiCodingAgentProvider
from app.llm.runtime_config import get_override as get_runtime_override
from app.llm.stub import StubProvider

_override: LLMProvider | None = None


def set_provider_override(provider: LLMProvider | None) -> None:
    """Used by tests to inject a fake provider."""
    global _override
    _override = provider


def get_provider(name: str | None = None) -> LLMProvider:
    if _override is not None:
        return _override

    runtime = get_runtime_override()
    choice = (name or (runtime.provider if runtime else settings.llm_provider)).lower()

    if choice == "cloud":
        vendor = (
            runtime.cloud_provider
            if runtime and runtime.cloud_provider
            else settings.cloud_provider
        )
        model = runtime.model if runtime and runtime.model else settings.cloud_model
        return CloudProvider(vendor=vendor, model=model)
    if choice == "pi":
        return PiCodingAgentProvider(
            provider=(runtime.cloud_provider if runtime else None) or None,
            model=(runtime.model if runtime else None) or None,
        )
    if choice == "stub":
        return StubProvider()

    if runtime and runtime.provider == "ollama":
        return OllamaProvider(
            base_url=runtime.base_url or settings.ollama_base_url,
            model=runtime.model or settings.ollama_model,
        )
    return OllamaProvider()


def describe_active_provider() -> dict[str, str | None]:
    provider = get_provider()
    runtime = get_runtime_override()
    active = runtime.provider if runtime else settings.llm_provider
    return {
        "provider": active,
        "model": provider.model,
        "label": provider.label(),
        "cloud_provider": (
            (runtime.cloud_provider if runtime else None) or settings.cloud_provider
            if active == "cloud"
            else None
        ),
        "source": "runtime" if runtime else "environment",
    }
