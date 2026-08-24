# PRD — The Lenny Growth Assistant

**Status:** built and running · **Author:** Forward Deployed Engineer engagement
**Date:** August 2026

---

## 1. Forward deployment brief

### 1.1 The engagement

A product and growth team asked for an internal assistant built on Lenny's Podcast
transcripts. Their people want grounded answers, reusable written content, and rendered
artifacts — without learning prompts, models, or infrastructure. The brief was incomplete
in the ways client briefs usually are: it did not say who the primary user is, what
"grounded" has to mean in practice, or how the thing gets operated after handover. Section
1.4 records the assumptions I made and why.

### 1.2 User and problem

**Primary user: a product or growth lead at a startup (Series A–C), 5–15 years in.**
They already listen to the podcast. Their job-to-be-done is not "learn about growth" — it
is **"make a decision this week and be able to defend it."** Concretely: shape a
retention strategy, design an activation experiment, prepare a recommendation for a
leadership review, write a post that makes their thinking public.

**Secondary user: the internal operator** — the engineer who runs, monitors, and extends
the system after handover. Optimizing only for the first user and ignoring the second is
how forward-deployed projects die three weeks after the demo.

**The pain, precisely:**

| Pain | Today | Cost |
|---|---|---|
| The answer exists but is unfindable | It is 40 minutes into an episode you half-remember | 20–60 min per question, usually abandoned |
| Generic AI answers are unusable | ChatGPT gives you the average of the internet's opinion about retention | Confident advice with no provenance you can defend in a review |
| You cannot cite what you cannot locate | "I think someone on Lenny's said…" | The recommendation loses its authority |
| Turning insight into writing is slow | Blank page, every time | Hours per post; most never get written |

**What the assistant removes:** the search cost, the provenance problem, and the blank
page — while making its own limits visible. When the corpus does not cover a question, it
says so rather than manufacturing an answer, because a confident wrong answer costs more
than no answer.

### 1.3 Success metrics

Proposed product and operational metrics. **These are targets and instrumentation plans,
not measured results** — no evaluation harness was run in this build (see §9).

| # | Metric | Definition | Target | How it is measured |
|---|---|---|---|---|
| **M1** | **Grounded-answer rate** *(primary)* | Share of answers to a 30-question evaluation set where every factual claim is supported by a retrieved excerpt, judged by a human | ≥ 90% | Manual scoring against the logged Evidence Pack |
| M2 | Unsupported-answer refusal rate | Share of deliberately off-corpus questions the assistant declines instead of answering | ≥ 95% | 15 off-corpus probes; `grounding.action == "refused"` in logs |
| M3 | Retrieval latency (p95) | `retrieval.completed.latency_ms` | < 400 ms | Structured logs |
| M4 | Median response latency (local model) | `request.completed.latency_ms`, `llama3.1:8b` on a developer laptop | < 20 s | Structured logs |
| M5 | Citation click-through | Share of grounded answers where the user opens ≥ 1 source | ≥ 25% | Frontend event (not yet instrumented) |
| M6 | Successful local startup rate | Fresh clone → `/health` = `ok`, following only the README | ≥ 95% | Onboarding sessions with new engineers |
| M7 | Artifact rendering success rate | Artifacts that render non-empty after sanitization | ≥ 98% | `artifact.sanitized` + empty-output errors |
| M8 | Ship 30 length adherence | Essays within 1,100–1,400 words | ≥ 80% | `skill.within_tolerance` in message metadata |

M1 is the one that matters. This product's value is provenance; an ungrounded answer is a
worse ChatGPT.

### 1.4 Assumptions

