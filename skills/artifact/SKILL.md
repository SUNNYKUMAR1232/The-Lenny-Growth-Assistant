---
name: artifact
version: 1.0.0
description: >
  Produce a self-contained Markdown document or a complete HTML/CSS artifact
  from the current conversation and its transcript evidence, written so it
  survives sanitization intact.
routes: [ARTIFACT]
---

# Artifact Skill

Artifacts are things the user takes away: a one-pager, a checklist, a
comparison table, a scorecard, a simple landing page. They render beside the
chat in the Artifact Viewer.

## Choosing the format

- **Markdown** — documents, plans, checklists, briefs, essays. The default
  when the user did not ask for a web page.
- **HTML** — anything whose *layout* is the point: a styled one-pager, a
  scorecard, a table with visual weighting, a landing page.

## Rules for every artifact

1. **Self-contained.** No external CSS, JS, fonts, images, or CDNs. They are
   stripped before rendering and the artifact will look broken.
2. **Grounded.** Content about Lenny's Podcast comes from the EVIDENCE block
   and cites `[S#]` tags. An artifact is not a licence to invent.
3. **Complete.** No `<!-- TODO -->`, no lorem ipsum, no "add your content
   here". Ship the finished thing.
4. **Titled.** Start with an `<h1>`/`#` that names the artifact.

## HTML rules (these mirror the sanitizer — break them and your work is removed)

- **Allowed**: semantic structure (`section`, `article`, `header`, `h1`-`h6`,
  `p`, `ul`, `ol`, `table`, `blockquote`, `figure`), inline `style`
  attributes, and a single `<style>` block.
- **Removed on the server**: `<script>`, `<iframe>`, `<form>`, `<input>`,
  `<object>`, `<embed>`, `<svg>`, `<link>`, `<meta>`, every `on*` handler,
  `javascript:` URLs, `@import`, and any `url()` that is not a `data:` URI.
- **Images**: only `data:` URIs. Prefer CSS shapes, borders, and typography
  over images.
- **No interactivity.** Scripts do not run in the viewer's sandbox. If the
  user asks for something interactive, build the static view of it and say in
  the chat message that scripting is disabled in the sandbox.
- Write CSS that stands on its own: system font stack, `max-width` around
  760px for reading, generous line-height, real spacing scale.
- Assume both light and dark surroundings; set an explicit background and
  text colour.

## Output contract

Return the artifact and nothing else — no preamble, no explanation, no code
fence around the whole document. The chat message that accompanies the
artifact is generated separately.
