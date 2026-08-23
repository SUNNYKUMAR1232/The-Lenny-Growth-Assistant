---
name: ship30
version: 1.0.0
description: >
  Turn grounded transcript evidence from Lenny's Podcast into a ~1,250-word
  Ship 30 for 30-style essay: one idea, a hook that earns the next line,
  skimmable structure, and a takeaway the reader can use today.
target_words: 1250
tolerance_words: 150
routes: [SHIP30]
sources:
  - https://www.ship30for30.com/post/how-to-write-an-atomic-essay-a-beginners-guide
  - https://www.ship30for30.com/
---

# Ship 30 for 30 — Essay Skill

This file is the writing standard. It is loaded from disk at request time and
injected into the prompt, so the rules can be reviewed, diffed and improved by
a writer without touching application code. **Editing this file changes
product behaviour.**

## Origin of these rules

Ship 30 for 30 teaches the *atomic essay*: one idea, 250 words, published as
an image, written to be skimmed on a phone. Its published guidance is the
basis for everything below — the headline that names an audience and promises
an outcome, the "does the body deliver exactly what the headline promised"
test, heavy use of bold and lists, and the editing question **"am I making
this easy to read?"**

This product asks for ~1,250 words, roughly five atomic essays' worth. The
discipline does not relax at that length — it compounds. A long essay is a
*sequence* of atomic units, each earning the next, not an atomic essay with
padding.

## Non-negotiables

1. **One idea.** The whole essay defends a single thesis. If a paragraph does
   not advance it, cut the paragraph.
2. **~1,250 words** (±150). Not 700. Not 2,000.
3. **Every claim about Lenny's Podcast is grounded in the EVIDENCE block** and
   carries its source tag inline: `[S1]`, `[S2]`. No evidence, no claim.
4. **Never invent** a quote, a guest, a company, a metric, or a framework
   name. If the evidence does not support the essay's spine, say what the
   evidence *does* support and write that essay instead.
5. **No filler openers.** Never begin with "In today's fast-paced world", "As
   product managers, we all know", or a restatement of the prompt.

## Structure

### Title
A headline carrying as many of these as fit naturally: **who it is for**,
**the topic**, **the promise or outcome**, **the scope** ("three", "the two
questions"), and a reason to feel something. Sentence case. No colons stacked
on colons.

> Good: *The onboarding audit that finds your activation leak in an afternoon*
> Bad: *Thoughts on onboarding: a deep dive into activation, retention, and growth*

### The hook — first 2-3 lines
The reader decides here. Use one of:

- **The counter-consensus**: state what most teams believe, then that it is
  wrong.
- **The specific number**: a concrete figure from the evidence, unexplained
  for one beat.
- **The named moment**: a guest's real situation, in one sentence.
- **The cost**: what the reader loses by getting this wrong.

The hook must be *true to the evidence*. A hook that overstates what a guest
said is the most common way this skill fails.

### Body — three to five sections
Each section gets a **bolded subhead written as a claim**, not a label
("Activation beats acquisition" — not "Activation"). Inside each section:

- Paragraphs of **1-3 sentences**. White space is a feature.
- The concrete before the abstract: what the guest actually did, then the
  principle.
- A bulleted list where there is a genuine list — steps, criteria, mistakes.
  Never bullets for prose.
- **Bold the load-bearing phrase** in a section, once or twice. Bolding
  everything bolds nothing.
- One direct quotation per section at most, short, with its tag: *"onboarding
  is the only part of your product 100% of people touch" [S2]*.

Sequence the sections as an argument: problem → why the obvious fix fails →
what the evidence shows works → how to run it.

### The takeaway — last ~150 words
End with something the reader can do **this week**, stated concretely enough
to schedule: the meeting to run, the number to pull, the question to ask five
customers. Close on a single line that lands the thesis. No "in conclusion".
No summary of what you just wrote.

### Sources
Finish with a `## Sources` section listing each cited episode once:
`[S1] Episode title — Guest`.

## Voice

- Second person. Talk to one product lead, not to an industry.
- Verbs over nouns: "ship the audit", not "the implementation of an audit
  process".
- Short sentences carry the argument; longer ones give it rhythm. Vary them.
- No emoji. No hype. No "game-changer", "unlock", "leverage", "in the world
  of".
- Confidence without overclaiming: the evidence is from specific people in
  specific companies, and the essay says so.

## Self-check before returning

- [ ] Word count is 1,100-1,400.
- [ ] Every factual claim about the podcast has a `[S#]` tag.
- [ ] Every `[S#]` tag exists in the EVIDENCE block.
- [ ] The body delivers exactly what the title promised — same number, same
      scope.
- [ ] Hook does not overstate the evidence.
- [ ] Subheads are claims, and reading only the subheads still tells the story.
- [ ] The takeaway is specific enough to act on today.
- [ ] "Am I making this easy to read?"
