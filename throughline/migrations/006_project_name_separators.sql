-- project_name has to work for a corpus written on more than one platform.
--
-- `conversations.project_path` is the `cwd` recorded inside the session file,
-- so it carries whatever separator the machine that ran the session used. The
-- generated column split on '/' alone; given a Windows path such as
-- C:\Users\michael\Documents\GitHub\foo it found no separator at all and
-- returned the whole string. Every Windows session therefore became a project
-- named by its own absolute path, and the same repository worked on from two
-- machines never grouped together — which is the one thing a shared corpus
-- exists to do.
--
-- Normalising the separator before splitting leaves POSIX paths untouched:
-- '/Users/x/foo' and 'C:\Users\x\foo' both yield 'foo'.
--
-- A generated column's expression cannot be altered in place, so the column is
-- dropped and re-added. It is STORED, so this rewrites the table; on the
-- corpus this was written against that is a few thousand rows. Views that
-- select it must be dropped first and restored afterwards.

DROP VIEW IF EXISTS public.v_conversation_stats;

ALTER TABLE public.conversations DROP COLUMN IF EXISTS project_name;

ALTER TABLE public.conversations
    ADD COLUMN project_name text GENERATED ALWAYS AS (
        CASE
            WHEN project_path IS NULL THEN 'unknown'::text
            ELSE split_part(replace(project_path, '\', '/'), '/'::text, '-1'::integer)
        END
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_conversations_project_name
    ON public.conversations (project_name);
