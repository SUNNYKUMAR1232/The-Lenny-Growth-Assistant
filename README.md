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

Screenshots are in [`docs/images/`](docs/images/).

---

## Setup in one command

```bash
./scripts/setup.sh            # macOS / Linux
```
```powershell
.\scripts\setup.ps1           # Windows
```

Checks prerequisites, writes `.env`, pulls the models, fetches the transcripts, starts the
stack, and indexes the knowledge base. Idempotent — a re-run after a failure resumes.
Then open <http://localhost:3000>.

| macOS / Linux | Windows | Effect |
|---|---|---|
| *(default)* | *(default)* | index 25 episodes — a demo corpus, a few minutes |
| `--full` | `-Full` | index all 303 episodes (~21,700 chunks) |
| `--episodes 50` | `-Episodes 50` | choose the corpus size |
| `--force` | `-Force` | re-chunk and re-embed what is already indexed |
| `--skip-models` | `-SkipModels` | models already pulled, or using a cloud model |
| `--skip-transcripts` | `-SkipTranscripts` | transcripts already on disk |

Use `--force` after changing `EMBEDDING_PROVIDER` or the chunk size: vectors already in
Postgres came from the *old* embedder, and mixing embedders in one index degrades
retrieval silently rather than failing.

If PowerShell blocks the script, that is the execution policy, not the script:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.

---

## Contents

