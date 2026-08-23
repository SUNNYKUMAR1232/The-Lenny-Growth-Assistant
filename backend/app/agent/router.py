"""Task classification.

Deterministic first, model second. Roughly 95% of real requests are settled by
explicit patterns — "write an essay", "make me a landing page" — and rules are
free, instant, testable, and identical on every run. The LLM classifier is a
tie-breaker for genuinely ambiguous phrasing, and it is skipped entirely when
a rule fires or when the provider is unavailable.

Routes:
  KNOWLEDGE_Q — grounded question answering (default)
  SHIP30      — ~1,250-word Ship 30 for 30 essay
  ARTIFACT    — Markdown document or HTML/CSS artifact for the viewer

Ordering matters: SHIP30 is checked before ARTIFACT because "write me an essay
I can publish as a page" is an essay request first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.llm.base import ChatMessage, LLMProvider
from app.observability.logging import get_logger

log = get_logger("agent.router")

SHIP30_PATTERNS = re.compile(
    r"\b(ship ?30|ship 30 for 30|atomic essay|essay|blog post|newsletter (?:post|issue)|"
    r"long[- ]form (?:post|piece)|write.{0,20}\b(?:1,?250|1250)\b.{0,10}word)\b",
    re.IGNORECASE,
)
ARTIFACT_PATTERNS = re.compile(
    r"\b(artifact|html|css|web ?page|landing page|one[- ]pager|"
    r"make me a (?:page|doc|document|checklist|template|table|scorecard)|"
    r"build (?:me )?a (?:page|doc|document|checklist|template|scorecard)|"
    r"create a (?:document|doc|checklist|template|one[- ]pager|scorecard|table)|"
    r"as (?:a )?markdown (?:doc|document|file))\b",
    re.IGNORECASE,
)
KNOWLEDGE_PATTERNS = re.compile(
    r"^(what|who|when|why|how|which|does|do |did |is |are |can |should |tell me|explain|"
    r"summar|compare|according to)",
    re.IGNORECASE,
)

VALID_ROUTES = {"KNOWLEDGE_Q", "SHIP30", "ARTIFACT"}

CLASSIFIER_SYSTEM = """Classify the user's request into exactly one route.

KNOWLEDGE_Q — they want an answer, explanation, comparison, or summary.
SHIP30      — they want a long-form essay / blog post / newsletter piece written.
ARTIFACT    — they want a document or web page produced as a rendered artifact.

When unsure, choose KNOWLEDGE_Q."""

SCHEMA_HINT = '{"route": "KNOWLEDGE_Q|SHIP30|ARTIFACT", "confidence": 0.0-1.0}'


@dataclass(slots=True)
class RouteDecision:
    route: str
    confidence: float
    method: str  # "hint" | "rule" | "llm" | "default"
    rationale: str = ""


def classify_rules(query: str) -> RouteDecision | None:
    if SHIP30_PATTERNS.search(query):
        return RouteDecision("SHIP30", 0.95, "rule", "matched essay pattern")
    if ARTIFACT_PATTERNS.search(query):
        return RouteDecision("ARTIFACT", 0.9, "rule", "matched artifact pattern")
    if KNOWLEDGE_PATTERNS.search(query.strip()) or query.strip().endswith("?"):
        return RouteDecision("KNOWLEDGE_Q", 0.85, "rule", "matched question pattern")
    return None


async def classify(
    query: str,
    *,
    route_hint: str | None = None,
    provider: LLMProvider | None = None,
    use_llm_fallback: bool = True,
) -> RouteDecision:
    if route_hint in VALID_ROUTES:
        decision = RouteDecision(route_hint, 1.0, "hint", "explicit client hint")
        log.info("agent.route_selected", route=decision.route, method=decision.method)
        return decision

    decision = classify_rules(query)
    if decision is None and provider is not None and use_llm_fallback:
        try:
            payload = await provider.structured_output(
                [ChatMessage(role="user", content=query[:1500])],
                schema_hint=SCHEMA_HINT,
                system=CLASSIFIER_SYSTEM,
                temperature=0.0,
                max_tokens=64,
            )
            route = str(payload.get("route", "")).upper() if isinstance(payload, dict) else ""
            if route in VALID_ROUTES:
                confidence = float(payload.get("confidence", 0.6) or 0.6)
                decision = RouteDecision(route, confidence, "llm", "model classification")
        except Exception as exc:
            log.warning("agent.route_llm_failed", error=str(exc)[:200])

    if decision is None:
        decision = RouteDecision("KNOWLEDGE_Q", 0.5, "default", "no rule matched")

    log.info(
        "agent.route_selected",
        route=decision.route,
        method=decision.method,
        confidence=decision.confidence,
    )
    return decision
