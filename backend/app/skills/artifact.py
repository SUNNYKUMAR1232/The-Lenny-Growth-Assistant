"""Artifact skill — generate a Markdown document or an HTML/CSS artifact.

Format selection is deterministic: an explicit `artifact_format` from the
client wins, otherwise keyword rules over the request ("web page", "landing
page", "styled" -> HTML; everything else -> Markdown). Asking the model to
choose would add a round trip and a failure mode for no benefit.

The model's output is untrusted from the moment it arrives: it is sanitized
here, before persistence, and the sanitized copy is what the viewer renders.
The raw output is kept alongside it for debugging only.
"""

from __future__ import annotations

import re
import time

from app.agent.context_builder import build_context
from app.errors import LLMError, SanitizationError
from app.observability.logging import get_logger
from app.security.sanitizer import sanitize_html, sanitize_markdown, wrap_document
from app.skills.base import Skill, SkillContext, SkillResult, load_skill_file

log = get_logger("skills.artifact")

HTML_HINTS = re.compile(
    r"\b(html|web ?page|landing page|css|styled|stylesheet|one[- ]pager|"
    r"poster|dashboard|scorecard|card|visual|mock ?up|site)\b",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>|^#\s+(.+)$", re.IGNORECASE | re.MULTILINE)


def choose_format(query: str, requested: str | None) -> str:
    if requested in {"markdown", "html"}:
        return requested
    return "html" if HTML_HINTS.search(query) else "markdown"


def _title(content: str, fallback: str) -> str:
    match = TITLE_RE.search(content)
    if match:
        raw = match.group(1) or match.group(2) or ""
        cleaned = re.sub(r"<[^>]+>", "", raw).strip()
        if cleaned:
            return cleaned[:200]
    return fallback[:200]


class ArtifactSkill(Skill):
    name = "artifact"
    route = "ARTIFACT"

    async def run(self, ctx: SkillContext) -> SkillResult:
        skill = load_skill_file("artifact")
        fmt = choose_format(ctx.query, ctx.artifact_format)

        instructions = (
            f"TASK: Produce a complete {'HTML/CSS artifact' if fmt == 'html' else 'Markdown document'} "
            "based on the conversation and the evidence.\n"
            "Return ONLY the artifact itself. No commentary before or after.\n\n"
            f"--- ARTIFACT STANDARD ---\n{skill.body}"
        )

        context = build_context(
            query=ctx.query,
            history=ctx.history,
            memories=ctx.memories,
            evidence=ctx.evidence,
            skill_instructions=instructions,
        )

        started = time.perf_counter()
        log.info("llm.started", skill=self.name, artifact_type=fmt)
        try:
            response = await ctx.provider.generate(
                context.messages, system=context.system, temperature=0.4, max_tokens=4000
            )
        except LLMError as exc:
            log.error("llm.failed", skill=self.name, code=exc.code, error=exc.message)
            raise

        raw = response.text.strip()
        latency = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "llm.completed",
            skill=self.name,
            provider=response.provider,
            model=response.model,
            latency_ms=latency,
            chars=len(raw),
        )

        if not raw:
            raise SanitizationError("The model returned an empty artifact.")

        if fmt == "html":
            cleaned, report = sanitize_html(raw)
            if not cleaned.strip():
                raise SanitizationError(
                    "Everything in the generated HTML was removed by the sanitizer, "
                    "so there is nothing safe to render."
                )
            title = _title(cleaned, fallback=ctx.query)
            content = wrap_document(cleaned, title=title)
            sanitization = report.as_dict()
        else:
            content = sanitize_markdown(raw)
            title = _title(content, fallback=ctx.query)
            sanitization = {"markdown_html_stripped": len(raw) != len(content)}

        log.info("artifact.generated", artifact_type=fmt, title=title, chars=len(content))

        message = (
            f"I've generated a {'styled HTML' if fmt == 'html' else 'Markdown'} artifact — "
            f"**{title}** — it's open in the Artifact Viewer."
        )
        if fmt == "html":
            message += (
                "\n\nIt renders in a sandboxed iframe with scripts disabled, so it's "
                "safe to view; use Download to keep a copy."
            )

        return SkillResult(
            text=message,
            artifact={
                "type": fmt,
                "title": title,
                "content": content,
                "raw_content": raw,
                "metadata": {
                    "sanitization": sanitization,
                    "evidence_used": context.evidence_tags,
                    "model": response.model,
                    "provider": response.provider,
                },
            },
            metadata={
                "skill": self.name,
                "llm_called": True,
                "artifact_type": fmt,
                "latency_ms": latency,
                "sanitization": sanitization,
            },
            # The chat message here is a one-line pointer, not a claim about
            # the transcripts; grounding is validated on the artifact's own
            # content instead (see controller).
            require_evidence=False,
        )
