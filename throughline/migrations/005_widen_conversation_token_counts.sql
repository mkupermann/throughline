-- Widen conversations.token_count_in and conversations.token_count_out
-- from integer to bigint so the per-session token totals (which include
-- cache-creation and cache-read tokens, easily into the billions on a
-- long-lived session) fit without an out-of-range exception.
--
-- This file was originally released as
-- 001_widen_conversation_token_counts.sql, which duplicated the ordinal of
-- 001_message_dedup.sql. The migration runner preserves that historic name as
-- an applied alias; never rename rows in applied_migrations.
--
-- ALTER COLUMN ... TYPE refuses to run while a view depends on the column, so
-- v_conversation_stats is dropped and re-created in the same transaction.

DROP VIEW IF EXISTS public.v_conversation_stats;

ALTER TABLE public.conversations
    ALTER COLUMN token_count_in TYPE bigint USING token_count_in::bigint;

ALTER TABLE public.conversations
    ALTER COLUMN token_count_out TYPE bigint USING token_count_out::bigint;

CREATE OR REPLACE VIEW public.v_conversation_stats AS
 SELECT project_name,
        count(*)                                       AS sessions,
        sum(message_count)                             AS total_messages,
        round(avg(token_count_in + token_count_out))   AS avg_tokens,
        sum(cost_usd)                                  AS total_cost
 FROM   conversations
 GROUP  BY project_name
 ORDER  BY count(*) DESC;
