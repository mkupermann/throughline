-- 004_generated_by.sql
--
-- Label conversations that a machine produced, so views can show a person
-- their own history instead of the tooling's exhaust.
--
-- Measured on a real corpus on 2026-08-11: of 3,606 conversations, ~3,017 were
-- Throughline's own `claude -p` calls (title generation, memory extraction,
-- entity extraction, reflection, and two eval harnesses) and a further ~247
-- were the user's own scheduled tools. Roughly 340 were sessions a person
-- actually had. Every list in the product — Timeline, Find, the counts on
-- Overview — was therefore showing overwhelmingly machine traffic, in the
-- shape the user described: thousands of two-message "conversations" with no
-- thread between them.
--
-- The ingest-time filter added earlier stops NEW self-talk from being stored.
-- It did nothing about what was already there, and nothing at all about the
-- user's own automation, which is not Throughline's to drop.
--
-- NULL means a person typed it. Anything else names the generator. Nothing is
-- deleted: this database is the only surviving copy of most of what it holds,
-- and a label lets a view fold the machinery away while leaving it openable.

BEGIN;

ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS generated_by text;

COMMENT ON COLUMN public.conversations.generated_by IS
    'Name of the script or scheduled tool that produced this conversation; '
    'NULL when a person did. Set by throughline.self_referential.generated_by '
    'at ingest time and by scripts/backfill_generated_by.py for rows stored '
    'before the column existed.';

-- Partial index: every default view filters on `generated_by IS NULL`, and
-- that is the selective side — roughly one row in ten on the corpus this was
-- written against.
CREATE INDEX IF NOT EXISTS idx_conversations_human
    ON public.conversations (started_at DESC)
    WHERE generated_by IS NULL;

COMMIT;
