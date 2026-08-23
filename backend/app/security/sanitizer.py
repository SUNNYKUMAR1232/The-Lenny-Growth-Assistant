"""Artifact sanitization — treat every byte of generated HTML as hostile.

Threat model: the HTML in an artifact is written by a language model, which is
steered by user text and by transcript text we do not control. Assume it can
contain anything an attacker could get a model to emit.

Defence is layered; this module is layer 1 (server-side), and the sandboxed
iframe in the frontend is layer 2. Neither is trusted to be sufficient alone.

Layer 1 removes, before anything is stored:
  * <script>, <iframe>, <object>, <embed>, <link>, <meta>, <base>, <form>,
    <input>, <button type=submit>, <svg>, <math>
  * every `on*` event-handler attribute
  * `javascript:`, `vbscript:`, `data:text/html` URLs
  * remote resource references — images must be `data:image/*`, so a rendered
    artifact cannot beacon to a third party
  * CSS `@import`, `url()` with a non-data scheme, `expression()`, `behavior:`

What is deliberately allowed: structural and text elements, tables, lists,
inline styles, and `<style>` blocks — because "generate an HTML/CSS artifact"
is a product requirement and CSS is most of what makes an artifact useful.

The stored `content` is the sanitized output. The original model output is
kept in `raw_content` for debugging and is never rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import bleach
from bleach.css_sanitizer import ALLOWED_CSS_PROPERTIES, CSSSanitizer

from app.config import settings
from app.errors import SanitizationError
from app.observability.logging import get_logger

log = get_logger("security.sanitizer")

ALLOWED_TAGS = {
    "a", "abbr", "article", "aside", "b", "blockquote", "br", "caption", "code",
    "col", "colgroup", "dd", "details", "div", "dl", "dt", "em", "figcaption",
    "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i",
    "img", "li", "main", "mark", "nav", "ol", "p", "pre", "section", "small",
    "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot",
    "th", "thead", "time", "tr", "u", "ul", "style",
}

ALLOWED_ATTRIBUTES = {
    "*": ["class", "id", "style", "title", "role", "aria-label", "aria-hidden", "lang"],
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "width", "height", "loading"],
    "td": ["colspan", "rowspan", "align"],
    "th": ["colspan", "rowspan", "align", "scope"],
    "time": ["datetime"],
    "col": ["span"],
}

ALLOWED_PROTOCOLS = {"http", "https", "mailto"}

# bleach's default CSS allowlist predates flexbox and grid, which would make
# every generated layout collapse. We extend it with modern layout/typography
# properties; values are still parsed and filtered by tinycss2, and `url()`,
# `expression()` and `@import` are removed separately.
EXTRA_CSS_PROPERTIES = {
    "align-items", "align-content", "align-self", "justify-content",
    "justify-items", "justify-self", "flex", "flex-basis", "flex-direction",
    "flex-flow", "flex-grow", "flex-shrink", "flex-wrap", "gap", "row-gap",
    "column-gap", "grid", "grid-area", "grid-auto-columns", "grid-auto-flow",
    "grid-auto-rows", "grid-column", "grid-row", "grid-template",
    "grid-template-areas", "grid-template-columns", "grid-template-rows",
    "border", "border-radius", "border-style", "border-width", "border-top",
    "border-right", "border-bottom", "border-left", "box-shadow", "box-sizing",
    "opacity", "overflow", "overflow-x", "overflow-y", "position", "top",
    "right", "bottom", "left", "z-index", "max-width", "min-width",
    "max-height", "min-height", "object-fit", "transform", "transition",
    "font", "font-feature-settings", "letter-spacing", "word-spacing",
    "text-transform", "text-overflow", "white-space", "line-height",
    "background", "background-image", "background-size", "background-position",
    "background-repeat", "background-clip", "color-scheme", "filter",
    "list-style", "list-style-type", "outline", "aspect-ratio", "columns",
}

CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=sorted(set(ALLOWED_CSS_PROPERTIES) | EXTRA_CSS_PROPERTIES)
)

_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"```(?:html|css)?\s*(.*?)```", re.DOTALL)
_DANGEROUS_CSS = re.compile(
    r"(@import\b|expression\s*\(|behavior\s*:|-moz-binding|javascript\s*:|"
    r"@charset\b|<\s*/?\s*script)",
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.IGNORECASE)
_EVENT_ATTR_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)

# `frame-ancestors` is deliberately absent: it is ignored when a CSP is
# delivered in a <meta> tag, and browsers log a console warning for it. The
# iframe's `sandbox` attribute is what actually confines this document.
CSP = (
    "default-src 'none'; "
    "img-src data:; "
    "style-src 'unsafe-inline'; "
    "font-src data:; "
    "script-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'"
)


@dataclass(slots=True)
class SanitizationReport:
    removed_style_declarations: int = 0
    removed_urls: list[str] = field(default_factory=list)
    stripped_bytes: int = 0
    had_script: bool = False
    had_event_handlers: bool = False

    def as_dict(self) -> dict:
        return {
            "removed_style_declarations": self.removed_style_declarations,
            "removed_urls": self.removed_urls[:10],
            "stripped_bytes": self.stripped_bytes,
            "had_script": self.had_script,
            "had_event_handlers": self.had_event_handlers,
        }


def _safe_url(value: str) -> bool:
    lowered = value.strip().lower().replace("\n", "").replace("\t", "")
    if lowered.startswith("data:image/") and "script" not in lowered:
        return True
    if lowered.startswith("#") or lowered.startswith("/"):
        return True
    scheme = lowered.split(":", 1)[0] if ":" in lowered.split("/", 1)[0] else ""
    if not scheme:
        return True  # relative
    return scheme in ALLOWED_PROTOCOLS


def sanitize_css(css: str, report: SanitizationReport) -> str:
    cleaned, removals = _DANGEROUS_CSS.subn("/*removed*/", css)
    report.removed_style_declarations += removals

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(1)
        if url.lower().startswith("data:image/"):
            return match.group(0)
        report.removed_urls.append(url[:120])
        return "none"

    cleaned = _CSS_URL_RE.sub(replace_url, cleaned)
    return cleaned


def _attribute_filter(tag: str, name: str, value: str) -> bool:
    if name.startswith("on"):
        return False
    if tag == "img" and name == "src":
        # Images must be inlined. A remote image URL is a tracking pixel with
        # extra steps: it would leak the viewer's IP and referrer to whatever
        # host the model happened to write down.
        return value.strip().lower().startswith("data:image/")
    if name in {"href", "src", "action", "formaction", "xlink:href"}:
        return _safe_url(value)
    if name == "style":
        return not _DANGEROUS_CSS.search(value)
    allowed = set(ALLOWED_ATTRIBUTES.get("*", [])) | set(ALLOWED_ATTRIBUTES.get(tag, []))
    return name in allowed


def extract_html(raw: str) -> str:
    """Pull HTML out of a fenced block if the model wrapped it in Markdown."""
    match = _FENCE_RE.search(raw)
    candidate = match.group(1) if match else raw
    return candidate.strip()


def sanitize_html(raw: str) -> tuple[str, SanitizationReport]:
    if raw is None:
        raise SanitizationError("No HTML content was produced.")
    source = extract_html(raw)
    if len(source.encode("utf-8")) > settings.artifact_max_bytes:
        raise SanitizationError(
            "The generated artifact is too large to render safely.",
            details={"max_bytes": settings.artifact_max_bytes},
        )

    report = SanitizationReport()
    report.had_script = bool(re.search(r"<\s*script", source, re.IGNORECASE))
    report.had_event_handlers = bool(_EVENT_ATTR_RE.search(source))

    # <style> content is extracted, CSS-sanitized, and re-inserted, because
    # bleach validates attributes and tags but not stylesheet text.
    styles: list[str] = []

    def stash_style(match: re.Match[str]) -> str:
        styles.append(sanitize_css(match.group(1), report))
        return f"<!--STYLE_BLOCK_{len(styles) - 1}-->"

    body = _STYLE_BLOCK_RE.sub(stash_style, source)

    try:
        cleaned = bleach.clean(
            body,
            tags=ALLOWED_TAGS - {"style"},
            attributes=_attribute_filter,
            protocols=sorted(ALLOWED_PROTOCOLS | {"data"}),
            strip=True,
            strip_comments=False,
            css_sanitizer=CSS_SANITIZER,
        )
    except Exception as exc:  # pragma: no cover - bleach is defensive already
        log.error("artifact.sanitization_failed", error=str(exc))
        raise SanitizationError() from exc

    for index, css in enumerate(styles):
        cleaned = cleaned.replace(
            f"<!--STYLE_BLOCK_{index}-->", f"<style>{css}</style>"
        )
    cleaned = re.sub(r"<!--(?!STYLE_BLOCK_)(.*?)-->", "", cleaned, flags=re.DOTALL)

    report.stripped_bytes = max(0, len(source) - len(cleaned))

    log.info("artifact.sanitized", **report.as_dict())
    return cleaned, report


def wrap_document(cleaned_html: str, title: str = "Artifact") -> str:
    """Wrap sanitized fragments in a minimal, CSP-locked document.

    The viewer renders this string inside a sandboxed iframe via `srcdoc`, so
    the CSP here is the in-document half of the isolation story.
    """
    safe_title = bleach.clean(title, tags=set(), strip=True)[:200]
    lowered = cleaned_html.lower()
    if "<html" in lowered:
        return cleaned_html
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        f"<title>{safe_title}</title>\n"
        "<style>:root{color-scheme:light dark}"
        "body{margin:0;padding:24px;font-family:ui-sans-serif,system-ui,-apple-system,"
        "'Segoe UI',Roboto,Helvetica,Arial,sans-serif;line-height:1.6;"
        "background:#fff;color:#18181b}"
        "img{max-width:100%;height:auto}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #e4e4e7;padding:8px}"
        "</style>\n"
        "</head>\n<body>\n"
        f"{cleaned_html}\n"
        "</body>\n</html>"
    )


_MD_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|iframe|object|embed|style|form)\b.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_MD_DANGEROUS_TAG_RE = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|form|input|link|meta|base)\b[^>]*>",
    re.IGNORECASE,
)
_MD_JS_LINK_RE = re.compile(r"\]\(\s*(javascript|vbscript|data:text/html)[^)]*\)", re.IGNORECASE)


def sanitize_markdown(raw: str) -> str:
    """Markdown artifacts.

    The client renders Markdown with raw HTML disabled (no `rehype-raw`), so
    embedded HTML is displayed as text rather than executed. We still strip the
    genuinely dangerous constructs server-side — defence in depth for any other
    renderer that might open a stored artifact — but we do NOT run the HTML
    sanitizer over Markdown, because entity-escaping would corrupt legitimate
    prose (`a < b`, code samples, comparison tables).
    """
    if len(raw.encode("utf-8")) > settings.artifact_max_bytes:
        raise SanitizationError(
            "The generated artifact is too large to render safely.",
            details={"max_bytes": settings.artifact_max_bytes},
        )
    cleaned = _MD_DANGEROUS_BLOCK_RE.sub("", raw)
    cleaned = _MD_DANGEROUS_TAG_RE.sub("", cleaned)
    cleaned = _EVENT_ATTR_RE.sub(" data-removed=", cleaned)
    cleaned = _MD_JS_LINK_RE.sub("](#removed)", cleaned)
    return cleaned
