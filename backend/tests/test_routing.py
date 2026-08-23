"""Router tests: deterministic rules, hints, and the LLM tie-breaker."""

from __future__ import annotations

import pytest

from app.agent.router import classify, classify_rules
from app.llm.stub import StubProvider


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What is product-market fit?", "KNOWLEDGE_Q"),
        ("How do the best teams run onboarding?", "KNOWLEDGE_Q"),
        ("Compare Casey and Rahul on retention", "KNOWLEDGE_Q"),
        ("Write a Ship 30 for 30 essay about activation", "SHIP30"),
        ("Turn that into a 1,250 word essay", "SHIP30"),
        ("Draft a blog post on retention curves", "SHIP30"),
        ("Build me an HTML landing page for this", "ARTIFACT"),
        ("Create a checklist document I can share", "ARTIFACT"),
        ("Make me a one-pager", "ARTIFACT"),
    ],
)
async def test_rule_routing(query: str, expected: str) -> None:
    decision = await classify(query, provider=StubProvider())
    assert decision.route == expected
    assert decision.method in {"rule", "llm", "default"}


async def test_explicit_hint_wins_over_rules() -> None:
    decision = await classify("What is retention?", route_hint="ARTIFACT")
    assert decision.route == "ARTIFACT"
    assert decision.method == "hint"
    assert decision.confidence == 1.0


async def test_invalid_hint_is_ignored() -> None:
    decision = await classify("What is retention?", route_hint="NONSENSE")
    assert decision.route == "KNOWLEDGE_Q"
    assert decision.method != "hint"


def test_essay_beats_artifact_when_both_match() -> None:
    decision = classify_rules("Write an essay I can publish as a landing page")
    assert decision is not None
    assert decision.route == "SHIP30"


async def test_unclassifiable_input_defaults_to_knowledge(monkeypatch) -> None:
    class Broken(StubProvider):
        async def structured_output(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("classifier unavailable")

    decision = await classify("retention", provider=Broken())
    assert decision.route == "KNOWLEDGE_Q"
    assert decision.method == "default"


async def test_router_does_not_call_model_when_a_rule_matches() -> None:
    calls: list[str] = []

    class Counting(StubProvider):
        async def structured_output(self, *args, **kwargs):  # type: ignore[override]
            calls.append("called")
            return {"route": "ARTIFACT"}

    decision = await classify("What is retention?", provider=Counting())
    assert decision.route == "KNOWLEDGE_Q"
    assert calls == []