| # | Assumption | Why | If wrong |
|---|---|---|---|
| A1 | The public `ChatPRD/lennys-podcast-transcripts` archive is an acceptable source | It is complete (303 episodes), structured, and openly licensed | The loader takes Markdown/JSON/text; swap the directory |
| A2 | Single-tenant, unauthenticated local evaluation | The brief describes an internal tool being evaluated, not a hosted product | Auth replaces one function (`get_or_create_user`); the schema already has users |
| A3 | English-only | The corpus is English | FTS config and prompts would need per-language handling |
| A4 | Evaluators have Ollama or can install it | The assignment requires the demo to run locally | Cloud provider works with one env change |
| A5 | A laptop-class local model (8B) is the quality baseline | That is what the demo runs on | Prompts stay explicit and defensive; the same code runs a frontier model |
| A6 | Sponsor reads and outros are not evidence | They are high-frequency and would pollute retrieval | Cleaning rules are conservative and easily reverted |
| A7 | ~300 episodes / ~40k chunks is the working corpus size | Measured from the archive | Beyond ~1M chunks, revisit the index and the flat rerank |
| A8 | Users tolerate 10–30s local latency if they can see progress | Local inference is slow; opacity is what feels broken | SSE surfaces every pipeline stage |

### 1.5 Scope

**In scope (built):**

- Grounded conversational Q&A with inline citations and deep links into episodes
- Independent, persisted sessions with follow-up context
- Hybrid retrieval (vector + full-text), fused and reranked, with episode-level expansion
  for comparative questions
- An explicit Evidence Pack as the only channel from corpus to model
- Controlled agent with three skills: RAG, Ship 30 essay, Artifact
- Ship 30 writing standard as an editable on-disk skill file
- Markdown and HTML/CSS artifacts, sanitized and sandboxed, rendered beside the chat
- Persistent user memory with extraction filtering, query-dependent retrieval, and user
  controls to inspect and delete
- Grounding validation with three outcomes: accept, annotate, refuse
- Local (Ollama) and cloud (Anthropic/OpenAI) providers behind one interface
- Structured JSON logs, dependency-level health, typed errors, graceful degradation
- Docker Compose startup, migrations, 134 automated tests, full documentation

**Explicitly out of scope, and why:**

| Excluded | Reason |
|---|---|
| Authentication / RBAC | Local evaluation build; adds surface without informing the evaluation |
| Hosted multi-tenant deployment | The brief is "run it locally and evaluate" |
| Fine-tuning | Retrieval quality dominates at this corpus size; fine-tuning is expensive and unfalsifiable here |
| Cross-encoder reranking | ~10× latency for a marginal gain over fused RRF at 40k chunks |
| Audio/video processing | Transcripts already exist; ASR is a different project |
| Real-time transcript sync | The archive updates weekly at most; `make transcripts && make ingest` covers it |
| Redis / Kafka / Elasticsearch / a separate vector DB | See the trade-off in §8 — Postgres does all three jobs at this scale |
| Streaming for essays and artifacts | They must be validated or sanitized as a whole; streaming would show text the pipeline might revise |

### 1.6 Risks and trade-offs

