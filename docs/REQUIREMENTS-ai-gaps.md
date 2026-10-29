# Requirements — missing functions for AI teams & users

Derived from public community signals (2026-08 … 2026-10).

Sources: GitHub discussions/issues (cross-tool memory, EGC, ai-memory, deepseek-harness,
Copilot coding agent reliability, agent handoff threads), trend digests naming
"memory/context management", "cross-vendor agent interoperability", "persistent context layers".

## Problem statements (what people say is missing)

| ID | Missing capability | Evidence |
| ---- | -------------------- | ---------- |
| P1 | **Agent handoff** — new session does not know "where you left off" | EGC "State loaded… Ready to pick up"; ai-memory handoffs; deepseek cross-session memory |
| P2 | **Cross-tool continuity** — decision in tool A is invisible in tool B | EGC "two tools, one brain"; deepseek "import sessions from pi/opencode/codex" |
| P3 | **Cost / token visibility** — no per-model or per-session accounting | dsh-billing "per-model cost accounting, session quota progress" |
| P4 | **Archive vs live context** — retrieval mixes old memory with current session | deepseek "tool archive and its memories versus this answer came from the current session" |
| P5 | **Project instruction files not in memory** — AGENTS.md/CLAUDE.md/skills ignored | abTEM "shared agent instructions (AGENTS.md/CLAUDE.md, skills)" |
| P6 | **Agent reliability & accountability** — hard to audit what an agent actually did | Copilot "reliable, autonomous…"; accountability report threads |
| P7 | **Tool coverage** — new coding tools ship monthly; archives lag | EGC install-target backlog (Kiro, Trae, Goose, Aider, OpenHands, Amazon Q, Warp) |
| P8 | **Team-shared memory** — personal store is not shareable/exportable safely | EGC/ai-memory "one install, every tool"; Throughline export is single-user |

## Requirements (throughline-enhancement backlog)

### R1 — Handoff brief (P1) — **implement now**

- `throughline handoff [--project X] [--limit N]` prints a structured resume brief:
  recent decisions, open questions, last sessions (tool + summary), top stale chunks.
- MCP tool `preload_summary` (already named in Python) must return the same brief.
- UI Overview shows the brief as "Continue where you left off".
- Acceptance: brief is ≤ 2k tokens, cites session ids, works offline (no LLM).

### R2 — Cross-tool continuity (P2) — partial, extend

- Search/Ask must include `source_tool` labels on every hit (done).
- New: `throughline decisions --project X` lists active decisions with originating tool
  and supersession chain.
- Acceptance: a decision written via Codex is listed when queried after a Claude session.

### R3 — Cost & token ledger (P3) — **implement now**

- Aggregate `token_count_in/out` and `cost_usd` per `source_tool`, per `model`, per day.
- `throughline costs [--days N]` CLI + `/api/costs` + Operate "Costs" tile.
- Acceptance: totals match `SELECT SUM(token_count_*)` within rounding.

### R4 — Source-aware retrieval (P4) — **implement now**

- Search/Ask flags: `--from memory|messages|all` (default all).
- API `?from=` filter. UI toggle on Find.
- Acceptance: `--from memory` returns only `memory_chunks`, never raw tool transcripts.

### R5 — Project instructions index (P5) — **implement now**

- Discover `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md`, `.cursorrules` under project roots.
- Store as `skills`/`prompts` rows (source_path, content) at ingest; searchable.
- Acceptance: `search "coding style"` can hit AGENTS.md content.

### R6 — Agent accountability (P6) — later

- `throughline audit [--since]` summarises what Throughline itself did (extract/embed/forget)
  and what agents wrote via MCP (`source_type = mcp_write`).

### R7 — Adapter growth (P7) — ongoing

- Registry must stay drop-in; add OpenCode/Aider/Amazon Q when paths are known.

### R8 — Team export pack (P8) — later

- `export-markdown --share` redacts PII and writes a zip-friendly folder + checksum.

## Status (2026-10-20)

| Req | Status |
| ----- | -------- |
| R1 handoff | done — `throughline handoff`, `/api/handoff`, Overview |
| R2 decisions | done — `throughline decisions` + supersession chains |
| R3 costs | done — `throughline costs`, `/api/costs` |
| R4 source filter | done — `search --from memory\|messages\|all` |
| R5 instructions | done — `scan-instructions` (51 files indexed) |
| R6 audit | done — `throughline audit`, `/api/audit` |
| R7 adapters | done — +opencode, +aider (11 total) |
| R8 team export | done — `export-markdown --redact` (4712 sessions) |
