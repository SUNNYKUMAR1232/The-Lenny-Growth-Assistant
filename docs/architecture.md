# Architecture

How the Lenny Growth Assistant is built, and why each part is the way it is.

---

## Contents

[System overview](#system-overview) · [Component boundaries](#component-boundaries) ·
[Database schema](#database-schema) · [Ingestion](#ingestion) · [Chunking](#chunking) ·
[Retrieval](#retrieval) · [Agent routing](#agent-routing) ·
[Memory and context](#memory-and-context) · [Model gateway](#model-gateway) ·
[The agent layer](#the-agent-layer-pi-coding-agent) · [Grounding](#grounding) ·
[Runtime model configuration](#runtime-model-configuration) ·
[Artifact security](#artifact-security) · [API boundaries](#api-boundaries) ·
[Observability](#observability) · [Failure handling](#failure-handling) ·
[Deployment topology](#deployment-topology)

---

## System overview

```
Browser ──REST/SSE──▶ FastAPI ──▶ Controlled Agent Controller
                                        │
                     ┌──────────────────┼──────────────────┐
                     ▼                  ▼                  ▼
                Retrieval          Memory             Model Gateway
                 Engine            layer              (Ollama|Cloud)
                     │                  │                  │
                     └────────► Context Builder ◄──────────┘
                                        │
                                        ▼
                             Grounding Validator
                                        │
                                        ▼
                       PostgreSQL (state · vectors · FTS)
```

One request walks one path. There is no queue, no worker pool, no second datastore.

## Component boundaries

Each of these is a package with one job, a typed interface, and its own tests. Anything
could be replaced without touching its neighbours.

| Component | Package | Owns | Does not know about |
|---|---|---|---|
| API | `app/api` | HTTP contracts, validation, error envelope, SSE transport | retrieval, prompts, models |
| Controller | `app/agent` | pipeline order, state, persistence of a turn | HTTP, SQL details |
| Skills | `app/skills` | one task each, prompt assembly via context builder | database, HTTP |
| Retrieval | `app/retrieval` | search, fusion, ranking, Evidence Pack | models, prompts |
| Memory | `app/memory` | extraction, storage, ranking of user facts | transcripts, evidence |
| Grounding | `app/grounding` | claim ↔ evidence checking | how the answer was produced |
| Model gateway | `app/llm` | provider differences, timeouts, error mapping | the app's domain |
| Embeddings | `app/embeddings` | vectorisation + degradation | retrieval logic |
| Ingestion | `app/ingestion` | load → clean → chunk → embed → index | serving |
| Security | `app/security` | sanitization | where the HTML came from |

**The rule that matters:** skills never touch the database, and retrieval never touches a
model. Content reaches the model through exactly one object — the Evidence Pack — which
is what makes grounding checkable.

## Database schema

```
users ──1:N──▶ sessions ──1:N──▶ messages
  │                 │
  │                 └──1:N──▶ artifacts ──0:1──▶ messages
  └──1:N──▶ memories ──0:1──▶ sessions (source)

documents ──1:N──▶ chunks
```

| Table | Key columns | Notes |
|---|---|---|
| `users` | `id`, `external_id` (unique), `metadata` | `external_id` is the browser-local identity; the seam where real auth would attach |
| `sessions` | `id`, `user_id`, `title`, `created_at`, `updated_at` | title auto-derived from the first question |
| `messages` | `id`, `session_id`, `role`, `content`, `metadata`, `created_at` | `metadata` is the turn's audit record: route, provider/model, evidence, grounding report, memories used, warnings, request id |
| `documents` | `id`, `source_key` (unique), `title`, `guest`, `source_url`, `content`, `content_hash` | one episode; `content_hash` powers idempotent re-ingest |
| `chunks` | `id`, `document_id`, `chunk_index`, `content`, `embedding vector(768)`, `content_tsv` (GENERATED), `metadata` | `metadata` carries `start_seconds`, `speakers`, `deep_link` |
| `memories` | `id`, `user_id`, `type`, `key`, `value`, `confidence`, `importance`, `embedding`, `expires_at` | unique on `(user_id, type, key)` so restated facts update instead of duplicating |
| `artifacts` | `id`, `session_id`, `message_id`, `type`, `title`, `content`, `raw_content` | `content` is sanitized and rendered; `raw_content` is the untrusted original, kept for debugging and never rendered |

Indexes: `hnsw (embedding vector_cosine_ops)` on `chunks` and `memories`; `gin
(content_tsv)` on `chunks`; composite indexes on `(session_id, created_at)` for messages
and artifacts; `(user_id, updated_at)` for memories.

Two schema decisions worth naming:

- **`chunks.content_tsv` is a GENERATED column.** Postgres derives it from `content`, so
  the lexical index cannot drift from the text it indexes. No trigger, no application code,
  no possible inconsistency.
- **`artifacts` stores both the sanitized and the raw payload.** Keeping the raw model
  output makes sanitizer bugs debuggable after the fact; storing it in a separate column
  from the one the viewer reads makes it impossible to render by accident.

**One database, not a stack.** Postgres is the transactional store, the full-text search
engine, and the vector store. At ~300 episodes / ~40k chunks, a dedicated vector DB, an
Elasticsearch cluster or Redis would each add a service, a client, a failure mode and a
backup story to buy nothing: pgvector with HNSW answers in single-digit milliseconds at this
size, the GENERATED tsvector means the lexical index cannot drift from the text, and
chunk↔episode joins stay free instead of becoming an application-side join across two
systems. The honest limit is roughly a million chunks, or heavy concurrent ingestion, where
HNSW build cost and single-writer pressure argue for splitting the vector store out — a
*scale* decision, and the retrieval interface is already the seam where it happens.

## Ingestion

```
data/transcripts/episodes/<guest>/transcript.md
        │
   loader     YAML frontmatter → title, guest, youtube_url, publish_date, keywords
        │     (also accepts plain Markdown, .txt, and JSON exports)
   cleaner    parse `Speaker (00:12:34):` turns → (speaker, start_seconds, text)
        │     drop sponsor reads and the standard outro
   chunker    pack whole turns to ~350 tokens, ~60 tokens overlap
        │
  embedder    nomic-embed-text via Ollama (768d), batched
        │
    store     documents + chunks (one transaction per episode)
        │
    index     HNSW on embeddings; GIN on the generated tsvector
```

**Idempotency.** `source_key` (the archive-relative path) plus `content_hash` means a
re-run skips unchanged episodes. A failed episode is logged and skipped, not fatal: one
malformed transcript must not abort a 300-episode run.

**Traceability.** Every chunk stores the episode title, guest, source URL, chunk index,
start second, and a YouTube deep link (`…&t=1830s`). Citations are therefore verifiable by
a human in two clicks — which is the entire point of grounding.

**Cleaning is conservative.** Sponsor reads ("this episode is brought to you by…") and the
outro are removed because they are high-frequency, semantically distinctive, and would
otherwise dominate retrieval for commercial-sounding queries. Nothing a guest said is
rewritten, and any rule that is unsure keeps the text.

### Embeddings and the honest fallback

Default: `nomic-embed-text` via Ollama, 768 dimensions, matching the schema.

Three providers exist: `ollama`, `openai`, and `hash`. The last is a **deterministic
hashing embedder** — hashed word n-grams projected into a unit vector. It is not a semantic
model: "churn" and "retention" are unrelated to it. It exists for two honest reasons:

1. **CI and tests run with no model server**, deterministically, so assertions are about
   pipeline behaviour rather than model prose.
2. **Graceful degradation**: if the embedding model disappears mid-session,
   `EMBEDDING_ALLOW_FALLBACK=true` degrades retrieval to effectively lexical-only rather
   than failing the request — and the API marks the response `degraded` so the UI can say
   so. It is never presented as vector-search quality.

`EMBEDDING_DIM` is baked into the schema. Changing the embedding model to one with a
different width requires a migration and a `--force` re-ingest; the Ollama provider fails
loudly with exactly that instruction rather than storing truncated vectors.

## Chunking

| Parameter | Value | Why |
|---|---|---|
| Boundary | utterance (speaker turn) | Podcast answers are self-contained; cutting mid-answer destroys the retrievable unit |
| Target size | ~350 tokens | Long enough for a claim *plus* its qualifier; short enough that 8 chunks + history + instructions fit an 8B model's context |
| Overlap | ~60 tokens | Preserves continuity across a seam without duplicating the corpus |
| Very long turns | split on sentences | A 10-minute monologue would otherwise be one unusable chunk |
| Token counting | `words / 0.75` estimate | Within ~10% for English speech, and costs no tokenizer dependency |

Both sizes are env-tunable (`CHUNK_TARGET_TOKENS`, `CHUNK_OVERLAP_TOKENS`); changing them
requires `make ingest --force`.

## Retrieval

```
query
 ├─▶ embed → pgvector cosine kNN ──┐   (semantic: paraphrase, concepts)
 │                                 ├─▶ union ─▶ weighted RRF ─▶ priors ─▶ diversity cap ─▶ top-k
 └─▶ websearch_to_tsquery → FTS ───┘   (lexical: proper nouns, exact phrases)
                                                     │
                                      strategy == "episode"?
                                                     │
                                          expand to a contiguous
                                          window around the best
                                          chunk in each top episode
```

Two legs, because they fail differently. Vector search finds "how do I keep users coming
back" ↔ "retention curve flattening". Keyword search finds "Superhuman", "Sean Ellis",
"PMF survey" — proper nouns where embeddings are mushy. Keyword is also the leg that still
works when the embedding model is down.

**Two strategies.** `chunk` (default) returns the top-k fused chunks. `episode` is chosen
deterministically for comparative and synthesis questions ("compare…", "what do guests
say about…", and every Ship 30 request): it finds the best *episodes*, then pulls a
contiguous window of chunks from each, so the model reasons over coherent stretches of
conversation rather than eight disconnected fragments.

**Junk floor.** Vector search always returns k neighbours, even for a query with nothing to
match — so "no evidence" would never happen without a floor.
`RETRIEVAL_MIN_VECTOR_SIMILARITY` (default 0.05) drops near-orthogonal hits. The right
value is model-dependent: for an embedder whose unrelated-pair similarity sits high, raise
it to 0.4–0.6. Grounding validation is the model-independent backstop.

### Fusion weights

Fusion is weighted Reciprocal Rank Fusion, not a blend of raw scores:

```
score = 0.6 · 1/(60 + vector_rank) + 0.4 · 1/(60 + keyword_rank)
      + 0.15 if the query names this episode's guest
      + 0.05 if query terms appear in the episode title
```

- **RRF over score blending** because cosine similarity and `ts_rank_cd` are not on
  comparable scales — `ts_rank_cd` is unbounded and only meaningful *within* one query.
  Ranks are comparable; scores are not.
- **0.6 / 0.4** because most questions are conceptual paraphrases (semantic leads), but the
  corpus is dense with names and product terms where lexical rescue matters. Both are
  env-tunable and the split is the first thing to revisit with real evaluation data.
- **The two priors are small and lexical** — a guest name in the query is a strong,
  cheap signal that a specific episode is wanted.
- **Diversity cap** of 3 chunks per episode, or a comparative question would come back
  entirely from whichever episode ranked best. If the cap starves the pack, it tops up
  from the best remaining chunks rather than returning less evidence.

**No cross-encoder.** A neural reranker would add a second model server, hundreds of ms,
and another failure mode for a marginal gain at 40k chunks. The interface is the place to
add one when evaluation shows fusion is the bottleneck.

### The Evidence Pack

```json
{
  "query": "How do you know if you have product-market fit?",
  "strategy": "chunk",
  "evidence": [{
    "source_id": "S1",
    "chunk_id": "f1693b72-…",
    "title": "How to know if you have product-market fit",
    "guest": "Rahul Vohra",
    "source_url": "https://www.youtube.com/watch?v=…&t=1830s",
    "chunk_index": 12,
    "text": "We used the Sean Ellis product-market fit survey…",
    "score": 0.0649, "vector_score": 0.71, "keyword_score": 0.93,
    "retrieval": "hybrid"
  }],
  "total_candidates": 51, "latency_ms": 15.98,
  "degraded": false, "degraded_reason": null
}
```

This object is the **only** channel from corpus to model. Skills receive it; they never
query the database. Consequences:

1. **Grounding is checkable** — the validator compares the answer to exactly what the model
   was given.
2. **The UI can show its work** — the sources list is the pack, rendered.
3. **Retrieval is independently testable** — pack in, assertions out, no model involved.
4. **Degradation is explicit** — `degraded` + `degraded_reason` travel with the evidence to
   the UI instead of silently changing answer quality.

## Agent routing

> The agent is controlled rather than fully autonomous because deterministic routing and
> bounded capabilities improve reliability, testing, latency, and operational debugging.

```
classify → retrieve context → select skill → execute → validate → persist
```

Every turn walks that path. The model does not choose a plan, cannot call arbitrary tools,
and cannot loop.

| Property | Controlled | Autonomous tool-loop |
|---|---|---|
| Latency | one or two model calls, bounded | unbounded |
| Testability | each stage unit-tested | end-to-end only, non-deterministic |
| Debuggability | a phase name in every log line | reconstruct intent from tool traces |
| Failure modes | enumerable | emergent |
| Local-model fit | good — 8B models follow narrow instructions | poor — small models loop or mis-call tools |

**Routing** is deterministic-first: explicit client hint → regex rules → LLM tie-breaker →
default `KNOWLEDGE_Q`. About 95% of real requests are settled by rules, which are free,
instant, and covered by tests. The model classifier only runs when nothing matched, and a
test asserts that a rule match never triggers a model call.

The cost of this design is real: the assistant cannot decide to run a second retrieval
after reading the first results. That is the trade I would revisit first if evaluation
showed multi-hop questions failing — and it would be a new *bounded* stage (`retrieve →
critique → retrieve once more`), not a free-form loop.

### Skills

| Skill | Route | Behaviour |
|---|---|---|
| `rag` | `KNOWLEDGE_Q` | Direct answer, 150–400 words, inline `[S#]`. Short-circuits without calling the model when evidence is empty |
| `ship30` | `SHIP30` | Loads `skills/ship30/SKILL.md`, targets ~1,250 words, runs **one** bounded expansion pass if the draft is short, emits a Markdown artifact |
| `artifact` | `ARTIFACT` | Chooses Markdown or HTML deterministically, generates, sanitizes, persists |

**Writing standards live on disk, not in Python.** `skills/ship30/SKILL.md` carries
frontmatter (`target_words`, `tolerance_words`) that the code reads, and prose rules the
model reads. A writer can improve the essay standard in a reviewable diff, and in Docker
the directory is mounted read-only so the change takes effect without a rebuild.

## Memory and context

Three categories, one mechanism:

- **Session memory** — conversation history, loaded per turn from `messages`.
- **Semantic memory** — stable facts: role, company stage, domain, preferences.
- **Episodic memory** — decisions or goals stated in a specific conversation.

```
conversation
   ↓ extractor          LLM structured output, then deterministic patterns as a fallback
candidate memories      {type, key, value, confidence, importance}
   ↓ filter             confidence ≥ 0.6 AND importance ≥ 0.4
persistent memory       upsert on (user_id, type, key); cap 200/user, evict by importance×confidence
   ↓ retrieval          0.65·cosine(query, memory) + 0.25·importance + 0.10·recency
top-k memories into the prompt, in their own labelled block
```

Design commitments:

- **Not everything is stored.** A junk drawer cannot be ranked or audited.
- **Retrieval is query-dependent.** Injecting all memories into every prompt burns context
  and drags irrelevant facts into unrelated answers.
- **Extraction runs every N turns** (default 2), not every turn — it is a second model call
  and it is not urgent.
- **Failure is silent-but-visible**: any memory error is caught, the turn continues on
  transcript evidence alone, and a warning is attached to the response.
- **Users can see and delete everything.** Personalization you cannot inspect is a dark
  pattern.

> Memory is separated from transcript evidence. Memory personalizes responses but is never
> treated as authoritative evidence for Lenny-related claims.

That separation is structural, not stylistic: separate tables, separate prompt blocks with
explicit rules, separate response fields (`memories_used` vs `evidence`), and memory is
excluded from grounding validation. A test asserts memory content never appears in
`evidence`.

### Context builder

```
BASE RULES              cite [S#]; never invent; memory is not evidence; refuse if unsupported
SKILL INSTRUCTIONS      task-specific (RAG / Ship30 / Artifact standard)
USER CONTEXT            personalization only — NOT evidence, never cite this
EVIDENCE                [S1] Title — guest — url  """ … """
[+ insufficient-evidence rule when the pack is empty]
[+ degradation note when retrieval was impaired]
---
conversation history (last 10 turns, ≤6k chars, oldest trimmed first)
current user message
```

The three inputs arrive in **separately labelled blocks with different rules**. If they
were concatenated, neither the model nor the validator could tell "the user told me they
work at Stripe" from "a guest said Stripe does X".

## Model gateway

```python
class LLMProvider:
    async def generate(messages, *, system, temperature, max_tokens) -> LLMResponse
    def   stream(messages, ...) -> AsyncIterator[str]
    async def structured_output(messages, *, schema_hint, ...) -> Any
    async def health() -> tuple[bool, str]
```

`OllamaProvider` (default, and what the demo runs), `CloudProvider` (Anthropic or OpenAI),
`StubProvider` (deterministic test double). Application code only ever calls
`get_provider()`.

`structured_output` does not trust the model to emit clean JSON: it extracts the first
balanced JSON value from a possibly chatty completion, because small local models wrap
JSON in prose and fences. Every caller also has a deterministic fallback, so a malformed
response degrades rather than failing.

**No automatic cross-provider fallback.** An operator who chose a local model for
data-residency reasons must never have a prompt silently shipped to a cloud API because
the local server hiccuped. Provider failures surface as typed errors with actionable
messages (`ollama pull llama3.1:8b`, `Is ollama serve running?`).

Errors are mapped, not leaked: timeout → `MODEL_TIMEOUT` (504), missing model / unreachable
→ `MODEL_UNAVAILABLE` (503), missing key → `MODEL_NOT_CONFIGURED` (503).

## The agent layer (Pi Coding Agent)

The assignment requires the agent layer to be built on the Anthropic Claude Agent SDK or
the Pi Coding Agent. This build uses **Pi**, wired in as a provider behind the same
`LLMProvider` interface as Ollama and the cloud SDKs (`app/llm/pi_agent.py`).

```
Controller ──> retrieval ──> Evidence Pack ──┐
                                             ├─> Pi CLI subprocess ──> NDJSON ──> text
   memory ──> context builder ───────────────┘        (no tools)
                                             └─> grounding validator ──> response
```

**Why a subprocess.** Pi ships as a Node CLI with a headless mode (`--print --mode json`);
that is its supported non-interactive interface. Running it out-of-process is also a
feature here: a hung or crashed agent cannot take the API down with it, and the timeout
is enforced by killing a process rather than by hoping a library honours a deadline.

**Why every tool is disabled.** Pi is a *coding* agent — read, bash, edit, write. In a
grounded-answer path those tools are all downside: the evidence has already been
retrieved by the controller, and a filesystem or shell tool only adds ways for a model to
go somewhere it should not. The invocation is therefore
`--no-tools --no-session --no-extensions --no-skills --no-context-files
--no-prompt-templates`, in an empty temp directory. What survives is the part we want —
a bounded generation step — and the controlled architecture is preserved rather than
replaced by an autonomous loop.

**Credentials.** Passed through the child process's environment, never argv, because
`/proc/<pid>/cmdline` is world-readable and an environment is not.

**Failure mapping.** Pi reports failures as `errorMessage` on an assistant message with
`stopReason: "error"`. Those are mapped onto the same typed errors as every other
provider: `MODEL_NOT_CONFIGURED` for auth and unknown-model failures,
`MODEL_UNAVAILABLE` for an unreachable backend, `MODEL_TIMEOUT` for a stall. The UI
cannot tell which provider produced an error, and does not need to.

**Streaming** is coarse-grained: `--mode json` emits whole messages rather than token
deltas, so the SSE stream fills in larger steps than with Ollama. The contract is
unchanged.

**Claude Agent SDK, considered.** It works and is Python-native, but it depends on
`mcp` 2.0, which requires `starlette>=1.6` and breaks FastAPI 0.116. FastAPI was moved to
0.141.1 (`starlette>=0.46`, no upper bound) so both can coexist in one environment; Pi
was chosen because it can also drive a local model, which keeps the mandatory Ollama
demo running through the same agent code path rather than a second one.

---

## Grounding

```
answer
  ↓ strip citations pointing at sources that were never retrieved
  ↓ split into claim-sized units (sentences; skip questions, headings, meta-statements)
  ↓ score each claim against the pack:
      rare-word (IDF-weighted) containment + 0.25 · bigram overlap
      a claim citing [S2] is scored against S2 first
  ↓ decide
      evidence empty + claims present  → REFUSE   (replace with an explicit "not covered")
      supported ratio < 0.5            → ANNOTATE (keep, flag in the UI)
      otherwise                        → ACCEPT
```

**What it catches:** off-corpus drift into the model's own priors, and hallucinated
citations. Both are the common failure of a small local model.

**What it does not catch, stated plainly:** it is a lexical measure. It does not verify
truth, resolve paraphrase, or detect a fluent claim that reuses evidence vocabulary while
saying something the guest did not say. It is a practical safeguard, not factual
verification, and it costs ~1ms and zero model calls.

The upgrade path is an NLI model or an LLM judge per claim, with the same interface —
worth it once M1 evaluation shows where the misses actually are.

For artifacts, the *artifact body* is validated rather than the one-line chat message,
because that is where the claims live.

## Runtime model configuration

`.env` is the source of truth at boot. `POST /api/model/config` adds an in-process
override so an evaluator can switch provider or paste a key from the browser without
editing files and restarting (`app/llm/runtime_config.py`).

The constraints are the design:

- **Memory only.** No row, no file. Nothing to leak in a backup or a commit.
- **Write-only keys.** A key goes in; reads return `api_key_set` and the last four
  characters. Asserted by tests, including that no other endpoint echoes it.
- **Env wins on restart**, so an override can never quietly become a deployment's
  permanent state.
- **Vendor-scoped.** The "keep the existing key when the form is re-saved" convenience
  matches on `(provider, vendor)`. An earlier version matched on provider alone, which
  would have carried an Anthropic key over to an OpenAI endpoint when the user switched
  vendor — caught by a test, and the reason that test exists.
- **Switchable off** with `ALLOW_RUNTIME_MODEL_CONFIG=false`, the right setting for any
  shared deployment.

`POST /api/model/test` verifies the *submitted* configuration with one real call and
always restores the previous state, so a bad key never becomes the active provider.

---

## Artifact security

**Threat model.** Artifact HTML is written by a language model, steered by user text and by
transcript text we do not control. Assume it can contain anything an attacker could get a
model to emit. It is rendered in a browser next to a session the user trusts.

**Two independent layers.** Neither is trusted to be sufficient alone.

**Layer 1 — server-side sanitization** (`app/security/sanitizer.py`), before storage:

| Allowed | Removed |
|---|---|
| Structural/semantic tags: `section`, `article`, `h1`–`h6`, `p`, `ul`/`ol`/`li`, `table`/`tr`/`td`/`th`, `blockquote`, `figure`, `pre`, `code`, `span`, `div` | `script`, `iframe`, `object`, `embed`, `form`, `input`, `link`, `meta`, `base`, `svg`, `math` |
| `class`, `id`, `style`, `title`, ARIA attributes | every `on*` event handler |
| `href` with `http`/`https`/`mailto`/relative/anchor | `javascript:`, `vbscript:`, `data:text/html` |
| `<img src="data:image/…">` | remote image URLs (a tracking pixel with extra steps) |
| `<style>` blocks and inline `style`, CSS values parsed by tinycss2 against an extended property allowlist | `@import`, `url()` with a non-`data:` scheme, `expression()`, `behavior:`, `-moz-binding` |

The CSS property allowlist extends bleach's default (which predates flexbox and grid) so
generated layouts survive; values are still parsed and filtered.

**Layer 2 — the viewer**: `<iframe sandbox="" srcdoc=… referrerpolicy="no-referrer">`.
An empty `sandbox` grants *nothing*: no scripts, no same-origin, no forms, no top-level
navigation, no popups. `srcdoc` (rather than a blob URL) means the frame has an opaque
origin and no URL a user could be tricked into opening directly. The document also carries
its own CSP (`default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src
'none'; form-action 'none'; base-uri 'none'`).

**Why this shape.** The requirement is "generate complete HTML/CSS artifacts", so stripping
CSS would defeat the product. The answer is to keep presentation and remove *capability*:
an artifact can look like anything and do nothing. A sanitizer bypass alone is not enough
to execute script, because the sandbox blocks execution; a sandbox misconfiguration alone
is not enough, because the payload was already removed.

**Residual risk, honestly:** an artifact can still render misleading *content* — sanitizers
address capability, not deception. Sanitization also runs once at write time, so a bleach
CVE would not retroactively clean stored artifacts; `raw_content` is kept precisely so
stored artifacts can be re-sanitized and diffed.

Markdown takes a different path: the client renders it **without `rehype-raw`**, so
embedded HTML is displayed as text. The server strips genuinely dangerous constructs but
deliberately does *not* run the HTML sanitizer over Markdown, because entity-escaping would
corrupt legitimate prose (`a < b`, code samples, tables).

## API boundaries

REST for state, SSE for the one long operation.

```
GET  /health                          dependency-level status
GET  /api/model                       active provider + availability
POST /api/sessions                    create
GET  /api/sessions                    list (per external user id)
GET  /api/sessions/{id}               session + messages + artifacts
DEL  /api/sessions/{id}               delete (cascades)
POST /api/sessions/{id}/messages      chat turn; `stream: true` → SSE
POST /api/artifacts                   store (always sanitized)
GET  /api/artifacts, /{id}            list / fetch
GET  /api/memories …                  inspect / add / delete / clear
POST /api/ingestion, GET /stats       run ingestion / corpus stats
```

Everything is Pydantic in and out; the frontend mirrors the models in `lib/types.ts`.
Errors use one envelope with a stable `code`, so the UI branches on codes rather than
parsing prose.

**SSE, and one bug worth recording.** The stream emits `route → memory → retrieval →
evidence → token* → final`. The streaming worker owns *its own* database session with an
explicit lifecycle: the first implementation reused the request-scoped session, and when a
client disconnected mid-stream, dependency teardown raced with task cancellation and leaked
a checked-out connection — which showed up as a test-suite teardown that hung until the
statement timeout. See `agent-transcripts/`.

## Observability

Structured JSON, one line per event, `request_id` and `session_id` bound via contextvars so
every component is correlated without threading a logger through calls.

| Event | Key fields |
|---|---|
| `request.started` / `request.completed` | method, path, status_code, latency_ms |
| `agent.route_selected` | route, method, confidence |
| `memory.retrieved` / `memory.extracted` | count, stored, filtered_out |
| `retrieval.started` / `retrieval.completed` | strategy, retrieval_count, vector_hits, keyword_hits, candidates, latency_ms, degraded |
| `evidence.created` | evidence_count, episodes |
| `llm.started` / `llm.completed` / `llm.failed` | provider, model, latency_ms, output_tokens, code |
| `grounding.completed` | action, checked_claims, supported_claims, supported_ratio, citations_removed |
| `artifact.generated` / `artifact.sanitized` | artifact_type, had_script, removed_urls, stripped_bytes |
| `database.error` | stage, error |

A redaction processor blanks credential-shaped keys before rendering, so a careless
`log.info(..., api_key=…)` cannot leak one.

## Failure handling

| Failure | Behaviour | Code |
|---|---|---|
| Missing cloud key | typed error naming the variable | `MODEL_NOT_CONFIGURED` |
| Ollama unreachable | typed error: "Is `ollama serve` running?" | `MODEL_UNAVAILABLE` |
| Model not pulled | typed error: `ollama pull <model>` | `MODEL_UNAVAILABLE` |
| Model timeout | typed error, 504 | `MODEL_TIMEOUT` |
| Embedding model down | **degrade** to keyword-only, response marked degraded | — |
| Empty retrieval | assistant refuses without calling the model | in-band |
| Database down | 503, `/health` reports `down`, process still boots | `DATABASE_UNAVAILABLE` |
| Invalid session | 404 with the id | `SESSION_NOT_FOUND` |
| Sanitizer removed everything | 422 rather than an empty artifact | `SANITIZATION_FAILED` |
| Memory subsystem error | swallowed; chat continues; warning attached | — |
| Ingestion: one bad transcript | logged, skipped, run continues | — |
| Unexpected exception | generic message; trace only in logs | `INTERNAL_ERROR` |

The pattern: **degrade what is enhancement (memory, embeddings), fail loudly on what is the
product (grounding, model)**.

## Deployment topology

```
┌──────────── host ─────────────┐
│                               │
│  ollama serve :11434  ◀───────┼──── host.docker.internal
│                               │
│  ┌──── docker compose ─────┐  │
│  │ frontend :3000          │  │
│  │ backend  :8000          │  │
│  │ postgres :5432 (pgvector)│ │
│  │   volume: pgdata         │ │
│  │   mounts: data/transcripts (ro)
│  │           skills/          (ro)
│  └──────────────────────────┘ │
└───────────────────────────────┘
```

**Ollama is on the host on purpose**: it needs GPU/Metal acceleration that a container
would not get portably, evaluators usually have it already, and a multi-GB model layer
inside Compose makes first start miserable. `extra_hosts: host.docker.internal:host-gateway`
makes it reachable on Linux as well as macOS.

Migrations run in the backend's start command, so `docker compose up --build` really is one
command on a clean machine. Both images run as non-root; the frontend uses Next's
`standalone` output.