| Risk | Likelihood | Impact | Mitigation | Residual |
|---|---|---|---|---|
| **Hallucination** — claims that no guest made | High without controls | Severe: destroys the product's only differentiator | Evidence Pack is the sole content channel; refusal on empty evidence; grounding validator; hallucinated citations stripped; UI shows sources | A fluent paraphrase reusing evidence vocabulary can pass a lexical check. Documented; NLI is the upgrade path |
| **Local-model quality** — an 8B model is weaker at long-form and instruction-following | High | Medium: short essays, occasional format drift | Explicit prompts; one bounded expansion pass; defensive JSON extraction; deterministic routing instead of tool-calling | Essays may land at 1,000 words on small models |
| **Latency** — 10–30s on a laptop | High | Medium: feels broken | SSE token streaming + named pipeline stages; retrieval is ~15–40ms of the total | Cold start on the first request is still slow |
| **Unsafe artifact rendering** — XSS via generated HTML | Medium | Severe | Server-side allowlist sanitizer + `sandbox=""` iframe + document CSP + no remote resources; 12 payload tests | A sanitizer bypass would still be confined by the sandbox — two layers, independent |
| **Data leakage** — prompts going somewhere unexpected | Low | Severe | No automatic cloud fallback; provider always visible; keys redacted in logs; no telemetry | Operator can still misconfigure `LLM_PROVIDER` |
| **Retrieval miss** — the answer exists but is not retrieved | Medium | Medium | Hybrid legs cover paraphrase *and* proper nouns; episode expansion for comparatives; diversity cap | Silent misses are the hardest failure to detect; M1 evaluation is the instrument |
| **Memory contaminating evidence** | Low by construction | Severe if it happened | Separate tables, separate prompt blocks, separate response fields, memory excluded from grounding | Prompt-level separation depends on the model respecting labels; the response contract does not |
| **Corpus staleness** | Medium | Low | Hash-based idempotent re-ingest; `make transcripts` | Nobody re-runs it unless prompted; a cron job is the fix |
| **Operator abandonment** — nobody can run it after handover | Medium | Severe | One-command startup, health that names the broken dependency, actionable error messages, troubleshooting table, CI | — |

---

## 2. User flows

### Flow 1 — Grounded question (the core loop)

```
User opens the app → sees model badge (ollama/llama3.1:8b) and corpus size
  → types "How do you know if you have product-market fit?"
  → UI: "Classifying…" → "Searching the transcript corpus…" → "Writing from the evidence…"
  → answer streams in with [S1] [S2] citations
  → header shows: Grounded answer · ollama/llama3.1:8b · grounded 6/6 · retrieval 38ms
  → user expands "6 sources from 3 episodes"
  → clicks "Listen" → YouTube opens at the exact second the quote was said
```

**Acceptance:** answer cites ≥1 source; every source is resolvable to episode + guest +
timestamp; the turn is persisted; latency is visible.

### Flow 2 — Follow-up in session context

```
"And how do teams actually measure that?"
  → prior turns are in context; the assistant knows "that" = product-market fit
  → new retrieval for the new question; new evidence; new citations
```

**Acceptance:** the follow-up resolves the pronoun; a *different* session started at the
same moment shares nothing.

### Flow 3 — Personalization

```
"I'm a PM at a seed-stage marketplace and I prefer concise answers."
  → extractor proposes {role, company_type, preference}; filter keeps the confident ones
  → next answer is shorter and marketplace-flavoured
  → the message shows: "Personalized using 3 remembered details … personalization only, not evidence"
  → user opens Memory → sees each fact with confidence and importance → can Forget any of them
```

**Acceptance:** memories are visible and deletable; they never appear in `evidence`;
disabling memory leaves the assistant fully functional.

### Flow 4 — Ship 30 essay

```
"Write a Ship 30 for 30 essay about onboarding as a growth lever"
  → router: SHIP30 → episode-level retrieval (more material)
  → skills/ship30/SKILL.md is loaded from disk and injected as the standard
  → ~1,250-word essay with hook, claim-shaped subheads, bullets, bold, takeaway, sources
  → also lands in the Artifact Viewer as a Markdown artifact
  → metadata records word_count, target, within_tolerance, expansion_pass
```

**Acceptance:** 1,100–1,400 words; claims cite `[S#]`; a writer can change the standard by
editing one Markdown file.

### Flow 5 — Artifact

```
"Build me an HTML one-pager summarising a growth review agenda"
  → router: ARTIFACT → format chosen deterministically (html)
  → model output is sanitized server-side before storage
  → viewer renders it in a sandboxed iframe; header lists what the sanitizer removed
  → Download saves the file; Source shows exactly what is stored
```

**Acceptance:** no script executes; the artifact persists and reloads with the session.

### Flow 6 — Degradation (the operator's flow)

