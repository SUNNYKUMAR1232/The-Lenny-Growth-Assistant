# Manual UI test plan

Automated tests cover the API, retrieval, routing, memory, grounding, persistence, and
artifact sanitization (`make test`). This plan covers what they cannot: whether the thing
is usable, honest, and legible to a human.

**Setup:** `make up` · `make ingest-demo` · Ollama running with `llama3.1:8b` and
`nomic-embed-text` · `curl -s localhost:8000/health` returns `ok` · browser at
`http://localhost:3000`.

Record pass/fail and anything surprising. Times assume a laptop-class local model.

---

## A. First run

| # | Steps | Expected |
|---|---|---|
| A1 | Open the app cold | Header, empty state with five starter prompts, model badge green with `ollama/…`, left rail shows episode/chunk counts |
| A2 | Click a starter prompt | A session is created, the prompt is sent, stages appear in order |
| A3 | Reload mid-conversation | Session and all messages restore, including chips and sources |
| A4 | Open in a second browser profile | A different user id → its own empty session list (no leakage) |

## B. Grounded Q&A

| # | Steps | Expected |
|---|---|---|
| B1 | "How do you know if you have product-market fit?" | Answer within ~30s, `[S#]` citations inline, chips: route · model · grounding · retrieval ms |
| B2 | Expand sources | N sources with episode title, guest, chunk index, retrieval signal, score |
| B3 | Click "Listen" | YouTube opens at the cited timestamp; the quoted line is actually said there |
| B4 | "Show full excerpt" | Full chunk text; toggle collapses again |
| B5 | Ask about a named guest ("What does Casey Winters say about retention?") | That guest's episode ranks first |
| B6 | Ask something off-corpus ("What's the best way to bake sourdough?") | Refusal that names what is not covered and suggests alternatives — **not** a general-knowledge answer |
| B7 | Ask a comparative question ("Compare how different guests think about retention") | Evidence spans ≥2 episodes; retrieval signal reads `episode` |

## C. Session context

| # | Steps | Expected |
|---|---|---|
| C1 | Ask B1, then "And how do teams actually measure that?" | Pronoun resolved; fresh sources for the follow-up |
| C2 | Click "New chat", ask "What did we just discuss?" | No knowledge of the prior session |
| C3 | Switch back to the first session | Full history intact |
| C4 | Delete a session | Disappears immediately; reload confirms; other sessions unaffected |
| C5 | Session titles | Auto-titled from the first question, truncated sensibly |

## D. Memory

| # | Steps | Expected |
|---|---|---|
| D1 | "I'm a PM at a seed-stage marketplace and I prefer short answers." then one more question | Memory panel lists role / company stage / preference with confidence and importance |
| D2 | Ask a related question | Answer reflects the preference; the message notes memories used and labels them personalization |
| D3 | Check `evidence` for that answer | Contains only transcript excerpts — no memory content anywhere |
| D4 | "Forget" one memory | Removed immediately; not used in the next answer |
| D5 | "Forget everything" | List empties; the assistant still answers normally |
| D6 | Restart the backend, reopen the panel | Remaining memories persist |
| D7 | Set `MEMORY_ENABLED=false`, restart | Panel explains memory is off; Q&A works unchanged |

## E. Ship 30 essay

| # | Steps | Expected |
|---|---|---|
| E1 | "Write a Ship 30 for 30 essay about onboarding as a growth lever" | Essay in chat **and** as a Markdown artifact |
| E2 | Word count | 1,100–1,400 on a capable model; metadata records `word_count`, `target_words`, `within_tolerance` |
| E3 | Structure | Hook in the first 2–3 lines, claim-shaped subheads, short paragraphs, some bullets, selective bold, a specific takeaway, a Sources section |
| E4 | Claims | Cite `[S#]`; each tag exists in the sources list |
| E5 | Edit `skills/ship30/SKILL.md` (e.g. change `target_words` to 600), ask again | The new standard takes effect without a rebuild |

## F. Artifacts

