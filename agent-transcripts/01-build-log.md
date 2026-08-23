# Build log

Chronological. Decisions on the left, what actually happened on the right. The interesting
part is the defects section: seven real bugs, six of them found by tests or by running the
system rather than by reading the code.

---

## Phase 0 — Environment

```
$ psql --version                    → PostgreSQL 16.13
$ apt-get install -y postgresql-16-pgvector
$ psql -d lenny -c 'CREATE EXTENSION vector'
$ psql -d lenny -c "SELECT extversion FROM pg_extension WHERE extname='vector'"
 0.6.0
```

Real Postgres + pgvector for the whole build. This is what made it possible to test
`GENERATED` tsvector columns, `websearch_to_tsquery`, cosine distance, and cascade deletes
instead of stubbing them.

```
$ git clone --depth 1 https://github.com/ChatPRD/lennys-podcast-transcripts
$ ls episodes | wc -l               → 303
$ du -sh episodes                   → 27M
```

Inspected the actual format before writing the loader: YAML frontmatter (guest, title,
`youtube_url`, `publish_date`, `duration`, keywords) and a body of
`Speaker (00:12:34):` blocks. **That timestamp is why citations deep-link into YouTube** —
it was not in the plan until the format was read.

Ollama install blocked (403). Recorded in `README.md` of this folder; the consequence was
the deterministic stub provider and MockTransport-based provider tests.

---

## Phase 1 — Architecture decisions taken up front

| Decision | Reasoning at the time |
|---|---|
| Controlled controller, not an autonomous tool-loop | The demo must run an 8B local model; small models mis-call tools and loop. Determinism also buys a unit test and a log line per stage |
| Evidence Pack as the only corpus→model channel | Makes grounding checkable and retrieval independently testable |
| Postgres for OLTP + FTS + vectors | 300 episodes / ~40k chunks does not justify a second datastore; joins to episode metadata stay free |
| Chunk on utterance boundaries | Podcast answers are self-contained; character-count chunking cuts mid-claim |
| Skill standards as on-disk `SKILL.md` | The assignment asks for a reusable skill, not a prompt in code; a writer should be able to change it in a diff |
| `sandbox=""` + server-side sanitizer | Two independent layers; neither trusted alone |
| No automatic cross-provider fallback | Silently shipping a prompt to a cloud API because the local server hiccuped is a data-residency incident, not a feature |

---

## Phase 2–6 — Backend

Built in dependency order: config/logging/errors → models + migration → embeddings →
retrieval → LLM gateway → context builder → grounding → skills → memory → controller →
API. Each layer was importable and exercised before the next was written.

First end-to-end run, before any frontend existed:

```
$ python -m app.scripts.ingest --limit 8
{"documents_ingested": 8, "chunks_written": 578, "chunks_embedded": 578, "duration_seconds": 2.0}

$ curl -X POST localhost:8000/api/sessions/$S/messages \
       -d '{"content":"What makes onboarding such an important growth lever?"}'
ROUTE      KNOWLEDGE_Q
EVIDENCE   S1 "How to build a high-performing growth team" — Adam Fishman — hybrid — 0.0649
GROUNDING  {"checked_claims": 8, "supported_claims": 8, "action": "accepted"}
LATENCY    46ms
```

The top hit for an onboarding question was Adam Fishman's onboarding episode. That is the
first evidence the retrieval design was sound, and it came from real data.

---

## Defects found and fixed

### 1. FastAPI 204 responses crashed at import

```
AssertionError: Status code 204 must not have a response body
```

**Cause.** With `from __future__ import annotations`, a `-> None` return annotation resolves
to `NoneType`, which FastAPI treats as a response model — and a response model is not
allowed with 204.

**Fix.** `response_model=None` on the two 204 endpoints. Worth recording because the failure
message points at the status code, not at the annotation, and the same trap will catch the
next person who adds a DELETE endpoint.

### 2. Response JSON used the ORM's attribute name, not the API's

Tests asserted `body["message"]["metadata"]` and got `KeyError: 'metadata'`.

**Cause.** SQLAlchemy reserves `metadata` on declarative models, so the ORM attribute is
`meta`. The schema used `Field(alias="meta")` to read it — but FastAPI serializes with
`by_alias=True`, so the *response* key became `meta` too. The contract silently changed
shape.

**Fix.** `validation_alias="meta"` (read from the ORM) + `serialization_alias="metadata"`
(write to the API). One line, but it is exactly the kind of drift that breaks a client at
integration time rather than at build time — and it was caught by a test asserting the
contract rather than the code.

### 3. pytest-asyncio loop-scope mismatch

Seven tests failed with `got Future attached to a different loop` and asyncpg protocol
errors.

**Cause.** A session-scoped fixture created the engine on one event loop; function-scoped
tests ran on another.

**Fix.** `asyncio_default_fixture_loop_scope = session` **and**
`asyncio_default_test_loop_scope = session` in `pytest.ini`. Not a product bug, but the
kind of thing that makes a suite flaky for whoever inherits it.

