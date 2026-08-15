-- 001_message_dedup.sql
--
-- Adds a partial unique index on (conversation_id, uuid) so the ingester can
-- safely re-process a still-growing Claude Code session file and insert only
-- its NEW messages via ON CONFLICT DO NOTHING.
--
-- Before this index, an appended (still-live) session re-hashed on the next
-- ingest run, the conversation INSERT hit ON CONFLICT (session_id) DO NOTHING,
-- and every new message in that file was silently dropped — permanently.
--
-- The index is partial (WHERE uuid IS NOT NULL) because a small number of
-- system/synthetic entries carry no uuid; those are not deduplicated.
--
-- If duplicate (conversation_id, uuid) rows already exist from earlier buggy
-- runs, collapse them first, keeping the lowest id.

DELETE FROM messages m
USING messages dup
WHERE m.conversation_id = dup.conversation_id
  AND m.uuid = dup.uuid
  AND m.uuid IS NOT NULL
  AND m.id > dup.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_conversation_uuid
    ON public.messages USING btree (conversation_id, uuid)
    WHERE (uuid IS NOT NULL);
