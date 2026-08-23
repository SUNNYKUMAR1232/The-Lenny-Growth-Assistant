# Verification log

Raw output from the build session. Everything below was run against a real PostgreSQL 16 +
pgvector 0.6 instance and the real 303-episode transcript archive. Model calls used the
deterministic stub provider and the deterministic embedder (see this folder's `README.md`
for why), so **retrieval, ingestion, persistence, routing, grounding mechanics, and
sanitization are verified; answer prose quality is not** — that requires the real model,
and is the point of running the demo on Ollama.

---

## 1. Database

```
$ pg_config --version
PostgreSQL 16.13

$ psql -d lenny -c "SELECT extversion FROM pg_extension WHERE extname='vector'"
 extversion
------------
 0.6.0

$ alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, Initial schema: users,
      sessions, messages, documents, chunks, memories, artifacts.
```

---

## 2. Ingestion — full corpus

```
$ time python -m app.scripts.ingest
… ingestion.progress  found=275 ingested=267 chunks=19170
… ingestion.progress  found=300 ingested=292 chunks=20919
… ingestion.completed documents_found=303 documents_ingested=295 documents_skipped=8
                      documents_failed=0 chunks_written=21102 chunks_embedded=21102
                      duration_seconds=119.27 error_count=0
{
  "documents_found": 303,
  "documents_ingested": 295,
  "documents_skipped": 8,      ← unchanged since an earlier run: idempotency working
  "documents_failed": 0,
  "chunks_written": 21102,
  "chunks_embedded": 21102,
  "duration_seconds": 119.27,
  "errors": []
}

real  1m59.870s
```

Corpus state afterwards:

```
$ python -m app.scripts.ingest --stats
{
  "documents": 303,
  "chunks": 21680,
  "embedded_chunks": 21680,
  "guests": 301,
  "embedding_models": ["deterministic-hash-768"]
}
```

303 episodes, 21,680 chunks, zero failures. With `nomic-embed-text` the wall time is
dominated by the embedding model rather than by Postgres.

---

## 3. End-to-end API — simple question (8-episode corpus)

```
$ curl -s -X POST localhost:8000/api/sessions -d '{"external_user_id":"tester"}'
$ curl -s -X POST localhost:8000/api/sessions/$S/messages \
       -d '{"content":"What makes onboarding such an important growth lever?"}'

ROUTE       KNOWLEDGE_Q
ANSWER      quotes Adam Fishman: "Onboarding is the only part of your product experience
            that a hundred percent of people are ever going to touch…" [S1]
EVIDENCE    S1  How to build a high-performing growth te…  Adam Fishman   0.0649  hybrid
            S2  Finding hidden growth opportunities in y…  Albert Cheng   0.0648  hybrid
            S3  How to build a high-performing growth te…  Adam Fishman   0.0645  hybrid
            S5  Finding hidden growth opportunities in y…  Albert Cheng   0.0573  vector
GROUNDING   {"checked_claims": 8, "supported_claims": 8, "supported_ratio": 1.0,
             "action": "accepted"}
WARNINGS    []
LATENCY     46.18 ms
```

The top hit for an onboarding question is the onboarding episode, surfaced by *both* legs
(`hybrid`).

---

## 4. End-to-end API — comparative question (full 21,680-chunk corpus)

```
$ curl -s -X POST localhost:8000/api/sessions/$S/messages \
       -d '{"content":"Compare what different guests say about retention versus acquisition"}'

route: KNOWLEDGE_Q | strategy: episode | retrieval_ms: 242.54 | total_ms: 264.38
episodes cited: 3 distinct episodes
grounding: {"checked_claims": 9, "supported_claims": 9, "supported_ratio": 1.0,
            "action": "accepted"}
```

The comparative phrasing switched the retrieval strategy to `episode` automatically, and
evidence spans three distinct episodes rather than one. Retrieval over 21,680 chunks took
243ms including the episode-window expansion queries — inside the <400ms p95 target in the
PRD.

**Honest caveat:** which three episodes were selected is not meaningful here, because the
deterministic embedder is lexical rather than semantic. With `nomic-embed-text` the vector
leg does the work it is designed for; this run verifies the *mechanics* (strategy
selection, expansion, diversity, latency at scale), not semantic ranking quality.

