"""Skill contract + on-disk skill loading.

Writing standards live in `skills/<name>/SKILL.md`, not in Python string
literals. A content person can improve the Ship 30 rules in a reviewable diff
without touching the agent, and the same file is what an evaluator reads to
understand what the product promises.

Files are read once and cached; `reload_skills()` clears the cache (used by
tests and available for a dev hot-reload).
"""

from __future__ import annotations

import abc
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.db.models import Memory, Message
from app.llm.base import LLMProvider
from app.observability.logging import get_logger
from app.schemas.contracts import EvidencePack

log = get_logger("skills")

SKILLS_DIR = Path(settings.skills_dir)


@dataclass(slots=True)
class SkillDefinition:
    name: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def target_words(self) -> int | None:
        value = self.metadata.get("target_words")
        return int(value) if value else None


@lru_cache(maxsize=16)
def load_skill_file(name: str) -> SkillDefinition:
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        log.warning("skill.file_missing", skill=name, path=str(path))
        return SkillDefinition(name=name, body="")
    text = path.read_text(encoding="utf-8")
    metadata: dict[str, Any] = {}
    body = text
    if text.lstrip().startswith("---"):
        parts = text.lstrip().split("---", 2)
        if len(parts) >= 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                metadata = {}
            body = parts[2]
    return SkillDefinition(name=name, body=body.strip(), metadata=metadata)


def reload_skills() -> None:
    load_skill_file.cache_clear()


EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class SkillContext:
    query: str
    session_id: uuid.UUID
    user_id: uuid.UUID
    history: list[Message]
    memories: list[Memory]
    evidence: EvidencePack
    provider: LLMProvider
    artifact_format: str | None = None
    request_id: str | None = None
    # Streaming is opt-in per turn. A skill that cannot stream usefully
    # (artifact generation, which must be sanitized whole) ignores both.
    stream: bool = False
    emit: EventEmitter | None = None

    async def send(self, event: str, payload: dict[str, Any]) -> None:
        if self.emit is not None:
            await self.emit(event, payload)


@dataclass(slots=True)
class SkillResult:
    text: str
    artifact: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    require_evidence: bool = True


class Skill(abc.ABC):
    name: str = "base"
    route: str = "KNOWLEDGE_Q"

    @abc.abstractmethod
    async def run(self, ctx: SkillContext) -> SkillResult: ...
