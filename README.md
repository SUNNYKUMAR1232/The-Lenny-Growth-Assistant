# The Lenny Growth Assistant

A controlled AI knowledge assistant over Lenny's Podcast transcripts: **evidence-grounded
retrieval, persistent user context, specialized skills, model flexibility, and secure
artifact generation.**

Ask a product or growth question and get an answer built only from what guests actually
said, with citations that deep-link to the second of the episode. Ask for a Ship 30 for 30
essay and get ~1,250 words written to a reusable writing standard. Ask for a document or an
HTML one-pager and it renders in a sandboxed viewer beside the chat.

```
┌───────────────────────────────────────────────────────────────────────┐
│ Lenny Growth Assistant                     ● ollama/llama3.1:8b       │
├──────────────┬──────────────────────────────┬─────────────────────────┤
│              │  You: How do I know if I     │  Artifact Viewer        │
│  New chat    │  have product-market fit?    │                         │
│              │                              │  ┌───────────────────┐  │
│  Sessions    │  [Grounded answer]           │  │  Rendered in a    │  │
│  · PMF       │  grounded 6/6 · 41ms         │  │  sandboxed iframe │  │
│  · Retention │                              │  │  (no scripts,     │  │
│              │  Rahul Vohra used the Sean   │  │   no network)     │  │
│              │  Ellis survey… [S1]          │  └───────────────────┘  │
│              │                              │                         │
│  8 episodes  │  › 6 sources from 3 episodes │  Preview | Source | ⤓   │
│  578 chunks  │                              │                         │
└──────────────┴──────────────────────────────┴─────────────────────────┘
```