---

## 5. Structured logs — one turn

```json
{"event":"request.started","method":"POST","path":"/api/sessions/…/messages","request_id":"a94ae86c…"}
{"event":"agent.route_selected","route":"KNOWLEDGE_Q","method":"rule","confidence":0.85}
{"event":"retrieval.started","strategy":"chunk","top_k":8,"query_chars":53}
{"event":"retrieval.completed","strategy":"chunk","retrieval_count":8,"candidates":51,
 "vector_hits":30,"keyword_hits":30,"latency_ms":15.98,"degraded":false}
{"event":"evidence.created","evidence_count":8,"episodes":3}
{"event":"llm.started","skill":"rag","provider":"stub"}
{"event":"llm.completed","skill":"rag","model":"deterministic","latency_ms":0.59}
{"event":"grounding.completed","action":"accepted","checked_claims":8,
 "supported_claims":8,"supported_ratio":1.0,"citations_removed":0}
{"event":"request.completed","route":"KNOWLEDGE_Q","retrieval_count":8,
 "memory_count":0,"phase":"completed","latency_ms":45.87,"warnings":0}
```

Every line carries `request_id` and `session_id`. This is what makes a bad answer
debuggable after the fact: you can see what was retrieved, what the router chose, and what
the validator concluded.

---

## 6. Test suites

```
$ cd backend && python -m pytest
........................................................................ [ 60%]
...............................................                          [100%]
119 passed in 1.73s
```

```
$ cd frontend && npx vitest run
 ✓ tests/message-bubble.test.tsx   (5 tests)
 ✓ tests/artifact-viewer.test.tsx  (5 tests)
 ✓ tests/api.test.ts               (5 tests)

 Test Files  3 passed (3)
      Tests  15 passed (15)
```

```
$ cd frontend && npm run build
 ✓ Compiled successfully
 ✓ Generating static pages (4/4)

Route (app)                    Size     First Load JS
┌ ○ /                          52.7 kB         140 kB
└ ○ /_not-found                873 B          88.2 kB
```

Before the streaming-session fix (defect 4 in the build log) the backend suite took
**16.5s** and ended with a teardown timeout; afterwards it takes **1.7s** and is clean.
That delta was a leaked database connection, not slow tests.

---

## 7. UI verification (Playwright, real browser)

The running app was driven by a headless Chromium against the live backend and
screenshotted at each step. Images are in [`docs/images/`](../docs/images/).

| Screenshot | What it verifies |
|---|---|
| `ui-empty.png` | First-run empty state, starter prompts, model badge, corpus counts in the rail |
| `ui-answer.png` | A grounded answer with inline `[S#]` citations and the sources affordance |
| `ui-sources.png` | Expanded sources: episode, guest, chunk index, retrieval signal, score, "Listen" deep link |
| `ui-artifact.png` | Artifact route: chips (`Artifact` · `stub/deterministic` · `grounded 12/15` · `retrieval 19ms`), artifact rendered in the sandboxed viewer, sources for the artifact turn |

Console output during the run surfaced exactly two issues, both fixed at the source:

1. a 404 for a missing favicon → added `frontend/app/icon.svg`;
2. `The Content Security Policy directive 'frame-ancestors' is ignored when delivered via a
   <meta> element` → removed `frame-ancestors` from the artifact CSP with a comment
   explaining that the iframe `sandbox` attribute is what actually confines the document.

No other console errors, and no page errors.

---

## 8. What this log does **not** prove

- **Answer quality on a real model.** The stub quotes evidence back; it is not a language
  model. Run the demo on Ollama.
- **Semantic retrieval quality.** The deterministic embedder is lexical. Mechanics are
  verified; ranking quality needs `nomic-embed-text` and the M1 evaluation from the PRD.
- **Live cloud provider calls.** No API key in the build environment; configuration and
  error paths are unit-tested only.
- **Docker Compose end-to-end.** No Docker daemon in the build sandbox. The Dockerfiles and
  compose file are written and reviewed, and the same commands they run (`alembic upgrade
  head`, `uvicorn`, `next build`, `next start`) were all executed directly. **Run
  `docker compose up --build` once before submitting.**
