"""Provider selection and documented fallback behaviour.

Fallback policy (documented in README "Model configuration"):

  LLM_PROVIDER=ollama  -> local model; NO automatic fallback to cloud.
  LLM_PROVIDER=cloud   -> cloud model; NO automatic fallback to local.
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
from app.llm.stub import StubProvider

_override: LLMProvider | None = None


def set_provider_override(provider: LLMProvider | None) -> None:
    """Used by tests to inject a fake provider."""
    global _override
    _override = provider


def get_provider(name: str | None = None) -> LLMProvider:
    if _override is not None:
        return _override
    choice = (name or settings.llm_provider).lower()
    if choice == "cloud":
        return CloudProvider()
    if choice == "stub":
        return StubProvider()
    return OllamaProvider()


def describe_active_provider() -> dict[str, str | None]:
    provider = get_provider()
    return {
        "provider": settings.llm_provider,
        "model": provider.model,
        "label": provider.label(),
        "cloud_provider": (
            settings.cloud_provider if settings.llm_provider == "cloud" else None
        ),
    }
