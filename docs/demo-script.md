# Demo script — 2:50

Camera on. Local model. One browser window, one terminal behind it.

## Before you hit record

```bash
ollama serve                       # keep it running
ollama list                        # llama3.1:8b and nomic-embed-text present
make up
make corpus                        # confirm episodes/chunks are indexed
curl -s localhost:8000/health      # everything "ok"
```

- Pre-warm the model with one throwaway question — the first request pays cold-start cost.
- Open `http://localhost:3000` and start a **new chat** so the screen is clean.
- Have a second terminal ready with `docker compose logs -f backend` for the log moment.

---

## 0:00–0:20 · The problem

> "A product team asked for an assistant over Lenny's Podcast transcripts. The answers they
> need already exist — 300 episodes of them — but they're 40 minutes into an episode
> nobody can find, and a generic chatbot will happily invent a quote instead. So the
> requirement isn't 'a chatbot with RAG'. It's an assistant whose every claim can be traced
> back to something a guest actually said."

## 0:20–0:50 · Grounded answer + sources

Type: **"How do you know if you have product-market fit?"**

While it runs, narrate the visible stages:

> "It classifies the request, checks what it remembers about me, searches the corpus —
> vector and full-text together — and only then writes, from the evidence it retrieved."

When it lands:

> "Six of six claims matched the retrieved excerpts. Retrieval took 38 milliseconds."

Expand **sources** → click **Listen** on one → YouTube opens at the exact second.

> "That's the whole product in one click: the citation resolves to the moment the guest
> said it."

## 0:50–1:10 · Session context

Type: **"And how do teams actually measure that?"**

> "No repeated context, no re-stating the question — the session carries it. It resolves
> 'that' to product-market fit, then retrieves *fresh* evidence for the new question. Each
> session is independent and persisted in Postgres; a new chat shares nothing."

## 1:10–1:30 · Memory

Type: **"I'm a PM at a seed-stage marketplace and I prefer short, practical answers."**
Then: **"What should I focus on first?"**

Open the **Memory** panel.

> "It extracted three durable facts, each with a confidence and an importance — only
> confident, important ones are stored, not every message. I can see all of it and delete
> any of it.
> And the boundary that matters: memory personalizes the answer, it is **never** evidence.
> Different tables, different prompt blocks, different response fields — and the grounding
> validator only ever checks transcript evidence."

## 1:30–1:50 · Ship 30 essay

Type: **"Write a Ship 30 for 30 essay about onboarding as a growth lever."**

While it writes, show `skills/ship30/SKILL.md` in the editor:

> "The writing standard is a file on disk, not a prompt buried in Python. Hook rules,
> subheads as claims, 1–3 sentence paragraphs, a specific takeaway, the target word count
> in frontmatter. A writer can improve this in a pull request, and in Docker it's mounted
> read-only so the change takes effect without a rebuild."

Result: ~1,250 words with citations, also rendered in the Artifact Viewer.

## 1:50–2:10 · Artifact + sandbox

Type: **"Turn the key points into an HTML one-pager I can share."**

> "The model writes HTML. That HTML is untrusted, so it goes through an allowlist sanitizer
> before it's stored — scripts, event handlers, iframes, remote images, unsafe CSS all
> removed — and then renders in an iframe with `sandbox=\"\"`, which grants nothing: no
> scripts, no same-origin, no network. The header tells you what was removed."

Click **Source** to show what is actually stored, then **Download**.

## 2:10–2:30 · Local model

Click the **model badge**.

> "This whole demo has been running on `llama3.1:8b` on this laptop, with
> `nomic-embed-text` for embeddings. Switching to Claude is one environment variable —
> `LLM_PROVIDER=cloud` — no code change, because everything goes through one provider
> interface. And there's deliberately **no** automatic fallback: if you chose a local model
> for data-residency reasons, a hiccup must never silently ship your prompt to a cloud API."

Optionally show the logs terminal:

> "Structured JSON, one line per stage, correlated by request id."

## 2:30–2:50 · One trade-off

> "The trade-off I'd defend hardest: **the agent is controlled, not autonomous.** Every
> turn walks the same pipeline — classify, retrieve, execute one of three skills, validate,
> persist. The model never picks a plan or calls tools in a loop.
>
> That costs me adaptive multi-hop retrieval. What it buys is predictable latency, a unit
> test per stage, a log line per stage, and no runaway tool loop in front of a customer —
> which matters more on an 8B local model than any autonomy would.
>
> Same instinct behind the infrastructure: one PostgreSQL doing transactions, full-text
> search, and vectors. At 300 episodes, adding a vector database and a search cluster would
> buy nothing and cost the client three more things to operate."

---

## If something goes wrong on camera

| Symptom | Say this, do this |
|---|---|
| Model is slow | "That's a local 8B model on a laptop — the stage indicator shows exactly where the time goes." Keep talking; it finishes |
| Model badge amber | "Ollama isn't up — notice the app tells me which dependency failed instead of a generic error." `ollama serve`, refresh |
| Answer is refused | Feature, not bug: "It refused rather than inventing an answer. That's the behaviour I optimised for." |
| Essay is short | "Small local models under-write; the skill runs one bounded expansion pass. A larger model closes the gap — the word count is in the message metadata." |

## Recording checklist

- [ ] Camera on, face visible
- [ ] 2–3 minutes, not 6
- [ ] Ollama visibly local (badge + `ollama list`)
- [ ] Sources expanded and one Listen link clicked
- [ ] Artifact rendered in the viewer
- [ ] One trade-off explained in your own words
- [ ] Uploaded to YouTube, link in the submission form
