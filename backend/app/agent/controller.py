"""Controlled agent controller.

    classify → retrieve context → select skill → execute → validate → persist

This is a *controller*, not an autonomous agent. It does not decide its own
plan, it cannot call arbitrary tools, and it cannot loop. Every turn walks the
same fixed pipeline with a bounded set of skills. The trade-off is deliberate
and documented in docs/architecture.md#why-a-controlled-agent: deterministic
routing gives predictable latency, unit-testable stages, log lines that map
one-to-one onto phases, and no chance of a runaway tool loop in front of a
customer.

Failure policy per stage:
  memory retrieval   -> degrade to [] and warn; chat continues
  evidence retrieval -> typed RETRIEVAL_UNAVAILABLE (the product's core promise
                        is grounding; answering ungrounded would be worse)
  skill/LLM          -> typed MODEL_* error; the user message is already saved
  memory extraction  -> swallowed and warned; never fails a turn
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.router import classify
from app.agent.state import AgentState, Phase
from app.config import settings
from app.db.models import Artifact, Memory, Message, Session as ChatSession
from app.errors import AppError, DatabaseError
from app.grounding.validator import validate
from app.llm.factory import get_provider
from app.memory import manager as memory_manager
from app.memory.extractor import extract_memories
from app.memory.retriever import retrieve_memories
from app.observability.logging import get_logger, set_request_context
from app.retrieval.evidence import retrieve_evidence
from app.schemas.contracts import (
    ArtifactResponse,
    ChatResponse,
    EvidencePack,
    GroundingReport,
    MemoryUsed,
    MessageResponse,
    ModelInfo,
)
from app.skills.artifact import ArtifactSkill
from app.skills.base import EventEmitter, SkillContext, SkillResult
from app.skills.rag import RAGSkill
from app.skills.ship30 import Ship30Skill

log = get_logger("agent.controller")

SKILLS = {
    "KNOWLEDGE_Q": RAGSkill(),
    "SHIP30": Ship30Skill(),
    "ARTIFACT": ArtifactSkill(),
}

# Essays and artifacts need more raw material than a direct question does.
TOP_K_BY_ROUTE = {"KNOWLEDGE_Q": None, "SHIP30": 10, "ARTIFACT": 6}


def model_info(available: bool = True, detail: str | None = None) -> ModelInfo:
    provider = get_provider()
    return ModelInfo(
        provider=settings.llm_provider,  # type: ignore[arg-type]
        model=provider.model,
        label=provider.label(),
        cloud_provider=settings.cloud_provider if settings.llm_provider == "cloud" else None,
        embedding_provider=settings.embedding_provider,
        embedding_model=(
            settings.ollama_embedding_model
            if settings.embedding_provider == "ollama"
            else settings.embedding_provider
        ),
        available=available,
        detail=detail,
    )


async def _load_history(session: AsyncSession, session_id: uuid.UUID) -> list[Message]:
    rows = (
        await session.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
    ).scalars()
    return list(rows)


async def handle_turn(
    db: AsyncSession,
    chat_session: ChatSession,
    content: str,
    *,
    request_id: str,
    route_hint: str | None = None,
    artifact_format: str | None = None,
    stream: bool = False,
    on_event: EventEmitter | None = None,
) -> ChatResponse:
    """Run one full turn.

    `on_event` receives progress events (`route`, `retrieval`, `token`, ...)
    for the SSE endpoint. The non-streaming JSON endpoint passes None and the
    pipeline is otherwise identical — one code path, two transports.
    """
    state = AgentState(
        request_id=request_id,
        session_id=chat_session.id,
        user_id=chat_session.user_id,
        query=content,
    )
    set_request_context(request_id=request_id, session_id=str(chat_session.id))
    provider = get_provider()

    async def emit(event: str, payload: dict) -> None:
        if on_event is not None:
            await on_event(event, payload)

    # ---------------------------------------------------------- user message
    history = await _load_history(db, chat_session.id)
    user_message = Message(session_id=chat_session.id, role="user", content=content, meta={})
    db.add(user_message)
    try:
        await db.commit()
        await db.refresh(user_message)
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("database.error", stage="persist_user_message", error=str(exc))
        raise DatabaseError() from exc

    # ------------------------------------------------------------- classify
    decision = await classify(content, route_hint=route_hint, provider=provider)
    state.route = decision.route
    state.route_method = decision.method
    state.route_confidence = decision.confidence
    state.enter(Phase.CLASSIFIED)
    await emit(
        "route",
        {
            "route": decision.route,
            "method": decision.method,
            "confidence": decision.confidence,
            "model": provider.label(),
        },
    )

    # ---------------------------------------------------------------- memory
    memories: list[Memory] = []
    if settings.memory_enabled:
        try:
            memories = await retrieve_memories(db, chat_session.user_id, content)
        except Exception as exc:  # memory must never break the assistant
            log.warning("memory.unavailable", error=str(exc)[:200])
            state.warn("Personalization is temporarily unavailable.")
    state.memories = memories
    state.enter(Phase.MEMORY_RETRIEVED)
    await emit("memory", {"count": len(memories), "keys": [m.key for m in memories]})

    # -------------------------------------------------------------- evidence
    await emit("retrieval", {"status": "started"})
    evidence: EvidencePack = await retrieve_evidence(
        db, content, route=state.route, top_k=TOP_K_BY_ROUTE.get(state.route)
    )
    state.evidence = evidence
    if evidence.degraded and evidence.degraded_reason:
        state.warn(evidence.degraded_reason)
    if evidence.is_empty:
        state.warn("No transcript evidence matched this question.")
    state.enter(Phase.EVIDENCE_RETRIEVED)
    await emit(
        "evidence",
        {
            "status": "completed",
            "count": len(evidence.evidence),
            "strategy": evidence.strategy,
            "latency_ms": evidence.latency_ms,
            "degraded": evidence.degraded,
            "items": [item.model_dump() for item in evidence.evidence],
        },
    )

    # ----------------------------------------------------------------- skill
    skill = SKILLS[state.route]
    ctx = SkillContext(
        query=content,
        session_id=chat_session.id,
        user_id=chat_session.user_id,
        history=history,
        memories=memories,
        evidence=evidence,
        provider=provider,
        artifact_format=artifact_format,
        request_id=request_id,
        stream=stream,
        emit=on_event,
    )
    result: SkillResult = await skill.run(ctx)
    state.skill_metadata = result.metadata
    state.enter(Phase.SKILL_EXECUTED)

    # ------------------------------------------------------------- grounding
    answer = result.text
    grounding = GroundingReport(action="skipped", enabled=settings.grounding_enabled)
    if settings.grounding_enabled and result.metadata.get("llm_called"):
        # For artifacts the chat message is a pointer; the artifact body is
        # what carries claims, so that is what gets validated.
        if result.artifact and state.route == "ARTIFACT":
            _, grounding = validate(
                result.artifact["content"], evidence, require_evidence=False
            )
        else:
            answer, grounding = validate(
                answer, evidence, require_evidence=result.require_evidence
            )
    state.answer = answer
    state.grounding = grounding
    if grounding.action == "refused":
        state.warn("The answer was replaced because it was not supported by evidence.")
    elif grounding.action == "annotated":
        state.warn("Some claims in this answer are only weakly supported by the evidence.")
    state.enter(Phase.GROUNDED)

    # ------------------------------------------------------------- persist
    assistant_meta = {
        "route": state.route,
        "route_method": state.route_method,
        "route_confidence": state.route_confidence,
        "provider": provider.name,
        "model": provider.model,
        "model_label": provider.label(),
        "evidence": [item.model_dump() for item in evidence.evidence],
        "evidence_strategy": evidence.strategy,
        "retrieval_latency_ms": evidence.latency_ms,
        "grounding": grounding.model_dump(exclude={"claims"}),
        "memories_used": [
            {"id": str(m.id), "key": m.key, "type": m.type} for m in memories
        ],
        "skill": result.metadata,
        "warnings": state.warnings,
        "request_id": request_id,
    }
    assistant_message = Message(
        session_id=chat_session.id, role="assistant", content=answer, meta=assistant_meta
    )
    db.add(assistant_message)

    artifact_row: Artifact | None = None
    if result.artifact:
        artifact_row = Artifact(
            session_id=chat_session.id,
            type=result.artifact["type"],
            title=result.artifact.get("title", "Artifact")[:512],
            content=result.artifact["content"],
            raw_content=result.artifact.get("raw_content"),
            meta=result.artifact.get("metadata", {}),
        )
        db.add(artifact_row)

    # Title a fresh session from its first question, so the sidebar is scannable.
    if chat_session.title in {"New chat", ""} and not history:
        chat_session.title = content.strip().replace("\n", " ")[:80]

    try:
        await db.flush()
        if artifact_row is not None:
            artifact_row.message_id = assistant_message.id
            assistant_message.meta = {
                **assistant_meta,
                "artifact_id": str(artifact_row.id),
            }
        await db.commit()
        await db.refresh(assistant_message)
        if artifact_row is not None:
            await db.refresh(artifact_row)
            state.artifact_id = artifact_row.id
    except SQLAlchemyError as exc:
        await db.rollback()
        log.error("database.error", stage="persist_assistant_message", error=str(exc))
        raise DatabaseError() from exc
    state.enter(Phase.PERSISTED)

    # ------------------------------------------------- memory extraction
    if settings.memory_enabled:
        await _maybe_extract_memories(db, state, history, content, answer, provider)

    state.enter(Phase.COMPLETED)
    log.info("request.completed", **state.summary())

    return ChatResponse(
        session_id=chat_session.id,
        request_id=request_id,
        user_message=MessageResponse.model_validate(user_message),
        message=MessageResponse.model_validate(assistant_message),
        route=state.route,  # type: ignore[arg-type]
        evidence=evidence.evidence,
        memories_used=[
            MemoryUsed(
                id=m.id,
                type=m.type,  # type: ignore[arg-type]
                key=m.key,
                value=m.value,
                confidence=m.confidence,
                importance=m.importance,
            )
            for m in memories
        ],
        grounding=grounding,
        artifact=(
            ArtifactResponse.model_validate(artifact_row) if artifact_row else None
        ),
        model=model_info(),
        latency_ms=state.elapsed_ms,
        warnings=state.warnings,
    )


async def _maybe_extract_memories(
    db: AsyncSession,
    state: AgentState,
    history: list[Message],
    query: str,
    answer: str,
    provider,
) -> None:
    """Extraction runs every N turns, not every turn (MEMORY_EXTRACT_EVERY_N_TURNS)."""
    turn_index = len([m for m in history if m.role == "user"]) + 1
    if turn_index % max(1, settings.memory_extract_every_n_turns) != 0:
        return
    try:
        conversation = [(m.role, m.content) for m in history[-6:]]
        conversation.append(("user", query))
        conversation.append(("assistant", answer[:1500]))
        candidates = await extract_memories(provider, conversation)
        if candidates:
            await memory_manager.store_candidates(
                db, state.user_id, candidates, source_session_id=state.session_id
            )
            await db.commit()
    except AppError as exc:
        await db.rollback()
        log.warning("memory.store_failed", code=exc.code, error=exc.message)
        state.warn("Could not update personalization for this turn.")
    except Exception as exc:
        await db.rollback()
        log.warning("memory.store_failed", error=str(exc)[:200])
        state.warn("Could not update personalization for this turn.")