Screenshots of the running app — empty state, a grounded answer, expanded sources with
episode deep links, and an artifact in the sandboxed viewer — are in
[`docs/images/`](docs/images/).

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Quick start (Docker)](#4-quick-start-docker)
5. [Quick start (local, no Docker)](#5-quick-start-local-no-docker)
6. [Transcript setup](#6-transcript-setup)
7. [Model configuration](#7-model-configuration)
8. [Environment variables](#8-environment-variables)
9. [Development commands](#9-development-commands)
10. [Tests](#10-tests)
11. [API reference](#11-api-reference)
12. [Observability](#12-observability)
13. [Troubleshooting](#13-troubleshooting)
14. [Security notes](#14-security-notes)
15. [Architecture trade-offs](#15-architecture-trade-offs)
16. [What is not built](#16-what-is-not-built)
17. [Repository map](#17-repository-map)

---

## 1. What it does

| Capability | How it works |
|---|---|
| **Grounded Q&A** | Hybrid retrieval (pgvector + Postgres FTS) → Evidence Pack → answer with `[S#]` citations → grounding validation |
| **Session memory** | Each chat is an independent session; history, evidence, routing and grounding verdicts are persisted in PostgreSQL |
| **Personal memory** | Durable facts about the user are extracted, filtered by confidence/importance, retrieved per query — and never treated as evidence |
| **Ship 30 essays** | A dedicated skill file (`skills/ship30/SKILL.md`) encodes the writing standard; the agent enforces length and grounding |
| **Artifacts** | Markdown and complete HTML/CSS documents, sanitized server-side and rendered in a fully sandboxed iframe |
| **Model flexibility** | `LLM_PROVIDER=ollama\|cloud` — local Ollama or Anthropic/OpenAI, no code change, provider always visible in the UI |
| **Operability** | One-command startup, structured JSON logs, dependency-level health checks, typed errors, 119 backend + 15 frontend tests |

---

## 2. Architecture

```
                    ┌──────────────────────────────┐
                    │         Next.js UI           │
                    │  chat · sources · artifacts  │
                    │  memory · model indicator    │
                    └──────────────┬───────────────┘
                             REST + SSE
                                   ▼
                    ┌──────────────────────────────┐
                    │           FastAPI            │
                    │ sessions · chat · artifacts  │
                    │ memory · ingestion · health  │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │   Controlled Agent Controller│
                    │ classify → retrieve → execute│
                    │        → validate            │
                    └──────────────┬───────────────┘
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        ┌───────────┐       ┌────────────┐       ┌────────────┐
        │ RAG skill │       │ Ship30     │       │ Artifact   │
        │           │       │ skill      │       │ skill      │
        └─────┬─────┘       └─────┬──────┘       └─────┬──────┘
              └───────────────────┼────────────────────┘
                                  ▼
                    ┌──────────────────────────────┐
                    │      Retrieval Engine        │
                    │ vector · keyword · RRF fuse  │
                    │ rerank · episode expansion   │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │        Evidence Pack         │
                    │ source · chunk · score · url │
                    └──────────────┬───────────────┘
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────┐        ┌──────────────────┐       ┌──────────────────┐
│  User Memory  │        │ Conversation     │       │ Transcript       │
│ personalizes  │        │ context          │       │ knowledge        │
└───────┬───────┘        └────────┬─────────┘       └────────┬─────────┘
        └─────────────────────────┼──────────────────────────┘
                                  ▼
                       ┌────────────────────┐
                       │  Context Builder   │  three labelled blocks,
                       └─────────┬──────────┘  never blurred together
                                 ▼
                       ┌────────────────────┐
                       │   Model Gateway    │  Ollama | Anthropic | OpenAI
                       └─────────┬──────────┘
                                 ▼
                       ┌────────────────────┐
                       │ Grounding Validator│  claims ↔ evidence
                       └─────────┬──────────┘
                                 ▼
                          Final response
```

Full detail — schema, boundaries, failure handling, why each choice was made — is in
[`docs/architecture.md`](docs/architecture.md).

---

## 3. Prerequisites

| Requirement | Why | Notes |
|---|---|---|
| **Docker + Docker Compose** | the one-command path | or Python 3.11 + Node 22 + Postgres 16 locally |
| **Ollama** | the demo runs on a local model | https://ollama.com — installed on the **host**, not in Compose |
| **~6 GB disk** | model weights + transcripts | `llama3.1:8b` ≈ 4.7GB, `nomic-embed-text` ≈ 274MB |
| **git** | to clone the transcript archive | `make transcripts` |
| Anthropic or OpenAI key | optional | only if you want to run the cloud provider |

---

## 4. Quick start (Docker)

```bash
git clone <this-repo> lenny-growth-assistant
cd lenny-growth-assistant
cp .env.example .env

# 1. Local model (in a separate terminal, on the host — not in Docker)
ollama serve
ollama pull llama3.1:8b
ollama pull nomic-embed-text

# 2. Transcripts (~26MB, 303 episodes)
make transcripts

# 3. The stack: postgres + backend + frontend, migrations run automatically
docker compose up --build     # or: make up

# 4. Index a demo-sized corpus (25 episodes, a few minutes)
make ingest-demo              # or `make ingest` for all 303

# 5. Open the app
open http://localhost:3000
```

Verify before you start clicking:

```bash
curl -s localhost:8000/health | python3 -m json.tool
# {"status": "ok", "components": {"database": {"status": "ok", ...},
#                                 "model": {"status": "ok", "detail": "ollama:llama3.1:8b"},
#                                 "embeddings": {"status": "ok", ...}}}
```

If `model` is `degraded`, Ollama is not reachable from the container — see
[Troubleshooting](#13-troubleshooting).

---

## 5. Quick start (local, no Docker)

```bash
# Postgres 16 with pgvector must be running and reachable.
createdb lenny
psql -d lenny -c 'CREATE EXTENSION IF NOT EXISTS vector;'

cp .env.example .env    # set DATABASE_URL to your local instance

make venv               # creates backend/.venv, installs dependencies
make migrate            # alembic upgrade head
make transcripts
make ingest-local

make dev-backend        # terminal 1 → http://localhost:8000
make dev-frontend       # terminal 2 → http://localhost:3000
```

---

## 6. Transcript setup

**Where the data comes from.** The corpus is the public archive
[`ChatPRD/lennys-podcast-transcripts`](https://github.com/ChatPRD/lennys-podcast-transcripts):
303 episodes as `episodes/<guest-slug>/transcript.md`, each with YAML frontmatter (guest,
title, `youtube_url`, publish date, keywords) and a speaker-labelled body with timestamps.

It is **not vendored into this repository** — it is ~26MB of someone else's content, and
pinning a copy would go stale. `make transcripts` clones it into `data/transcripts/`
(gitignored). Nothing in this repo invents transcript content; with no transcripts
ingested, the assistant refuses to answer rather than falling back on the model's priors.

**How ingestion works** (`backend/app/ingestion/`):

```
transcript.md
   ↓  loader     frontmatter → title / guest / source_url / metadata
   ↓  cleaner    parse `Speaker (00:12:34):` turns; drop sponsor reads and the outro
   ↓  chunker    pack whole turns to ~350 tokens with ~60 tokens of overlap
   ↓  embedder   Ollama nomic-embed-text (768d)
   ↓  store      documents + chunks in PostgreSQL
   ↓  index      pgvector HNSW (cosine) + a GENERATED tsvector column with a GIN index
```

**Chunking strategy.** Podcast answers are long and self-contained, so chunks break on
*utterance boundaries* rather than character counts — a chunk is a whole run of turns.
~350 tokens is long enough to carry a claim plus its qualifier and short enough that eight
of them fit comfortably in an 8B model's context alongside history and instructions.

**Indexing strategy.** `chunks.content_tsv` is a Postgres GENERATED column, so the lexical
index can never drift from the text. Vectors use an HNSW index with cosine ops.

**Refresh / re-ingestion.** Documents are keyed by their path in the archive and carry a
content hash. Re-running ingestion skips unchanged episodes, so `git pull` in
`data/transcripts` + `make ingest` only pays for what changed. `--force` re-chunks and
re-embeds everything (needed after changing chunk sizes or the embedding model).

**Source tracing.** Every chunk stores `title`, `guest`, `source_url`, `chunk_index`,
`start_seconds`, and a `deep_link` — a YouTube URL with `&t=<seconds>s`. That is what the
"Listen" button in the sources list opens, so any citation can be verified against the
recording in two clicks.

```bash
make corpus     # documents / chunks / embedded chunks / guests currently indexed
```

---

## 7. Model configuration

Switching models is an environment change. No code changes, no rebuild of the backend
image (restart the container to pick up new env).

```env
# Local — this is what the submitted demo runs on
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434     # http://host.docker.internal:11434 in Docker
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

```env
# Cloud
LLM_PROVIDER=cloud
CLOUD_PROVIDER=anthropic        # or openai
CLOUD_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=sk-ant-...
```

The active provider is always shown in the UI header (click the badge for model,
embedding provider, and per-dependency health) and returned by `GET /api/model`.

**Fallback behaviour — deliberate and documented.** There is **no automatic
cross-provider fallback**. If you selected a local model and Ollama goes down, the request
fails with `MODEL_UNAVAILABLE` and an actionable message; your prompt is never silently
shipped to a cloud API you did not choose. Switching is an explicit operator action.

There *is* one automatic degradation, and the UI announces it: if the **embedding** model
is unavailable, retrieval degrades to keyword-only rather than failing the request
(`EMBEDDING_ALLOW_FALLBACK=true`). The response is marked degraded and the reason is shown.

A third provider, `LLM_PROVIDER=stub`, is a deterministic test double used by the test
suite. It is not a model; it reports itself as `stub/deterministic` and is never used for
a demo.

---

## 8. Environment variables

Full annotated list in [`.env.example`](.env.example). The ones that matter:

| Variable | Default | Required? | Purpose |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/lenny` | **yes** | Postgres + pgvector DSN |
| `LLM_PROVIDER` | `ollama` | **yes** | `ollama` \| `cloud` \| `stub` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | with ollama | `http://host.docker.internal:11434` inside Compose |
| `OLLAMA_MODEL` | `llama3.1:8b` | with ollama | any chat model you have pulled |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | with ollama | must be 768-dim to match the schema |
| `ANTHROPIC_API_KEY` | – | with cloud | never commit it; `.env` is gitignored |
| `EMBEDDING_PROVIDER` | `ollama` | no | `ollama` \| `openai` \| `hash` |
| `EMBEDDING_DIM` | `768` | no | changing it needs a new migration + re-ingest |
| `RETRIEVAL_VECTOR_WEIGHT` / `RETRIEVAL_KEYWORD_WEIGHT` | `0.6` / `0.4` | no | hybrid fusion weights |
| `GROUNDING_MIN_SUPPORT` | `0.28` | no | per-claim support threshold |
| `MEMORY_ENABLED` | `true` | no | turn personalization off entirely |
| `TRANSCRIPTS_DIR` | `./data/transcripts` | no | ingestion root (also the API's path allowlist) |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | no | baked into the frontend at build time |

---

## 9. Development commands

```bash
make help            # every target, described
make up / down       # start / stop the Docker stack
make logs            # tail all service logs
make transcripts     # clone or update the transcript archive
make ingest-demo     # index 25 episodes
make ingest          # index all 303
make corpus          # what is indexed right now
make venv            # local Python env
make migrate         # alembic upgrade head
make dev-backend     # uvicorn with autoreload
make dev-frontend    # next dev
make test            # backend + frontend suites
make typecheck       # tsc --noEmit
```

---

## 10. Tests

```bash
make test
```

**Backend — 119 tests** (`backend/tests/`), run against a real Postgres + pgvector, with
the deterministic stub provider and embedder so no model server or API key is needed:

| File | Covers |
|---|---|
| `test_api.py` | health, sessions CRUD, validation errors, structured error envelope, request ids |
| `test_chat_flow.py` | end-to-end turns per route, persistence, session isolation, SSE event stream, refusal with no evidence |
| `test_retrieval.py` | keyword leg, vector leg, hybrid fusion, diversity, guest boosting, empty retrieval, episode expansion |
| `test_routing.py` | rule routing, hints, precedence, LLM tie-breaker failure, "no model call when a rule matches" |
| `test_memory.py` | extraction filtering, upsert, query-dependent retrieval, user isolation, cap eviction, **memory failure does not break RAG** |
| `test_grounding.py` | supported accepted, unsupported annotated, empty evidence refused, hallucinated citations stripped |
| `test_artifact_security.py` | 12 injection payloads, CSS filtering, data-URI-only images, size limits, API sanitization |
| `test_ingestion.py` | frontmatter parsing, sponsor/outro removal, chunk boundaries, deep links, idempotent re-ingest |
| `test_llm_gateway.py` | Ollama generate/stream/timeout/404/unreachable (httpx MockTransport), cloud key errors, JSON extraction |
| `test_persistence.py` | cascades, metadata round-trip, uniqueness constraints, timestamps |

**Frontend — 15 tests** (`frontend/tests/`): SSE parsing (including partial buffers and
malformed payloads), artifact viewer sandbox attributes, and message metadata rendering.

A **manual UI test plan** is in [`docs/manual-test-plan.md`](docs/manual-test-plan.md).

CI runs both suites plus a production build on every push
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

---

## 11. API reference

Interactive docs at `http://localhost:8000/docs`.

```
GET    /health                              dependency-level health
GET    /api/model                           active provider + availability

POST   /api/sessions                        create a session
GET    /api/sessions?external_user_id=…     list sessions
GET    /api/sessions/{id}                   session + messages + artifacts
DELETE /api/sessions/{id}                   delete a session

POST   /api/sessions/{id}/messages          send a message  (stream:true → SSE)

POST   /api/artifacts                       store an artifact (always sanitized)
GET    /api/artifacts?session_id=…          list artifacts
GET    /api/artifacts/{id}                  fetch an artifact

GET    /api/memories?external_user_id=…     inspect memory
POST   /api/memories                        add a memory
DELETE /api/memories/{id}                   forget one
DELETE /api/memories?external_user_id=…     forget everything

POST   /api/ingestion                       run ingestion
GET    /api/ingestion/stats                 corpus statistics
```

Every error uses one envelope:

```json
{ "error": { "code": "RETRIEVAL_UNAVAILABLE",
             "message": "Knowledge retrieval is temporarily unavailable." } }
```

Codes the UI branches on: `SESSION_NOT_FOUND`, `VALIDATION_ERROR`, `DATABASE_UNAVAILABLE`,
`RETRIEVAL_UNAVAILABLE`, `EMBEDDING_UNAVAILABLE`, `MODEL_UNAVAILABLE`, `MODEL_TIMEOUT`,
`MODEL_NOT_CONFIGURED`, `SANITIZATION_FAILED`, `ARTIFACT_INVALID`, `INTERNAL_ERROR`.

---

## 12. Observability

Structured JSON logs on stdout, one line per event, with `request_id` and `session_id`
bound for the whole turn:

```json
{"event":"retrieval.completed","strategy":"chunk","retrieval_count":8,"candidates":51,
 "vector_hits":30,"keyword_hits":30,"latency_ms":15.98,"degraded":false,
 "request_id":"a94ae86c…","session_id":"b8dbdba3…","timestamp":"2026-08-24T03:26:40Z"}
```

Events: `request.started` · `agent.route_selected` · `memory.retrieved` ·
`retrieval.started` · `retrieval.completed` · `evidence.created` · `llm.started` ·
`llm.completed` · `llm.failed` · `grounding.completed` · `artifact.generated` ·
`artifact.sanitized` · `memory.extracted` · `database.error` · `request.completed`.

API keys and secrets are redacted by a log processor before rendering, so a careless
`log.info(..., api_key=...)` cannot leak one.

```bash
docker compose logs -f backend | grep retrieval.completed
LOG_FORMAT=console make dev-backend      # human-readable logs while developing
```

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/health` → `model: degraded`, "Could not reach Ollama" | Ollama not running, or not reachable from the container | `ollama serve` on the host; in Docker set `OLLAMA_BASE_URL=http://host.docker.internal:11434` (Linux: `--add-host` is already configured via `extra_hosts`) |
| `MODEL_UNAVAILABLE`, "does not have `llama3.1:8b` pulled" | model not downloaded | `ollama pull llama3.1:8b` |
| Answers say "I couldn't find anything…" for everything | corpus is empty | `make transcripts && make ingest-demo`; check `make corpus` |
| `/health` → `database: down` | Postgres not up, or wrong DSN | `docker compose ps`; check `DATABASE_URL` (Docker uses host `postgres`, local uses `localhost`) |
| `pgvector extension is not installed` | plain Postgres image | use `pgvector/pgvector:pg16`, or `CREATE EXTENSION vector;` then `make migrate` |
| `Embedding dimension mismatch: model returned 1024, schema expects 768` | different embedding model | keep a 768-dim model, or set `EMBEDDING_DIM`, write a migration, and re-ingest with `--force` |
| Retrieval marked "keyword-only" | embedding model down | start Ollama and pull `nomic-embed-text`; the app keeps working meanwhile |
| Frontend loads, every call fails with `NETWORK_ERROR` | frontend built with the wrong API URL | rebuild with `NEXT_PUBLIC_API_BASE_URL=…` (it is inlined at build time) |
| First answer takes 30s+ | cold local model | expected on first token; subsequent turns are faster. Use a smaller model (`llama3.2:3b`) on a modest laptop |
| Essays come out short | small local models under-write | the Ship30 skill already runs one expansion pass; a larger model or the cloud provider closes the gap |
| Ingestion is slow | embedding throughput | `make ingest-demo` (25 episodes) is enough for a demo; `--no-embed` indexes text only |

---

## 14. Security notes

- **Generated HTML is untrusted.** Two independent layers: a server-side allowlist
  sanitizer (tags, attributes, URL schemes, CSS) that runs before anything is stored, and a
  viewer that renders the result in an iframe with `sandbox=""` — no scripts, no
  same-origin, no forms, no navigation — plus a restrictive CSP inside the document.
  Full rationale, allow/block lists and the residual-risk discussion:
  [`docs/architecture.md#artifact-security`](docs/architecture.md#artifact-security).
- **Images must be `data:` URIs.** A remote `<img>` in generated HTML is a tracking pixel
  with extra steps; remote references are stripped.
- **Markdown is rendered without `rehype-raw`**, so embedded HTML displays as text.
- **No secrets in the repo.** `.env` is gitignored, `.env.example` ships empty keys, and
  the log pipeline redacts credential-shaped keys.
- **Errors never leak internals.** Clients get a code and a sentence; stack traces and
  provider messages stay in the logs.
- **Input validation** on every endpoint via Pydantic; ingestion paths are constrained to
  `TRANSCRIPTS_DIR` so the API cannot be used to read the filesystem.
- **No authentication** — deliberately out of scope for a local evaluation build, and
  called out as such in the PRD. `get_or_create_user` is the single seam to replace.

---

## 15. Architecture trade-offs

Three decisions worth defending, in short. The long versions are in
[`docs/architecture.md`](docs/architecture.md).

> **One database, not a stack.** I intentionally avoided unnecessary distributed
> infrastructure. PostgreSQL serves as the transactional store, the full-text search
> engine, and the vector store because the dataset and evaluation scope do not justify
> introducing additional operational dependencies. One backup, one connection string, one
> failure mode — and joins between chunks and their episode metadata are free.

> **The agent is controlled, not autonomous.** Deterministic routing with a bounded set of
> skills improves reliability, testing, latency, and operational debugging. Every turn
> walks the same pipeline; there is no plan the model can invent, no tool loop that can run
> away, and every stage boundary is a log line and a unit test.

> **Memory is separated from transcript evidence.** Memory personalizes responses but is
> never treated as authoritative evidence for Lenny-related claims. They live in separate
> tables, arrive in separately labelled prompt blocks, are returned in separate response
> fields, and only evidence is checked by the grounding validator.

---

## 16. What is not built

Stated plainly, because pretending otherwise wastes an evaluator's time:

- **No authentication or multi-tenancy.** Identity is a browser-local id.
- **Grounding validation is lexical, not semantic.** It reliably catches off-corpus drift
  and hallucinated citations; it does not verify truth, and a fluent paraphrase that reuses
  evidence vocabulary can pass. The upgrade path (NLI or an LLM judge) is documented.
- **The reranker is a fusion heuristic**, not a cross-encoder — a deliberate cost/latency
  choice at this corpus size.
- **Ollama is not containerized.** It runs on the host for GPU/Metal access.
- **No evaluation harness.** The PRD proposes metrics; they are targets, not measurements.
- **Cloud providers are implemented but were not exercised against live APIs** in this
  build (no keys available in the build environment). They are covered by unit tests for
  the configuration and error paths, and the abstraction is the same one Ollama uses.

---

## 17. Repository map

```
lenny-growth-assistant/
├── backend/
│   ├── app/
│   │   ├── api/           chat (JSON + SSE), sessions, artifacts, memory, ingestion, health
│   │   ├── agent/         controller · router · state · context_builder
│   │   ├── skills/        rag · ship30 · artifact  (+ on-disk SKILL.md loader)
│   │   ├── retrieval/     vector · keyword · reranker · evidence
│   │   ├── llm/           base · ollama · cloud · stub · factory
│   │   ├── embeddings/    ollama · openai · deterministic fallback
│   │   ├── memory/        extractor · manager · retriever
│   │   ├── grounding/     validator
│   │   ├── ingestion/     loader · cleaner · chunker · indexer
│   │   ├── security/      sanitizer
│   │   ├── db/            models · database
│   │   └── observability/ structured logging
│   ├── alembic/           migrations
│   └── tests/             119 tests
├── frontend/
│   ├── app/               Next.js App Router
│   ├── components/        workspace · chat · sources · artifact viewer · memory · model badge
│   ├── lib/               typed API client + SSE parser
│   └── tests/             15 tests
├── skills/
│   ├── ship30/SKILL.md    the Ship 30 writing standard  ← editable without touching code
│   └── artifact/SKILL.md  artifact generation rules
├── docs/                  PRD · architecture · design · demo script · manual test plan
├── agent-transcripts/     how this was built with an AI coding agent, including the bugs
├── data/transcripts/      corpus (gitignored, fetched by `make transcripts`)
├── docker-compose.yml
├── Makefile
└── .env.example
```

---

Built as a forward-deployment exercise: the goal was a system another engineer can run,
trust, debug, and extend — not a demo that only works once.
