-- User-authored, source-linked project state. History is append-only.
CREATE TABLE IF NOT EXISTS public.project_checkpoints (
    id bigserial PRIMARY KEY,
    project_name text NOT NULL,
    project_path text,
    kind text NOT NULL CHECK (kind IN ('goal', 'status', 'blocker', 'next')),
    content text NOT NULL CHECK (length(btrim(content)) BETWEEN 1 AND 4000),
    source_conversation_id bigint REFERENCES public.conversations(id) ON DELETE SET NULL,
    source_message_id bigint REFERENCES public.messages(id) ON DELETE SET NULL,
    source_session_id uuid NOT NULL,
    source_message_uuid uuid,
    source_excerpt text NOT NULL,
    recorded_by text NOT NULL DEFAULT 'local user',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_project_checkpoints_scope
    ON public.project_checkpoints (project_name, project_path, created_at DESC, id DESC);
