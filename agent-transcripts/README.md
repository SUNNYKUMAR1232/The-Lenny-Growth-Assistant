# Agent transcripts

This repository was built in one session with an AI coding agent (Claude, Opus 5, driving a
Linux sandbox with a real PostgreSQL 16 + pgvector 0.6 instance, Python 3.11, and Node 22).
These notes record how that actually went — including the things that broke and how they
were corrected — because "we used AI tools" is only useful information if you can see the
judgment applied on top.

Nothing here is reconstructed after the fact for presentation. Where a session limit
prevented something (Ollama could not be installed in the sandbox), that is stated rather
than papered over.

## Files

| File | What it contains |
|---|---|
| [`01-build-log.md`](01-build-log.md) | Phase-by-phase build log: decisions taken, and the seven real defects found during the build with the diagnosis and fix for each |
| [`02-verification-log.md`](02-verification-log.md) | Raw evidence: migration output, ingestion runs against the real corpus, end-to-end API responses, full test-suite runs, screenshots of the running UI |

## How the agent was directed

The working method, in the order it mattered:

1. **Architecture before code.** The pipeline (classify → retrieve → skill → validate →
   persist), the module boundaries, and the schema were decided first, so that generated
   code had a shape to fit into rather than inventing one per file.
2. **Real dependencies, not mocks, wherever it was possible.** PostgreSQL with pgvector was
   installed and running for the whole build; the full 303-episode transcript archive was
   cloned and ingested. Every claim in the README about retrieval, ingestion, and
   persistence was checked against a running system, not asserted.
3. **Tests as the specification for behaviour that matters.** Grounding, sanitization,
   memory isolation, and provider error mapping were written as tests first or immediately
   after, and *the tests found real bugs* (§ 01-build-log, defects 4–6). Where a test
   failed because the test was wrong rather than the code, that is recorded too.
4. **Verify by running, not by reading.** The API was exercised end-to-end with `curl`, and
   the UI was driven with Playwright and screenshotted, because "it compiles" is not
   evidence that it works.
5. **Refuse to fake anything.** No fabricated transcripts, no invented benchmark numbers, no
   "measured" metrics that were never measured. The PRD labels its metrics as targets. The
   README has a "What is not built" section.

## What the agent was not allowed to do

- **Invent transcript content.** The corpus is the public
  `ChatPRD/lennys-podcast-transcripts` archive, cloned at build time. With an empty corpus
  the product refuses to answer rather than falling back on model priors.
- **Claim untested paths work.** The Anthropic and OpenAI providers are implemented and
  unit-tested for configuration and error handling, but no live cloud call was made (no API
  key in the environment). The README says so.
- **Hide a limitation behind a euphemism.** The grounding validator is lexical; that is
  stated in the code docstring, the architecture doc, the PRD risk table, and the README.

## Environment limitation worth naming

Ollama could not be installed in the build sandbox — the installer host was not reachable
from the network policy in force:

```
$ curl -fsSL https://ollama.com/install.sh -o /tmp/ollama_install.sh
curl: (22) The requested URL returned error: 403
```

Consequences, handled honestly rather than hidden:

- The **Ollama provider is unit-tested against an `httpx.MockTransport`** covering
  `generate`, `stream`, missing-model (404), timeout, unreachable-server, and health checks
  — the paths that are painful to reproduce against a live server anyway.
- End-to-end runs used `LLM_PROVIDER=stub`, a **deterministic test double** that composes
  answers by quoting the evidence it was given. It exercises the whole pipeline (routing →
  retrieval → skill → grounding → persistence → sanitization) without pretending to be a
  language model: it reports itself as `stub/deterministic` in the API and the UI.
- Embeddings during the build used the deterministic hash embedder for the same reason. The
  real path is `nomic-embed-text`; the fallback is documented as non-semantic everywhere it
  appears.

**Before recording the demo video, run it on real Ollama** (`ollama serve`,
`ollama pull llama3.1:8b nomic-embed-text`, `LLM_PROVIDER=ollama`) — the assignment requires
the demo on a local model, and that is the configuration the repository ships as its
default.
