-- 002_source_tool.sql
--
-- Adds conversations.source_tool: WHICH tool produced this conversation.
--
-- This is deliberately a new column rather than a cleanup of `entrypoint`,
-- because `entrypoint` already has a correct and different meaning — HOW the
-- tool was invoked (`cli` vs `sdk-cli`). Conflating the two is the root cause
-- documented in the design spec §1.1: claude_code passed Claude's own
-- entrypoint through, so 98% of the corpus was unrecognisable as Claude Code,
-- and conflicts.py has been reporting false cross-tool conflicts between
-- Claude Code and itself.
--
-- Nullable on purpose. NULL means "genuinely unknown" and the UI renders it as
-- "(unattributed)" rather than hiding it. The rows that end up NULL here
-- predate any Vibe files on disk; labelling them `vibe` would be a fabrication
-- that hardens into fact.
--
-- Idempotent: every UPDATE is guarded by `source_tool IS NULL`, so re-running
-- changes zero rows.

ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS source_tool text;

CREATE INDEX IF NOT EXISTS idx_conversations_source_tool
    ON public.conversations USING btree (source_tool);

-- Rule 1 — an explicit metadata.source that names a known adapter.
UPDATE public.conversations
SET source_tool = metadata->>'source'
WHERE source_tool IS NULL
  AND metadata->>'source' IN (
      'claude_code','windsurf','hermes','codex','continue','cline','vibe','cursor','zed'
  );

-- Rule 2 — Claude Code's own entrypoint values.
UPDATE public.conversations
SET source_tool = 'claude_code'
WHERE source_tool IS NULL
  AND entrypoint IN ('cli','sdk-cli');

-- Rule 3 — entrypoint already naming an adapter.
UPDATE public.conversations
SET source_tool = entrypoint
WHERE source_tool IS NULL
  AND entrypoint IN (
      'claude_code','windsurf','hermes','codex','continue','cline','vibe','cursor','zed'
  );

-- Rule 3b — the one adapter whose entrypoint does not match its name.
UPDATE public.conversations
SET source_tool = 'continue'
WHERE source_tool IS NULL
  AND entrypoint = 'continue.dev';

-- Everything else stays NULL, deliberately.
