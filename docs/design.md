# Design

UI/UX principles, information architecture, interaction states, responsive behaviour, and
accessibility for the Lenny Growth Assistant.

---

## 1. Principles

**1. Provenance is the product.** Everything else is a chat window. Sources are one click
from every answer, each citation resolves to an episode *and a timestamp*, and the grounding
verdict is displayed whether it is good news or not. The interface's job is to make the
answer checkable, not just readable.

**2. Show the machinery when it is working, hide it when it is not.** A local 8B model
takes 10–30 seconds. An opaque spinner for that long reads as broken; "Searching the
transcript corpus… 8 excerpts · chunk" reads as working, and doubles as a live view of the
architecture during a demo. Once the answer arrives, the machinery collapses into three
small chips.

**3. Never look more confident than the system is.** Weak grounding is an amber chip, not
silence. Keyword-only retrieval says so. An unavailable model turns the badge amber and
explains itself. The interface is allowed to be less impressive if that is the truth.

**4. Reading comfort over density.** This is a text product: 3xl measure, 15px body, 1.6
line-height, generous whitespace between turns. No zebra striping, no card-in-card, no
decorative gradients.

**5. Restraint in colour.** One accent (indigo) for user messages and primary actions.
Semantic colour only where it carries meaning: green grounded, amber degraded, red failed.
Everything else is grey.

---

## 2. Information architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ☰  Lenny Growth Assistant          ● ollama/llama3.1:8b  Memory  ☾  ▤   │  header
├──────────────┬──────────────────────────────────┬───────────────────────┤
│ + New chat   │                                  │  Artifact Viewer      │
│              │  You: How do I know if I have    │  ┌─────────────────┐  │
│ Sessions     │  product-market fit?             │  │ HTML  title     │  │
│ · PMF        │                                  │  │ Preview│Source│⤓ │  │
│ · Retention  │  [Grounded answer] ollama/…      │  ├─────────────────┤  │
│ · Onboarding │  grounded 6/6 · retrieval 38ms   │  │                 │  │
│              │  ┌────────────────────────────┐  │  │  sandboxed      │  │
│              │  │ Rahul Vohra used the Sean  │  │  │  iframe         │  │
│              │  │ Ellis survey… [S1]         │  │  │                 │  │
│              │  │                            │  │  └─────────────────┘  │
│              │  │ › 6 sources from 3 episodes│  │  scripts disabled     │
│              │  └────────────────────────────┘  │                       │
│ Knowledge    │  ┌────────────────────────────┐  │                       │
│ 303 episodes │  │ Ask about product, growth… │  │                       │
│ 41k chunks   │  └────────────────────────────┘  │                       │
└──────────────┴──────────────────────────────────┴───────────────────────┘
     nav                    primary                      secondary
```

Three regions, ranked by how often they are used:

| Region | Contains | Why here |
|---|---|---|
| **Header** | title, model badge, memory, theme, artifact toggle (mobile) | Model identity is persistent context, not a setting buried in a menu — the assignment requires it visible, and during a demo it is the thing people ask about |
| **Left rail** | new chat, session list, corpus stats | Sessions are navigation. Corpus stats sit at the bottom because "is anything indexed?" is the first question when answers look wrong |
| **Centre** | conversation + composer | The primary surface; everything else can be collapsed |
| **Right** | Artifact Viewer | Beside the chat, never replacing it — an artifact is a *result of* the conversation, so both must be visible at once |

**Progressive disclosure.** Three layers: the answer (always), the metadata chips (always,
one line), the evidence (one click), the full excerpt (one more click), the raw artifact
source (a tab). Nothing important is hidden; nothing verbose is forced.

---

## 3. Key interaction states

### Composer
| State | Treatment |
|---|---|
| Empty | Placeholder naming both modes: "Ask about product, growth, retention… or ask for an essay or artifact" |
| Typing | Auto-growing textarea to 200px, then scrolls. `Enter` sends, `Shift+Enter` newlines |
| Sending | Input and button disabled, button reads "Working…" — the request is in flight and double-send is a real risk on slow models |
| Error | The composer re-enables immediately; the failed message is removed and the error is shown above, so the text can be retried |

### Assistant turn
| State | Treatment |
|---|---|
| Routing | "Classifying the request…" + route chip once known |
| Memory | "Checking what I remember about you…" (skipped visually if instant) |
| Retrieving | "Searching the transcript corpus…" |
| Evidence found | "Writing from the evidence…" + "8 excerpts · chunk" |
| Streaming | Tokens append in place, in plain text (Markdown renders after completion, so half-parsed syntax never flickers) |
| Validating | "Checking claims against the sources…" — brief but named, because it is a real stage |
| Complete | Chips: route · model · grounding · retrieval latency. Sources collapsed |
| Refused | The refusal reads as a decision, not an error: what is not covered plus what to try instead |
| Weakly grounded | Amber chip `weakly grounded (2/6)` and an inline note in the answer |


### Sources

The single most important UI decision in the product.

```
› 6 sources from 3 episodes                    ← collapsed by default
  [S1]  How to know if you have product-market fit
        Rahul Vohra · chunk 12 · hybrid · score 0.065      [Listen]
        "We used the Sean Ellis product-market fit…"
