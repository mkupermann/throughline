# Hero animation concepts

Three concept variants for the README hero, in response to the external
review's *"there is no animated hero — a 10-second SVG/GIF would close
the biggest visual gap"* item. Each file in this directory is a fully
self-contained, animated SVG you can preview by opening it in any
browser — no JS, no GIF, no external assets, and they degrade
gracefully when GitHub renders the SVG statically (the first keyframe
shows).

The current static hero (`docs/assets/hero.svg`) and the static
architecture diagram (`docs/assets/architecture.svg`) already cover
the *visible at rest* case. These concepts are about the *moving*
hero — the loop you would drop into the README's first
`<img src="…">` instead of the static file.

Pick one (or none — the static hero is good enough on its own).

---

## Concept A — *"The Thread"*

[`hero-concept-A-thread.svg`](hero-concept-A-thread.svg)

- **What you see**: A single glowing thread weaves through six tool
  anchors along the bottom — Claude Code, Codex, Hermes, Continue,
  Windsurf, Cline. The thread's dash pattern animates forward, giving
  the impression of motion *through* the tools.
- **Pitch**: "One thread, six tools." Lowest-friction evolution of the
  current static hero — same composition, same palette, but now the
  thread moves.
- **Loop**: 6 seconds. One animated property (`stroke-dashoffset`).
- **Risk**: Conservative. Reads almost identically to the current hero
  when the animation is paused (e.g. GitHub's image cache, embedded in
  a slide deck).
- **Best fit**: README header. Drop-in replacement for the existing
  `hero.svg`.

## Concept B — *"Ingest → Read"*

[`hero-concept-B-ingest.svg`](hero-concept-B-ingest.svg)

- **What you see**: Three JSONL files materialise on the left
  (`~/.claude/projects/*.jsonl`, `~/.codex/sessions/*.jsonl`,
  `~/.hermes/state.db`) — they slide into the Throughline core,
  which pulses — then a "Memory · this project" context block
  appears on the right with three remembered facts, before the loop
  resets. The three stage labels — `WRITE · INGEST · READ` — are
  always visible.
- **Pitch**: This is the loop the external review proposed verbatim:
  *"3 CLIs write into separate JSONL files, Throughline ingests them,
  the next session pulls the unified context."* It tells the whole
  value-prop in 8 seconds without a single word of marketing copy.
- **Loop**: 8 seconds. Four phases (write → ingest → read → fade).
- **Risk**: Highest. The most moving parts of the three; the most
  *interesting* of the three; also the most "explainer-video" in feel.
  When paused (static render) it shows the title and stage labels,
  which is fine — but it looks weakest of the three when not playing.
- **Best fit**: kupermann.com/memory landing page (where it always
  plays), or near "What it does" in the README rather than the
  top-of-page header.

## Concept C — *"Sessions fade, memory persists"*

[`hero-concept-C-timeline.svg`](hero-concept-C-timeline.svg)

- **What you see**: A time axis runs MON → SUN across the bottom.
  Above the line: session bubbles light up one tool per weekday
  (Claude Code → Codex → Hermes → Continue → Windsurf → Cline),
  each fades after firing — a visual argument that sessions are
  ephemeral. Below the line: the `memory_chunks` bar grows
  monotonically, day by day. Tagline: *"The agents forget.
  Throughline remembers."*
- **Pitch**: The thesis stated in motion. Different *kind* of argument
  from A and B — A is structural (one thread), B is operational (data
  flowing), C is **philosophical**: time passes, sessions disappear,
  the memory store accrues. This is the one a sceptical reader who
  wants the "so what" benefits in three seconds.
- **Loop**: 7 seconds. Two animated layers — ephemeral session bubbles
  and a persistent growing bar.
- **Risk**: Medium. Cleaner than B at rest, more conceptual than A.
- **Best fit**: Landing page or a "Why this exists" section, where
  the thesis matters more than the architecture.

---

## How to use one as the README hero

If you pick A, B, or C, the README change is one line. Edit
`README.md`:

```diff
- <img src="docs/assets/hero.svg" alt="..." width="860">
+ <img src="docs/landing/hero-concept-B-ingest.svg" alt="..." width="860">
```

GitHub renders SVGs in `<img>` tags but **does not play SVG `<animate>`
elements** inside the README itself — they render as the first
keyframe. To play the animation you need to either:

1. **Use the SVG on a self-hosted landing page** (the existing
   `docs/landing/index.html` is the natural home), where the browser
   renders SVG animations directly.
2. **Convert the SVG to a GIF or MP4** for the README — `ffmpeg`
   `+` `rsvg-convert` works, and the resulting file goes in
   `docs/assets/` alongside the static hero. The SVG stays in
   `docs/landing/` as the source of truth.

The static `docs/assets/hero.svg` already works fine on GitHub — these
SVGs are upgrades, not replacements that GitHub will play.

---

## Why three concepts, not one

Hero design is one of the few things a code agent shouldn't decide
solo — the same value prop reads very differently depending on whether
you lead with *structure* (A), *flow* (B), or *thesis* (C). The
quickest way to pick is to open each SVG in a browser tab and watch
the loop once. Whichever one makes you smile is the right one.