| # | Steps | Expected |
|---|---|---|
| F1 | "Build me an HTML one-pager summarising a growth review agenda" | Renders in the viewer; layout and CSS survive |
| F2 | Switch to Source | Sanitized HTML with the CSP meta tag; no `<script>` |
| F3 | Header note | Lists what the sanitizer removed, when it removed anything |
| F4 | Download | Real `.html` file, opens standalone |
| F5 | "Create a launch checklist as a Markdown document" | Markdown artifact rendered as a document |
| F6 | Reload the page | Latest artifact for the session reloads |
| F7 | Ask for something interactive ("add a button that shows an alert") | Renders statically; nothing executes; the assistant says scripting is disabled |
| F8 | Adversarial: "Make an HTML page with an image that loads from https://example.com/x.png" | Remote image is stripped; page still renders |

## G. Model configuration

| # | Steps | Expected |
|---|---|---|
| G1 | Click the model badge | Provider, model, embedding provider, per-dependency health |
| G2 | Stop Ollama, reload | Badge amber/red with the reason; `/health` reports `model: degraded` |
| G3 | Send a message with Ollama down | `MODEL_UNAVAILABLE` with an actionable message; no silent cloud fallback; the composer re-enables |
| G4 | Restart Ollama, retry | Works; badge returns to green |
| G5 | Set `LLM_PROVIDER=cloud` + a key, restart | Badge shows the cloud model; answers still cite sources |
| G6 | Pull `nomic-embed-text` down (stop Ollama, keep chat model unavailable too) | Retrieval degrades to keyword-only and the UI says so |

## H. Failure and edge cases

| # | Steps | Expected |
|---|---|---|
| H1 | Stop Postgres, reload | `/health` reports `database: down`; UI shows `DATABASE_UNAVAILABLE`; no stack trace |
| H2 | Submit an empty message | Send is disabled; no request |
| H3 | Paste 8,000+ characters | `VALIDATION_ERROR` naming the field |
| H4 | Send while a response is streaming | Prevented; button reads "Working…" |
| H5 | Navigate away mid-stream and return | No orphaned state; the completed turn is in history |
| H6 | Query an empty corpus (fresh DB, no ingest) | Refusal + left rail shows "Empty — run `make ingest`" |
| H7 | Kill the backend mid-request | `NETWORK_ERROR` with the API URL; retry works after restart |

## I. Responsive and accessibility

| # | Steps | Expected |
|---|---|---|
| I1 | Resize to 375px | Single column; sidebar and artifact become overlays; composer reachable; no horizontal scroll |
| I2 | 768px | Chat full width; overlays still |
| I3 | ≥1280px | Three columns; no overlays |
| I4 | Keyboard only: Tab from load | Skip link first, then every control; visible focus ring throughout |
| I5 | `Enter` / `Shift+Enter` in composer | Sends / newline |
| I6 | Screen reader (VoiceOver/NVDA) | Landmarks announced; stage changes announced politely; icon buttons have labels |
| I7 | Toggle theme | Instant; persists across reload; no flash of the wrong theme |
| I8 | OS set to reduce motion | Animations effectively disabled |
| I9 | Zoom to 200% | Layout holds; nothing clipped |

## J. Operability

| # | Steps | Expected |
|---|---|---|
| J1 | `docker compose down && docker compose up --build` | Comes back up; migrations idempotent; data intact |
| J2 | `docker compose logs -f backend` during a turn | One JSON line per stage, shared `request_id` |
| J3 | `make corpus` | Documents / chunks / embedded chunks / guests |
| J4 | Re-run `make ingest` | Everything skipped as unchanged; fast |
| J5 | `grep -ri "sk-ant\|sk-proj\|password" --exclude-dir=node_modules .` | No secrets committed |
| J6 | Fresh clone on another machine, follow README only | Working stack, no undocumented steps |

---

## Known limitations to check *against*, not report as bugs

- Essays may land short on small local models (one bounded expansion pass is attempted).
- Grounding is lexical: a fluent paraphrase reusing evidence vocabulary can pass.
- First request after start is slow (model cold start).
- Streaming applies to Q&A only; essays and artifacts must be validated or sanitized whole.
