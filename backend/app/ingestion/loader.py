"""Transcript loading.

Source of truth for the corpus is the public archive
`https://github.com/ChatPRD/lennys-podcast-transcripts` (303 episodes,
`episodes/<guest-slug>/transcript.md`, YAML frontmatter + speaker-labelled
body). `make transcripts` clones it into `data/transcripts/`.

The loader is format-tolerant on purpose so a client can drop in their own
exports later: it accepts Markdown with or without frontmatter, plain text,
and JSON (single object or list). Nothing here invents content — a file with
no usable text is reported as skipped, never synthesised.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.observability.logging import get_logger

log = get_logger("ingestion.loader")

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".json"}
FRONTMATTER_DELIM = "---"


@dataclass(slots=True)
class RawDocument:
    source_key: str
    title: str
    body: str
    guest: str | None = None
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.lstrip().startswith(FRONTMATTER_DELIM):
        return {}, text
    stripped = text.lstrip()
    parts = stripped.split(FRONTMATTER_DELIM, 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        log.warning("ingestion.frontmatter_unparsed", error=str(exc))
        return {}, parts[2]
    if not isinstance(meta, dict):
        meta = {}
    return meta, parts[2]


def _title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _normalise_meta(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, (str, int, float, bool, list, dict)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def load_file(path: Path, root: Path) -> list[RawDocument]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("ingestion.read_failed", path=str(path), error=str(exc))
        return []

    try:
        source_key = str(path.relative_to(root))
    except ValueError:
        source_key = path.name

    if path.suffix.lower() == ".json":
        return _load_json(text, source_key)

    meta, body = _split_frontmatter(text)
    meta = _normalise_meta(meta)
    if not body.strip():
        return []

    guest = meta.get("guest") or _guest_from_path(path)
    title = meta.get("title") or _title_from_body(body, fallback=path.parent.name)
    url = meta.get("youtube_url") or meta.get("source_url") or meta.get("url")
    return [
        RawDocument(
            source_key=source_key,
            title=str(title).strip(),
            body=body,
            guest=str(guest).strip() if guest else None,
            source_url=str(url) if url else None,
            metadata=meta,
        )
    ]


def _guest_from_path(path: Path) -> str | None:
    folder = path.parent.name
    if not folder or folder in {"transcripts", "episodes", "."}:
        return None
    return folder.replace("-", " ").title()


def _load_json(text: str, source_key: str) -> list[RawDocument]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("ingestion.json_invalid", source_key=source_key, error=str(exc))
        return []
    records = payload if isinstance(payload, list) else [payload]
    documents: list[RawDocument] = []
    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        body = record.get("transcript") or record.get("content") or record.get("body") or ""
        if not str(body).strip():
            continue
        key = source_key if len(records) == 1 else f"{source_key}#{idx}"
        documents.append(
            RawDocument(
                source_key=key,
                title=str(record.get("title") or key),
                body=str(body),
                guest=record.get("guest"),
                source_url=record.get("source_url") or record.get("youtube_url"),
                metadata=_normalise_meta(
                    {k: v for k, v in record.items() if k not in {"transcript", "content", "body"}}
                ),
            )
        )
    return documents


def iter_documents(path: Path, limit: int = 0) -> Iterator[RawDocument]:
    """Yield RawDocuments from a file or a directory tree."""
    path = path.expanduser()
    if not path.exists():
        log.warning("ingestion.path_missing", path=str(path))
        return

    root = path if path.is_dir() else path.parent
    files = (
        sorted(p for p in path.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)
        if path.is_dir()
        else [path]
    )

    emitted = 0
    for file_path in files:
        # Skip the archive's own index/readme scaffolding — it is metadata
        # about the corpus, not transcript evidence.
        parts = {p.lower() for p in file_path.parts}
        if "index" in parts or ".git" in parts or "scripts" in parts:
            continue
        if file_path.name.lower() in {"readme.md", "claude.md", "license.md"}:
            continue
        for document in load_file(file_path, root):
            yield document
            emitted += 1
            if limit and emitted >= limit:
                return
