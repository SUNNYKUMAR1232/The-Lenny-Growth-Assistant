"""Grounding validator tests."""

from __future__ import annotations

from app.grounding.validator import (
    REFUSAL_TEXT,
    split_claims,
    strip_invalid_citations,
    validate,
)
from app.schemas.contracts import EvidenceItem, EvidencePack


def _pack(*texts: str) -> EvidencePack:
    return EvidencePack(
        query="q",
        evidence=[
            EvidenceItem(
                source_id=f"S{i + 1}",
                chunk_id=f"c{i}",
                title="Episode",
                guest="Guest",
                source_url="https://example.com",
                chunk_index=i,
                text=text,
                score=0.8,
            )
            for i, text in enumerate(texts)
        ],
    )


EVIDENCE_TEXT = (
    "Retention is the compounding engine of growth. If your retention curve does "
    "not flatten, acquisition is a leaky bucket and paid spend just makes the leak "
    "more expensive."
)


def test_supported_answer_is_accepted() -> None:
    answer = (
        "Retention is the compounding engine of growth [S1]. When the retention "
        "curve does not flatten, acquisition behaves like a leaky bucket [S1]."
    )
    revised, report = validate(answer, _pack(EVIDENCE_TEXT))
    assert report.action == "accepted"
    assert report.supported_claims == report.checked_claims
    assert revised == answer


def test_unsupported_answer_is_annotated() -> None:
    answer = (
        "Enterprise procurement cycles in regulated banking typically require "
        "vendor security questionnaires and a formal SOC 2 audit trail. "
        "Committee sign-off usually adds another quarter to the sales cycle."
    )
    revised, report = validate(answer, _pack(EVIDENCE_TEXT))
    assert report.action == "annotated"
    assert report.supported_ratio < 0.5
    assert "Grounding note" in revised


def test_empty_evidence_forces_refusal() -> None:
    answer = "Lenny's guests agree that the magic number for activation is seven days."
    revised, report = validate(answer, EvidencePack(query="q", evidence=[]))
    assert report.action == "refused"
    assert revised == REFUSAL_TEXT
    assert report.revised is True


def test_empty_evidence_with_no_claims_is_left_alone() -> None:
    answer = "I don't have transcript evidence for that."
    revised, report = validate(answer, EvidencePack(query="q", evidence=[]))
    assert report.action == "accepted"
    assert revised == answer


def test_hallucinated_citations_are_stripped() -> None:
    answer = "Retention is the compounding engine of growth [S1] [S7]."
    revised, report = validate(answer, _pack(EVIDENCE_TEXT))
    assert "[S7]" not in revised
    assert "[S1]" in revised
    assert report.revised is True


def test_citation_stripping_counts_removals() -> None:
    cleaned, removed = strip_invalid_citations("a [S1] b [S9] c [S12]", {"S1"})
    assert removed == 2
    assert "[S1]" in cleaned and "[S9]" not in cleaned


def test_claim_splitting_ignores_questions_and_headings() -> None:
    claims = split_claims(
        "# Heading\n"
        "What should you measure first?\n"
        "Retention compounds over time and shows up in every downstream metric.\n"
        "- Look at cohorts rather than aggregate numbers when you review the curve."
    )
    assert len(claims) == 2
    assert all(not c.startswith("#") for c in claims)
    assert all(not c.endswith("?") for c in claims)


def test_validator_prefers_the_cited_source() -> None:
    answer = "The retention curve must flatten or acquisition is a leaky bucket [S2]."
    _, report = validate(answer, _pack("Unrelated pricing discussion.", EVIDENCE_TEXT))
    assert report.claims[0].best_source_id == "S2"
    assert report.claims[0].supported


def test_disabled_validator_skips(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "grounding_enabled", False)
    answer = "Anything at all."
    revised, report = validate(answer, _pack(EVIDENCE_TEXT))
    assert report.action == "skipped"
    assert report.enabled is False
    assert revised == answer
