-- Preserve imported paths and existing identities; only an explicit assignment overrides grouping.
ALTER TABLE public.project_names ADD COLUMN IF NOT EXISTS is_curated boolean NOT NULL DEFAULT false;
ALTER TABLE public.conversations ADD COLUMN IF NOT EXISTS assigned_project text
    REFERENCES public.project_names(project_key) ON DELETE RESTRICT;
ALTER TABLE public.conversations ADD COLUMN IF NOT EXISTS source_project_name text GENERATED ALWAYS AS (
    CASE WHEN project_path IS NULL THEN 'unknown'::text
    ELSE split_part(replace(project_path, '\', '/'), '/', -1) END
) STORED;
-- PostgreSQL 16 preserves the column, data, views and indexes with DROP EXPRESSION.
ALTER TABLE public.conversations ALTER COLUMN project_name DROP EXPRESSION IF EXISTS;
CREATE TABLE IF NOT EXISTS public.conversation_project_changes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id bigint NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    previous_project text,
    assigned_project text,
    source_path text,
    actor text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION public.apply_conversation_project() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.project_name := COALESCE(NEW.assigned_project,
        CASE WHEN NEW.project_path IS NULL THEN 'unknown'::text
        ELSE split_part(replace(NEW.project_path, '\', '/'), '/', -1) END);
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS conversation_project_identity ON public.conversations;
CREATE TRIGGER conversation_project_identity BEFORE INSERT OR UPDATE ON public.conversations
    FOR EACH ROW EXECUTE FUNCTION public.apply_conversation_project();
CREATE OR REPLACE FUNCTION public.record_conversation_project() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.assigned_project IS DISTINCT FROM NEW.assigned_project THEN
        INSERT INTO public.conversation_project_changes(conversation_id,previous_project,assigned_project,source_path,actor)
            VALUES(NEW.id,OLD.assigned_project,NEW.assigned_project,NEW.project_path,
                COALESCE(NULLIF(current_setting('throughline.actor',true),''),'operator'));
        INSERT INTO public.access_audit(actor,request_id,action,entity_type,entity_id,changed_fields)
            VALUES(COALESCE(NULLIF(current_setting('throughline.actor',true),''),'operator'),
                NULLIF(current_setting('throughline.request_id',true),''),'ASSIGN','conversation',NEW.id::text,ARRAY['assigned_project']);
    END IF;
    IF OLD.project_name IS DISTINCT FROM NEW.project_name THEN
        UPDATE public.memory_chunks SET project_name=NEW.project_name
            WHERE source_type='conversation' AND source_id=NEW.id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS conversation_project_history ON public.conversations;
CREATE TRIGGER conversation_project_history AFTER UPDATE ON public.conversations
    FOR EACH ROW EXECUTE FUNCTION public.record_conversation_project();
-- Also restores assignments when adopting an untracked schema that reapplied migration 006.
UPDATE public.conversations SET assigned_project=assigned_project WHERE assigned_project IS NOT NULL;