[What it does](#what-it-does) · [Architecture](#architecture) · [Prerequisites](#prerequisites) ·
[Running it](#running-it) · [Knowledge base](#knowledge-base) · [Model configuration](#model-configuration) ·
[Environment variables](#environment-variables) · [Commands](#commands) · [Tests](#tests) ·
[API](#api) · [Observability](#observability) · [Troubleshooting](#troubleshooting) ·
[Security](#security) · [Limitations](#limitations) · [Repository map](#repository-map)

Deeper documents: [PRD](docs/PRD.md) · [architecture.md](docs/architecture.md) ·
[design.md](docs/design.md) · [manual test plan](docs/manual-test-plan.md) ·
[demo script](docs/demo-script.md) · [agent transcripts](agent-transcripts/)

---

## What it does

| Capability | How it works |
|---|---|
| **Grounded Q&A** | Hybrid retrieval (pgvector + Postgres FTS) → Evidence Pack → answer with `[S#]` citations → grounding validation |
| **Session memory** | Each chat is an independent session; history, evidence, routing and grounding verdicts persisted in PostgreSQL |
| **Personal memory** | Durable facts about the user, filtered by confidence/importance, retrieved per query — and never treated as evidence |
| **Ship 30 essays** | A skill file (`skills/ship30/SKILL.md`) encodes the writing standard; the agent enforces length and grounding |
| **Artifacts** | Markdown and complete HTML/CSS, sanitized server-side and rendered in a fully sandboxed iframe |
| **Model flexibility** | Local Ollama, Anthropic/OpenAI, or the Pi agent — no code change, provider always visible in the UI |
| **Operability** | One-command startup, structured JSON logs, dependency-level health checks, typed errors, 130 backend + 34 frontend tests |

---

## Architecture

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


The agent is **controlled, not autonomous**: deterministic routing over a bounded set of
skills. Every turn walks the same pipeline, so each stage boundary is a log line and a unit
test — there is no plan the model can invent and no tool loop that can run away.

Two other decisions worth stating: **one database, not a stack** (Postgres is the
transactional store, the FTS engine, *and* the vector store — one backup, one connection
string, one failure mode), and **memory is separated from transcript evidence** (separate
tables, separately labelled prompt blocks, separate response fields; only evidence is
checked by the validator).

Schema, component boundaries, retrieval weights, failure handling and the long-form
rationale: [`docs/architecture.md`](docs/architecture.md).

---

## Prerequisites

| Requirement | Why | Notes |
|---|---|---|
| **Docker + Docker Compose** | the one-command path | or Python 3.11 + Node 22 + Postgres 16 locally |
| **Ollama** | the demo runs on a local model | https://ollama.com — on the **host**, not in Compose |
| **~6 GB disk** | model weights + transcripts | `llama3.1:8b` ≈ 4.7GB, `nomic-embed-text` ≈ 274MB |
| **git** | to clone the transcript archive | done by the setup script |
| Anthropic or OpenAI key | optional | only for the cloud provider |

---

## Running it

**Docker** — what the setup script automates:

```bash
cp .env.example .env
ollama serve && ollama pull llama3.1:8b && ollama pull nomic-embed-text
make transcripts                 # clone the transcript archive
docker compose up --build -d     # migrations run automatically
make ingest-demo                 # index 25 episodes (make ingest for all 303)
```

In `.env`, Docker needs `OLLAMA_BASE_URL=http://host.docker.internal:11434`. The
`.env.example` default is `localhost`, which is right only for the no-Docker path — leave
it and the backend looks for Ollama inside its own container (amber model badge).

Verify before clicking:

```bash
curl -s localhost:8000/health | python -m json.tool
# components.model → {"status":"ok","detail":"ollama:llama3.1:8b"}
```

**Local, no Docker** — Postgres 16 with pgvector must be reachable:

```bash
createdb lenny && psql -d lenny -c 'CREATE EXTENSION IF NOT EXISTS vector;'
make venv && make migrate
make transcripts && make ingest-local
make dev-backend      # terminal 1 → :8000
make dev-frontend     # terminal 2 → :3000
```

**Windows** — use `.\scripts\setup.ps1`. `make` does not exist in `cmd`/PowerShell, so
without the script you run the Makefile's commands by hand; the equivalents are
`copy` for `cp`, `move` for `mv`, `rmdir /s /q` for `rm -rf`, and `$env:VAR="value"` for
`export`. If port 5432 is taken by a local Postgres, publish `-p 5433:5432` and update
`DATABASE_URL`.

---

## Knowledge base

The corpus is the public archive
[`ChatPRD/lennys-podcast-transcripts`](https://github.com/ChatPRD/lennys-podcast-transcripts):
303 episodes as `episodes/<guest-slug>/transcript.md` with YAML frontmatter and a
speaker-labelled body. It is **not vendored** — ~26MB of someone else's content that would
go stale — so the setup script clones it into `data/transcripts/` (gitignored).

```
transcript.md
   ↓  loader     frontmatter → title / guest / source_url
   ↓  cleaner    parse `Speaker (00:12:34):` turns; drop sponsor reads and the outro
   ↓  chunker    pack whole turns to ~350 tokens with ~60 tokens of overlap
   ↓  embedder   Ollama nomic-embed-text (768d)
   ↓  store      documents + chunks in PostgreSQL
   ↓  index      pgvector HNSW (cosine) + a GENERATED tsvector column with a GIN index
```

Chunks break on **utterance boundaries**, not character counts — a podcast answer is long
and self-contained, and ~350 tokens carries a claim plus its qualifier while letting eight
fit in an 8B model's context. `chunks.content_tsv` is a GENERATED column, so the lexical
index can never drift from the text.

**Refresh.** Documents are keyed by archive path and carry a content hash, so re-running
ingestion skips unchanged episodes; `--force` re-chunks and re-embeds everything (needed
after changing the embedding model). **Tracing.** Every chunk stores `title`, `guest`,
`source_url`, `chunk_index`, `start_seconds` and a `deep_link` — a YouTube URL with
`&t=<seconds>s`, which is what the "Listen" button opens.

---

## Model configuration

Switch models without touching code — via `.env`, or from the UI's model settings panel
with no restart.

```env
LLM_PROVIDER=ollama          # ollama | cloud | pi | stub
OLLAMA_MODEL=llama3.1:8b
CLOUD_PROVIDER=anthropic     # anthropic | openai
CLOUD_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=           # only when LLM_PROVIDER=cloud
```

The active provider is always visible in the UI badge and at `GET /api/model`.

**Fallback policy — deliberately no silent cross-provider fallback.** If you chose a local
model for data-residency reasons, a hiccup must never quietly ship your prompt to a cloud
API. A down provider returns a typed, actionable error and the UI shows it. The *one*
automatic degradation is retrieval: if the embedding model is unavailable, search drops to
keyword-only, and the response is marked degraded with the reason
(`EMBEDDING_ALLOW_FALLBACK=true`).

**Runtime config from the UI** (`POST /api/model/config`) is memory-only: keys never touch
disk, reads return only the last four characters, restarting returns to `.env`, and
`ALLOW_RUNTIME_MODEL_CONFIG=false` (the default in production) refuses the endpoint.

**The agent layer — Pi Coding Agent.** The assignment asks for the Claude Agent SDK or Pi;
this repo uses Pi.

```bash
npm install -g @earendil-works/pi-coding-agent    # Node CLI; not in the backend image
```
```env
LLM_PROVIDER=pi
PI_PROVIDER=anthropic        # anthropic | openai | google | ollama
PI_MODEL=claude-sonnet-4-5
```

> **Docker note:** the backend image ships no Node, so `LLM_PROVIDER=pi` works on the
> local (non-Docker) path only. In Docker it fails with a clear
> `The Pi Coding Agent CLI was not found`.

Pi runs as a subprocess speaking NDJSON, invoked `--no-tools --no-session --no-extensions
--no-skills --no-context-files --no-prompt-templates` in an empty temp dir. That is the
point: a coding agent's read/bash/edit/write tools have no place in a grounded-answer path
where the controller has already retrieved the evidence. Credentials go through the
environment, never argv — a command line is world-readable in `/proc`.

Full rationale, including why not the Claude Agent SDK (it pulls `mcp` 2.0 → `starlette>=1.6`,
which broke FastAPI 0.116): [`docs/architecture.md`](docs/architecture.md#the-agent-layer-pi-coding-agent).

---

## Environment variables

Every knob is documented with safe defaults in [`.env.example`](.env.example). The ones
that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` · `cloud` · `pi` · `stub` |
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` · `openai` · `hash` (non-semantic fallback) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | **`host.docker.internal` in Docker** |
| `DATABASE_URL` | local Postgres DSN | host `postgres` in Docker, `localhost` outside |
| `RETRIEVAL_TOP_K` | `8` | evidence chunks per answer |
| `GROUNDING_ENABLED` | `true` | claim ↔ evidence validation |
| `ALLOW_RUNTIME_MODEL_CONFIG` | `true` | off by default in production |

No secrets are committed: `.env` and `.env.bak*` are gitignored, `.env.example` ships empty
keys, and the log pipeline redacts credential-shaped values.

---

## Commands

```bash
make help            # every target, described
make setup           # one-command setup (setup-full for all 303 episodes)
make up / down       # start / stop the Docker stack
make logs            # tail all service logs
make transcripts     # clone or update the transcript archive
make ingest-demo     # index 25 episodes   (make ingest = all 303)
make corpus          # what is indexed right now
make venv / migrate  # local Python env; alembic upgrade head
make dev-backend     # uvicorn with autoreload
make dev-frontend    # next dev
make test            # backend + frontend suites
make typecheck       # tsc --noEmit
```

---

## Tests

```bash
make test
```

**Backend — 130 tests** (`backend/tests/`), against a real Postgres + pgvector, using the
deterministic stub provider and embedder so no model server or API key is needed. Covers
health and session CRUD, the structured error envelope, end-to-end chat per route, session
isolation, SSE streaming, refusal with no evidence, all three retrieval legs and fusion,
routing precedence, memory isolation and cap eviction, grounding accept/annotate/refuse,
12 artifact injection payloads, ingestion idempotency, model-gateway timeouts and
unreachability, and persistence cascades.

**Frontend — 34 tests** (`frontend/tests/`): SSE parsing including partial buffers and
malformed payloads, artifact viewer sandbox attributes, message metadata rendering, and stream cancellation.

A manual UI test plan is in [`docs/manual-test-plan.md`](docs/manual-test-plan.md). CI runs
both suites plus a production build on every push
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

---

## API

Interactive docs at `http://localhost:8000/docs`.

```
GET    /health                              dependency-level health
GET    /api/model                           active provider + availability
                                            (/options, /test, /config — see architecture.md)

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

## Observability

Structured JSON logs on stdout, one line per event, with `request_id` and `session_id`
bound for the whole turn:

```json
{"event":"retrieval.completed","strategy":"chunk","retrieval_count":8,"candidates":51,
 "latency_ms":15.98,"degraded":false,"request_id":"a94ae86c…","timestamp":"2026-08-24T03:26:40Z"}
```

Events run `request.started` → `agent.route_selected` → `memory.retrieved` →
`retrieval.*` → `evidence.created` → `llm.*` → `grounding.completed` →
`artifact.*` → `request.completed`, plus `database.error`. A log processor redacts
credential-shaped keys, so a careless `log.info(..., api_key=...)` cannot leak one.

```bash
docker compose logs -f backend | grep retrieval.completed
LOG_FORMAT=console make dev-backend      # human-readable while developing
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every question says "I couldn't find anything…" | corpus is empty | `make corpus`; if `chunks` is 0, `make transcripts && make ingest-demo`. ~60ms retrieval is the tell |
| `/health` → `model: degraded` | Ollama down or unreachable from the container | `ollama serve`; set `OLLAMA_BASE_URL=http://host.docker.internal:11434` |
| `MODEL_UNAVAILABLE`, "does not have `llama3.1:8b`" | model not pulled | `ollama pull llama3.1:8b` |
| `The Pi Coding Agent CLI was not found` | Pi needs Node; not in the backend image | use the local path, or `npm install -g @earendil-works/pi-coding-agent` |
| `/health` → `database: down` | Postgres not up, or wrong DSN | `docker compose ps`; Docker uses host `postgres`, local uses `localhost` |
| `pgvector extension is not installed` | plain Postgres image | use `pgvector/pgvector:pg16`, or `CREATE EXTENSION vector;` then `make migrate` |
| `Embedding dimension mismatch: returned 1024, schema expects 768` | different embedding model | keep a 768-dim model, or set `EMBEDDING_DIM`, migrate, and re-ingest `--force` |
| Retrieval marked "keyword-only" | embedding model down | start Ollama and pull `nomic-embed-text`; the app keeps working meanwhile |
| Frontend loads, calls fail `NETWORK_ERROR` | wrong API URL baked in | rebuild with `NEXT_PUBLIC_API_BASE_URL=…` (inlined at build time) |
| First answer takes 30s+ | cold local model | expected; later turns are faster. Try `llama3.2:3b` on a modest laptop |
| Essays come out short | small local models under-write | the skill runs one expansion pass; a larger or cloud model closes the gap |

A *populated* corpus that refuses one specific question is different — and worded
differently. That is retrieval genuinely finding nothing relevant, which is the system
working as designed.

---

## Security

- **Generated HTML is untrusted.** Two independent layers: a server-side allowlist
  sanitizer (tags, attributes, URL schemes, CSS) that runs before anything is stored, and a
  viewer that renders it in an iframe with `sandbox=""` — no scripts, no same-origin, no
  forms, no navigation — plus a restrictive CSP inside the document.
- **Images must be `data:` URIs.** A remote `<img>` is a tracking pixel with extra steps.
- **Markdown renders without `rehype-raw`**, so embedded HTML displays as text.
- **Errors never leak internals.** Clients get a code and a sentence; stack traces stay in
  the logs. Pydantic validates every endpoint, and ingestion paths are constrained to
  `TRANSCRIPTS_DIR` so the API cannot read the filesystem.
- **No authentication** — deliberately out of scope for a local evaluation build.
  `get_or_create_user` is the single seam to replace.

Allow/block lists and the residual-risk discussion:
[`docs/architecture.md#artifact-security`](docs/architecture.md#artifact-security).

## Repository map

```
lenny-growth-assistant/
├── backend/app/
│   ├── api/            chat (JSON + SSE), sessions, artifacts, memory, ingestion, health
│   ├── agent/          controller · router · state · context_builder
│   ├── skills/         rag · ship30 · artifact  (+ on-disk SKILL.md loader)
│   ├── retrieval/      vector · keyword · reranker · evidence
│   ├── llm/            base · ollama · cloud · pi_agent · stub · factory
│   ├── embeddings/     ollama · openai · deterministic fallback
│   ├── memory/         extractor · manager · retriever
│   ├── grounding/      validator
│   ├── ingestion/      loader · cleaner · chunker · indexer
│   ├── security/       sanitizer
│   └── db/ · observability/
├── frontend/           Next.js App Router · components · typed API client + SSE parser
├── scripts/            setup.sh · setup.ps1
├── skills/             ship30/SKILL.md · artifact/SKILL.md  ← editable without touching code
├── docs/               PRD · architecture · design · demo script · manual test plan
├── agent-transcripts/  how this was built with an AI coding agent, including the bugs
└── data/transcripts/   corpus (gitignored, fetched by scripts/setup.*)
```

---

Built as a forward-deployment exercise: the goal was a system another engineer can run,
trust, debug, and extend — not a demo that only works once.