### 4. Leaked database connection on the streaming endpoint *(a real production bug)*

**Symptom.** The suite passed but took 16 seconds and ended with:

```
ERROR at teardown … DROP TABLE artifacts
asyncpg.exceptions.QueryCanceledError: canceling statement due to statement timeout
```

**Diagnosis.** `DROP TABLE` needs `ACCESS EXCLUSIVE` and was waiting on an open
transaction. Something held a connection after the SSE test finished. The SSE endpoint ran
its worker task on the *request-scoped* session; when the response generator ended, task
cancellation raced with FastAPI's dependency teardown, and a checked-out connection was
never returned. SQLAlchemy said so out loud:

```
The garbage collector is trying to clean up non-checked-in connection …
```

**Fix (two parts, both correct beyond the test):**

1. The streaming worker now owns its own session with an explicit `async with` lifecycle,
   loading the session row itself, instead of borrowing the request's.
2. The stream's `finally` awaits the worker after cancelling it, so it unwinds and returns
   its connection when a client disconnects mid-stream.

Suite: **16.5s → 1.05s**, no teardown errors. In production this was a connection leak per
abandoned stream — a browser tab closed mid-answer would have leaked one every time.

### 5. "No evidence" could never happen

The test asserting that a nonsense query returns an empty Evidence Pack failed: it got six
results.

**Cause, and it is a design bug not a test bug.** Vector search always returns *k* nearest
neighbours. There is no such thing as "no match" in a kNN query — so the refusal path,
which the entire grounding story depends on, was unreachable whenever the corpus was
non-empty.

**Fix.** `RETRIEVAL_MIN_VECTOR_SIMILARITY` (default 0.05) drops near-orthogonal hits before
fusion. Measured against the fallback embedder: an unrelated query scored 0.0 while
"how do I measure retention" scored 0.104 against the retention chunk — so the floor
separates junk from weak-but-real. The value is documented as **model-dependent** (raise it
to 0.4–0.6 for an embedder whose unrelated-pair similarity runs high), with grounding
validation named as the model-independent backstop.

The test was also rewritten to be honest: the strongest assertion is made against a
genuinely empty corpus, which is the condition the RAG skill short-circuits on.

### 6. Two sanitizer holes, found by the security tests

**6a. Remote images were allowed.** `<img src="https://tracker.test/pixel.gif">` survived,
because the URL check allowed `http`/`https` for any attribute. A remote image in generated
HTML is a tracking pixel with extra steps: it leaks the viewer's IP and referrer to a host
the model chose. **Fix:** a per-tag rule — `img.src` must be a `data:image/` URI.

**6b. Inline styles were being emptied.** `style="color:#333"` came out as `style=""`,
which would have made every generated artifact look broken. bleach ≥6 strips style contents
unless a `CSSSanitizer` is supplied. **Fix:** added `tinycss2` and a `CSSSanitizer` whose
property allowlist extends bleach's default — that default predates flexbox and grid, so
without the extension every generated layout would have collapsed. Values are still parsed
and filtered, and `@import`/`url()`/`expression()` are removed separately.

Both were found by tests written from an attacker's list of payloads, not by reading the
sanitizer.

### 7. Stub provider matched the system prompt as evidence

The deterministic provider's evidence regex was loose enough to capture a line of the
rules block as if it were a retrieved excerpt.

**Fix.** Anchored the pattern to the exact block shape the context builder emits
(`^[S1] header\n"""\n…\n"""`). Only affects the test double, but a test double that lies
makes every test built on it worthless.

---

## Verification, not vibes

- **119 backend tests**, all green, against real Postgres + pgvector, in ~1.7s.
- **15 frontend tests** (SSE parsing, artifact sandbox attributes, message metadata).
- **Production build** of the Next.js app: clean.
- **The UI was driven with Playwright** and screenshotted: empty state, grounded answer with
  sources expanded and deep links, and an HTML artifact rendered in the sandboxed viewer.
  Two console messages surfaced and were fixed at the source: a missing favicon (added
  `app/icon.svg`) and `frame-ancestors` being ignored in a `<meta>` CSP (removed from the
  artifact CSP, with a comment explaining that the iframe `sandbox` is what confines the
  document).

Raw output in [`02-verification-log.md`](02-verification-log.md).

---

## Things deliberately left undone

Recorded here so they are decisions, not oversights:

- **No evaluation harness.** The PRD proposes M1–M8 and labels them targets. Building a
  scorer and then reporting its numbers as "results" would be the dishonest version.
- **No cross-encoder reranker.** ~10× retrieval latency and a second model server for a
  marginal gain at 40k chunks.
- **No live cloud provider run.** No API key in the build environment; the paths are
  unit-tested and the limitation is in the README.
- **No auth.** Out of scope for a local evaluation build; `get_or_create_user` is the single
  seam where it attaches.
