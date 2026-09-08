-- Generated display names retain their origin and sampled source conversations.
ALTER TABLE public.project_names ADD COLUMN IF NOT EXISTS name_origin text NOT NULL DEFAULT 'user'
    CHECK (name_origin IN ('user', 'model'));
ALTER TABLE public.project_names ADD COLUMN IF NOT EXISTS source_conversation_ids bigint[] NOT NULL DEFAULT '{}';
