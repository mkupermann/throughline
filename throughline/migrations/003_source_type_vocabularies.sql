-- 003_source_type_vocabularies.sql
--
-- Constrain the `source_type` columns to the values the code actually writes.
--
-- These are closed vocabularies stored as free text, and the gap is not
-- theoretical. `throughline status` filtered its extraction-freshness
-- indicator on `source_type IN ('extraction', 'mcp_write')`. Neither value has
-- ever been written by anything — the extractor writes 'conversation' and the
-- manual path writes 'manual'. Nothing rejected the mistake: the filter simply
-- matched no rows, `max()` over the empty set returned NULL rather than an
-- error, and the indicator read "—" on a database holding 986 chunks. A
-- stalled extractor and a mistyped filter were indistinguishable for as long
-- as the typo existed.
--
-- A CHECK constraint does not catch a bad value in a WHERE clause, but it
-- forces the vocabulary to be written down in one authoritative place and
-- makes the *write* side fail loudly, which is what stops a fifth spelling
-- from quietly joining the four in use.
--
-- Values below are the complete set of literals written anywhere in the tree:
--   memory_chunks — 'conversation' (scripts/extract_memory.py),
--                   'manual' (throughline/queries/memory.py, skill/scripts/add.py),
--                   'mcp_write' (memory_mcp/server.py),
--                   'reflection_merge' and 'consolidation' (scripts/reflect_memory.py)
--   embeddings    — 'memory_chunk' and 'message' (scripts/generate_embeddings.py)
--
-- Adding a new source type means editing this list in a new migration and in
-- sql/schema.sql. That friction is the point.
--
-- Not covered here: `memory_chunks.source_id` is polymorphic — it means a
-- conversation id only when source_type = 'conversation' — so it cannot carry
-- a foreign key, and chunks whose conversation was deleted keep pointing at a
-- row that is gone. `throughline doctor --category archive` reports those; a
-- real fix needs the column split by referent, which is a larger change than
-- this migration.

BEGIN;

ALTER TABLE public.memory_chunks
    DROP CONSTRAINT IF EXISTS memory_chunks_source_type_check;
ALTER TABLE public.memory_chunks
    ADD CONSTRAINT memory_chunks_source_type_check
    CHECK (source_type IN ('conversation', 'manual', 'mcp_write',
                           'reflection_merge', 'consolidation'));

ALTER TABLE public.embeddings
    DROP CONSTRAINT IF EXISTS embeddings_source_type_check;
ALTER TABLE public.embeddings
    ADD CONSTRAINT embeddings_source_type_check
    CHECK (source_type IN ('memory_chunk', 'message'));

COMMIT;