```

Collapsed by default — available in one click, never in the way of reading. The `[S1]` tag
matches the inline citation, so a reader can trace a specific sentence rather than the
answer as a whole. The retrieval signal (`hybrid`/`vector`/`keyword`/`episode`) is shown
because the operator debugging a bad answer needs it. **"Listen" opens the episode at the
second the quote was said** — verification in two clicks is what makes the grounding claim
credible instead of decorative. Excerpts clamp to three lines.

### Artifact viewer

Beside the chat, never instead of it — an artifact is a product of the conversation.
**Preview / Source tabs**: Preview is what you show, Source is what is actually stored, and
how a reviewer confirms the sanitizer did its job. Sanitization is reported in the header
("script tags removed · 1 remote URL blocked") because security work the user cannot see
builds no trust, and a footer states the guarantee: scripts, forms, and network requests are
disabled. Download yields a real `.html`/`.md` file.

### Model indicator

A header chip: status dot plus provider/model in monospace (`ollama/llama3.1:8b`) — an
identifier, and the value people read aloud in a demo. Green: reachable. Amber: impaired
(e.g. embeddings degraded). Red: the selected model is unavailable, with the reason and a
reminder that there is no automatic cloud fallback. Clicking opens per-dependency health.

### Memory

A right-hand drawer that states its own boundary: *"Personalization only. Never used as
evidence for what guests said."* Each row shows key, value, type, confidence and importance
— showing confidence makes the system legible, because memory is *inferred*, not
transcribed. **Forget** per row and **Forget everything** at the bottom, both immediate. In
the conversation, personalized answers name the memories used: personalization is disclosed,
not silent.

### Empty states
| Where | Treatment |
|---|---|
| No conversation | Heading, one line on how grounding works, five clickable starters — one per capability (Q&A, synthesis, comparison, essay, artifact) so the product teaches itself |
| No artifact | Icon + "Ask for a document, a checklist, or an HTML one-pager and it renders here" |
| No sessions | "No conversations yet." |
| No memories | Explains what would get remembered, rather than showing an empty list |
| Empty corpus | Left rail turns amber: "Empty — run `make ingest`" |

### Error states
Errors appear as a dismissible bar under the header with the **code** in monospace next to
the sentence. Codes are shown deliberately: an evaluator reading `MODEL_UNAVAILABLE` can
grep logs and docs. The message always says what to do (`Is ollama serve running?`), never
just what failed.

---

## 4. Responsive behaviour

| Breakpoint | Layout |
|---|---|
| **< 640px** | Single column. Sidebar and artifact viewer become full-screen overlays via header toggles. Message bubbles 85% width. Composer pinned to the bottom |
| **640–1024px** | Chat + composer full width; sidebar and artifact still overlay |
| **≥ 1024px** | Three columns: 256px rail · fluid chat · 46% artifact panel (max 2xl). No overlays |

The conversation column keeps a `max-w-3xl` reading measure at every size — a 2000px-wide
line of prose is unreadable no matter how much room there is.

Layout uses `h-dvh` so the mobile URL bar cannot push the composer off-screen, and only the
message list scrolls: header, composer, and artifact chrome stay put.

---

## 5. Accessibility

| Area | Implementation |
|---|---|
| Landmarks | `header`, `nav`, `main`, `aside`, `article` per message |
| Skip link | "Skip to message input" as the first focusable element |
| Focus | Global `:focus-visible` ring at 2px with offset; never removed |
| Live regions | Pipeline status is `role="status"` + `aria-live="polite"` so screen readers hear stage changes without interrupting |
| Labels | Every icon-only control has `aria-label`; the composer has a visually hidden `<label>` |
| State | `aria-expanded` on all disclosures, `aria-current="page"` on the active session, `aria-selected` on viewer tabs, `role="dialog"` + `aria-modal` on the memory panel |
| Keyboard | Everything reachable and operable; `Enter`/`Shift+Enter` in the composer; no keyboard traps |
| Contrast | Body text ≥ 7:1, muted text ≥ 4.5:1, chips ≥ 4.5:1 in both themes |
| Motion | `prefers-reduced-motion` reduces every animation to ~0 |
| Colour independence | Grounding state is text *and* colour ("grounded 6/6", "weakly grounded"), never colour alone |
| Frame | The artifact iframe has a descriptive `title` |

---

## 6. Visual system

| Token | Light | Dark |
|---|---|---|
| surface / raised / sunken | `#fff` / `#fafaf9` / `#f4f4f5` | `#0d0d0f` / `#18181b` / `#09090b` |
| line | `#e4e4e7` | `#27272a` |
| ink / muted / faint | `#18181b` / `#52525b` / `#8c8c96` | `#f4f4f5` / `#a8a8b0` / `#71717a` |
| accent | `#4f46e5` | `#818cf8` |
| ok / warn / danger | `#15803d` / `#b45309` / `#be3030` | `#4ade80` / `#f59e0b` / `#f87171` |

Colours are declared once as RGB triples on `:root`, redefined under `.dark`; no component
hard-codes a hex. Theme follows the OS, is overridable, persists in `localStorage`, and is
applied before first paint so there is no flash of the wrong theme.

Type: system sans stack, 15px body / 1.6, monospace only for identifiers. Spacing on a 4px
scale; radii 8/12/16px; one shadow, used only for overlays.

---

## 7. Design decisions worth defending

| Decision | Alternative | Why |
|---|---|---|
| Named pipeline stages instead of a spinner | Generic loading | 10–30s of opacity reads as broken; naming the stage is honest and demo-legible |
| Sources collapsed by default | Always expanded | Eight excerpts push the answer off-screen; one click is a low price |
| Streaming as plain text, Markdown after | Incremental Markdown | Half-parsed Markdown flickers and reflows; plain text is stable |
| Grounding verdict always shown | Only on problems | A badge that only appears when something is wrong trains people to ignore the absence |
| Artifact panel occupies a column at ≥1024px | Modal or drawer | Modals hide the conversation the artifact came from |
| Error codes shown to users | Friendly text only | This is an internal/evaluator tool; a code is greppable and speeds up support |
| One accent colour | Per-route colour coding | Route is already labelled in words; colour-coding three routes adds noise, not meaning |
| No avatars | Avatar per role | Alignment and colour already distinguish speakers; avatars cost vertical space in a text-dense product |
