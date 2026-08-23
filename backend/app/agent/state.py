"""Turn state.

The controller is a small state machine; this module holds the state it moves
through. Making the state explicit (rather than a pile of locals) buys three
things: every stage boundary is a log line with a phase name, the response can
report exactly which stages ran, and a failure can be attributed to a phase
instead of a stack frame.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.db.models import Memory
from app.schemas.contracts import EvidencePack, GroundingReport


class Phase(str, Enum):
    RECEIVED = "received"
    CLASSIFIED = "classified"
    MEMORY_RETRIEVED = "memory_retrieved"
    EVIDENCE_RETRIEVED = "evidence_retrieved"
    SKILL_EXECUTED = "skill_executed"
    GROUNDED = "grounded"
    PERSISTED = "persisted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class AgentState:
    request_id: str
    session_id: uuid.UUID
    user_id: uuid.UUID
    query: str

    phase: Phase = Phase.RECEIVED
    route: str = "KNOWLEDGE_Q"
    route_method: str = "default"
    route_confidence: float = 0.0

    memories: list[Memory] = field(default_factory=list)
    evidence: EvidencePack | None = None
    answer: str = ""
    grounding: GroundingReport | None = None
    artifact_id: uuid.UUID | None = None

    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    skill_metadata: dict[str, Any] = field(default_factory=dict)

    _started: float = field(default_factory=time.perf_counter)
    _phase_started: float = field(default_factory=time.perf_counter)

    def enter(self, phase: Phase) -> None:
        now = time.perf_counter()
        self.timings[self.phase.value] = round((now - self._phase_started) * 1000, 2)
        self._phase_started = now
        self.phase = phase

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1000, 2)

    def summary(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": str(self.session_id),
            "route": self.route,
            "route_method": self.route_method,
            "phase": self.phase.value,
            "latency_ms": self.elapsed_ms,
            "retrieval_count": len(self.evidence.evidence) if self.evidence else 0,
            "memory_count": len(self.memories),
            "warnings": len(self.warnings),
        }
