"""Test configuration.

Tests run against a real PostgreSQL + pgvector database, because the parts
most worth testing — the generated tsvector column, `websearch_to_tsquery`,
cosine distance, cascade deletes — are Postgres behaviours that a SQLite
stand-in would not exercise. `TEST_DATABASE_URL` points at a scratch database
(`lenny_test` by default); the schema is created and dropped per session.

The model provider is the deterministic stub and the embedder is the
deterministic hash embedder, so the suite needs no Ollama, no API key, and no
network, and every assertion is about pipeline behaviour rather than model
prose.
"""

from __future__ import annotations

import os

# Must precede any `app.*` import: Settings is built at import time.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/lenny_test",
    ),
)
os.environ["APP_ENV"] = "test"
os.environ["LLM_PROVIDER"] = "stub"
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LOG_FORMAT"] = "console"
os.environ.setdefault("MEMORY_EXTRACT_EVERY_N_TURNS", "1")

import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.database import Base, dispose_engine, get_db, get_engine, get_sessionmaker  # noqa: E402
from app.db.models import Chunk, Document, Session as ChatSession, User  # noqa: E402
from app.embeddings.factory import embed_with_fallback  # noqa: E402
from app.main import create_app  # noqa: E402

pytest_plugins = ("pytest_asyncio",)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Dispose first: a cancelled streaming request can leave a connection
    # holding locks, which would make DROP TABLE wait out the statement timeout.
    await dispose_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await dispose_engine()


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    maker = get_sessionmaker()
    async with maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(db: AsyncSession) -> AsyncIterator[None]:
    """Each test starts from an empty transactional state."""
    await db.execute(text("DELETE FROM users"))  # cascades to sessions/messages/artifacts/memories
    await db.execute(text("DELETE FROM documents"))  # cascades to chunks
    await db.commit()
    yield


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()

    maker = get_sessionmaker()

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    row = User(external_id=f"user-{uuid.uuid4()}", display_name="Test User", meta={})
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest_asyncio.fixture
async def chat_session(db: AsyncSession, user: User) -> ChatSession:
    row = ChatSession(user_id=user.id, title="Test session", meta={})
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


CORPUS = [
    (
        "onboarding-episode",
        "How to build a high-performing growth team",
        "Adam Fishman",
        [
            "Onboarding is the only part of your product experience that a hundred "
            "percent of people are ever going to touch. Good luck getting a hundred "
            "percent feature adoption of anything else in your product.",
            "Your brand is the promise you make in the marketplace and your product "
            "experience is the delivery of that promise. Those two things have to be "
            "in lockstep or you get mismatched expectations.",
        ],
    ),
    (
        "pmf-episode",
        "How to know if you have product-market fit",
        "Rahul Vohra",
        [
            "We used the Sean Ellis product-market fit survey: ask users how they "
            "would feel if they could no longer use the product, and track the "
            "percentage who say very disappointed. Forty percent is the benchmark.",
            "We segmented the very disappointed users, found what they had in common, "
            "and doubled down on that segment instead of averaging across everyone.",
        ],
    ),
    (
        "retention-episode",
        "Why retention is the only growth metric that matters",
        "Casey Winters",
        [
            "Retention is the compounding engine of growth. If your retention curve "
            "does not flatten, acquisition is a leaky bucket and paid spend just "
            "makes the leak more expensive.",
            "Look at the retention curve by cohort and by use case, not in aggregate. "
            "Aggregate retention hides the one segment that actually loves you.",
        ],
    ),
]


@pytest_asyncio.fixture
async def corpus(db: AsyncSession) -> list[Document]:
    """A small, real-shaped corpus with embeddings, seeded per test."""
    await db.execute(text("DELETE FROM documents"))  # cascades to chunks
    await db.commit()

    documents: list[Document] = []
    for key, title, guest, chunks in CORPUS:
        document = Document(
            source_key=key,
            title=title,
            guest=guest,
            source_url=f"https://www.youtube.com/watch?v={key}",
            content="\n".join(chunks),
            content_hash=key,
            meta={},
        )
        db.add(document)
        await db.flush()
        vectors = (await embed_with_fallback(chunks)).vectors
        for index, (chunk_text, vector) in enumerate(zip(chunks, vectors)):
            db.add(
                Chunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=chunk_text,
                    token_estimate=len(chunk_text.split()),
                    embedding=vector,
                    embedding_model="deterministic-hash-768",
                    meta={
                        "title": title,
                        "guest": guest,
                        "start_seconds": index * 120,
                        "deep_link": f"https://www.youtube.com/watch?v={key}&t={index * 120}s",
                    },
                )
            )
        documents.append(document)
    await db.commit()
    return documents


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