```
Ollama is down
  → /health: model degraded, with the reason
  → model badge turns amber and explains
  → sending a message → MODEL_UNAVAILABLE with "Is `ollama serve` running?"
  → no silent cloud fallback

Embedding model is down
  → retrieval degrades to keyword-only, the answer still arrives, the UI says so
```

---

## 3. Acceptance criteria

| # | Criterion | Verified by |
|---|---|---|
| AC1 | A knowledge question returns an answer citing ≥1 real transcript excerpt with a resolvable URL | `test_chat_flow.py::test_knowledge_question_returns_grounded_answer` + manual |
| AC2 | With no matching evidence, the assistant refuses instead of answering | `test_chat_flow.py::test_question_with_no_evidence_refuses_instead_of_inventing` |
| AC3 | Sessions are independent; messages persist across reloads and restarts | `test_chat_flow.py::test_sessions_are_isolated`, `test_persistence.py` |
| AC4 | Routing sends questions → RAG, essays → Ship30, documents → Artifact, deterministically | `test_routing.py` (9 parametrized cases) |
| AC5 | Ship 30 essays are ~1,250 words and follow the on-disk standard | `test_chat_flow.py::test_ship30_route_produces_markdown_artifact` + manual review |
| AC6 | Generated HTML cannot execute script in the app | `test_artifact_security.py` (12 payloads) + `artifact-viewer.test.tsx` |
| AC7 | Model provider is switchable by env alone and always visible in the UI | `test_llm_gateway.py::test_factory_never_silently_falls_back`, model badge |
| AC8 | Memory is inspectable and deletable, and never used as evidence | `test_memory.py::test_memory_is_never_presented_as_evidence`, memory panel |
| AC9 | A memory failure does not break question answering | `test_memory.py::test_memory_retrieval_failure_does_not_break_chat` |
| AC10 | `docker compose up --build` yields a working stack with migrations applied | Manual, documented in README §4 |
| AC11 | Every failure mode returns a typed error, never a stack trace | `test_api.py`, `test_llm_gateway.py`, exception handlers |
| AC12 | Re-ingestion is idempotent | `test_ingestion.py::test_ingestion_is_idempotent` |

---

## 4. Implementation plan (as executed)

| Phase | Delivered |
|---|---|
| 1 · Discovery | Read the brief, chose the architecture, wrote the PRD and architecture docs |
| 2 · Backend foundation | FastAPI, config, structured logging, typed errors, SQLAlchemy models, Alembic migration, sessions/messages/health |
| 3 · Knowledge base | Loader, cleaner, chunker, embeddings + fallback, pgvector, FTS, hybrid retrieval, reranker, Evidence Pack |
| 4 · AI layer | Model gateway (Ollama/cloud/stub), context builder, controlled controller, router, RAG skill, grounding validator |
| 5 · Memory | Extractor (LLM + heuristics), confidence/importance filter, manager with upsert + eviction, query-dependent retriever, user-control API |
| 6 · Content skills | `skills/ship30/SKILL.md`, Ship30 skill with length enforcement, artifact skill, sanitizer, artifact persistence |
| 7 · Frontend | Next.js workspace: chat, SSE streaming, sessions, sources, artifact viewer, memory panel, model badge, states, responsive, a11y |
| 8 · Security & ops | Sanitizer hardening, sandboxed viewer, Docker Compose, Makefile, `.env.example`, CI |
| 9 · Tests | 119 backend + 15 frontend tests; fixed two real bugs they surfaced (see `agent-transcripts/`) |
| 10 · Docs & demo | README, PRD, architecture, design, manual test plan, demo script, agent transcripts |

---

## 5. Non-goals

Not "a chatbot with RAG bolted on." The product is a **controlled AI knowledge assistant**:
memory personalizes, RAG grounds, the agent orchestrates, evidence constrains, the
validator verifies, the model generates, and the artifact sandbox protects. Each of those
is a separate, replaceable component with its own tests — that is the deliverable, not the
chat window.
