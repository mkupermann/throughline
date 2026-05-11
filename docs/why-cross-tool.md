# Why cross-tool memory matters

This document argues the thesis Throughline is built on: **a memory layer
for AI coding assistants must be cross-tool, or it doesn't really solve
the problem.** If you only ever use one assistant — and you're sure you
always will — you may not need this. Most working developers don't
actually have that property, even when they think they do.

## The shape of the problem

A "memory" for an AI assistant is the set of things the assistant can be
relied on to know about you, your project, and your past decisions at the
moment of its very first reply in a new session. There are three layers:

1. **Model weights.** Trained-in world knowledge. Same for everyone on the
   same model. Stale within months; useless for anything project-specific.
2. **Per-tool memory.** What Anthropic, OpenAI, Cursor, Continue, etc.
   each maintain inside their own product. Personal to you, tied to the
   product's account, lives wherever that vendor stores it.
3. **Substrate memory.** Files on your laptop. Project README, CLAUDE.md,
   AGENTS.md, the actual codebase. Read fresh every session by whichever
   tool you're using.

Throughline sits between (2) and (3): a substrate-level memory store that
every assistant on your machine can read and write. Why does that matter?

## Three concrete failure modes per-tool memory does not solve

### 1. Tool switching loses context

You're in Claude Code on Monday. The repo is FastAPI + SQLAlchemy. Claude
helps you migrate three endpoints to async, learns your test patterns,
picks up that `make lint` runs both ruff and mypy. By Friday you're in
Cursor (a teammate's pairing tool, or an experiment) on the same repo.
Cursor knows none of this. You re-explain. The next week you're in Codex
CLI because Claude Code is rate-limited or you want a different model.
Codex knows none of this either.

Per-tool memory says "use Claude Code consistently." That is not a
realistic constraint over a year of work. Tools have outages. Pricing
changes. New tools ship. Codebases are shared between developers who
each have their own preferred CLI.

Throughline ingests Claude Code's JSONL, Codex's rollouts, Hermes's
session JSONs, Continue's session files, Cline's, Windsurf's plans —
into one schema. When you start a new session in any of them, the
SessionStart hook (or the MCP server, or the manual context dump) pulls
the relevant decisions from across all of them. The thread survives.

### 2. The most valuable memory is decision provenance, not facts

Per-tool memory tends to record facts: *"the user prefers pytest with
xdist."* That's useful, but the high-value memory is **decision
provenance**:

- "On 2026-02-14 we picked pgvector over Milvus *because* the deployment
  footprint matters more than recall@10 in our case."
- "We dropped the SHAP integration in v0.2 *because* the wheel-build
  burden on Apple Silicon was costing more support hours than the
  feature was worth."

That sentence has three parts: the *what*, the *when*, and the
*because*. Throughline's memory extractor pulls all three from the
transcripts of *every* tool you used. Per-tool memory typically only
captures the *what*, and only from the tool that recorded it.

When you ask Claude two months later "why did we pick pgvector?", a
per-tool memory either repeats the *what* (you already know it), or
hallucinates a confident-sounding *because* (you can't trust it). A
cross-tool memory hands back the actual quote with a date and a tool
attribution. Different epistemic category.

### 3. Vendor memory is gated by the vendor

Anthropic's memory feature works inside Claude. OpenAI's works inside
ChatGPT. Neither is going to ingest the other's transcripts. That's
fine — they're not in the business of helping their competitor's tool
get smarter. But it leaves the substrate-memory job unfilled.

The right place for that job is on your laptop, controlled by you,
ingesting whatever local AI sessions exist, exposing them to whatever
tool you happen to want to use next.

## Why not just use one tool

You can. Many people do, successfully. The argument for cross-tool isn't
"single-tool is wrong"; it's "single-tool is fragile in ways most people
underestimate." Tools have a lifecycle:

- Claude Code is two years old; the field is moving fast enough that the
  best tool in 2027 may not exist yet.
- Codex CLI shipped late 2024; the early adopters had to switch in.
- Cursor users migrated from VSCode + Copilot; some are now experimenting
  with Continue.dev.

Every one of those migrations is a memory bankruptcy in a per-tool model.
You restart, re-explain, re-discover the same pitfalls. The infrastructure
cost of *being able to switch* is what Throughline absorbs.

## Why not just use one of the cross-tool options that exist

There are three families of competitor:

1. **General-purpose memory frameworks** (Mem0, Letta née MemGPT, Zep).
   These are application-level SDKs: you build the agent and it calls
   the memory API. They don't auto-ingest from local AI tools — that's
   not their job, you're meant to be writing your own agent. If you are
   writing your own agent, use them. If you are using off-the-shelf
   tools, they don't help.

2. **Vendor-side memory** (Anthropic, OpenAI). Scoped to the vendor.
   See above. These will only ever solve the within-vendor case.

3. **IDE memory features** (Cursor Memory, Continue.dev rules, etc.).
   Scoped to the IDE. Better than nothing inside that IDE; not portable.

Throughline's slot: **local-first cross-tool ingest** for off-the-shelf
AI CLIs and IDE plugins, exposing the unified memory back via three
channels (Claude Code skill, MCP server, raw context file). It's a
substrate, not an agent framework.

## When this is a bad fit

- You only ever use one tool, you're sure you always will, and the
  vendor's built-in memory is good enough. Then run with that and
  revisit if it stops being enough.
- You don't want to run Postgres on your laptop and don't want to use
  the Docker Compose shortcut. Throughline needs persistent storage;
  there's no cloud-hosted SaaS version and there isn't one planned.
- You write your own agent end-to-end. Use Mem0 / Letta / Zep —
  they're designed for that case and Throughline isn't.

## When this is the right tool

- You routinely use two or more AI CLIs or IDE plugins on the same
  codebase.
- You care about *why* decisions were made, not just *what* they were.
- You want the memory to live on your laptop, not in a vendor account.
- You'd rather inspect SQL than reverse-engineer a cloud vendor's
  retention policy.

If three of those four describe you, Throughline is the substrate the
field hasn't quite shipped yet. The vendors will get there for their
own ecosystems eventually; cross-vendor will likely remain unsolved
because the incentives don't align. This project closes that gap
locally until they do.

## See also

- [README — Comparison to alternatives](../README.md#comparison-to-alternatives)
  for the feature-by-feature table.
- [docs/architecture.md](architecture.md) for how the adapter framework
  actually pulls from each tool.
- [docs/USAGE.md](USAGE.md) for the day-to-day commands.
