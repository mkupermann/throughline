-- Existing provider credentials are deliberately preserved.
CREATE TABLE IF NOT EXISTS public.access_users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text UNIQUE NOT NULL CHECK (username ~ '^[a-z0-9][a-z0-9._@-]{0,119}$'),
    display_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.access_sessions (
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES public.access_users(id) ON DELETE CASCADE,
    csrf_token text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS access_sessions_user ON public.access_sessions (user_id);
CREATE TABLE IF NOT EXISTS public.access_attempts (
    key text PRIMARY KEY,
    attempts integer NOT NULL,
    window_start timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.access_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor text NOT NULL,
    request_id text,
    action text NOT NULL,
    entity_type text NOT NULL,
    entity_id text,
    changed_fields text[] NOT NULL DEFAULT '{}'
);
CREATE OR REPLACE FUNCTION public.record_access_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE before_row jsonb; after_row jsonb; fields text[];
BEGIN
    before_row := CASE WHEN TG_OP = 'INSERT' THEN '{}'::jsonb ELSE to_jsonb(OLD) END;
    after_row := CASE WHEN TG_OP = 'DELETE' THEN '{}'::jsonb ELSE to_jsonb(NEW) END;
    SELECT array_agg(k ORDER BY k) INTO fields FROM (
        SELECT key AS k FROM jsonb_object_keys(before_row || after_row) AS key
        WHERE before_row -> key IS DISTINCT FROM after_row -> key
    ) changed;
    INSERT INTO public.access_audit(actor, request_id, action, entity_type, entity_id, changed_fields)
    VALUES (COALESCE(NULLIF(current_setting('throughline.actor', true), ''), 'operator'),
        NULLIF(current_setting('throughline.request_id', true), ''), TG_OP, TG_TABLE_NAME,
        COALESCE(after_row->>'id', before_row->>'id', after_row->>'purpose', before_row->>'purpose',
                 after_row->>'project_key', before_row->>'project_key'), COALESCE(fields, '{}'));
    RETURN COALESCE(NEW, OLD);
END $$;
DO $$ DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['access_users','project_checkpoints','project_names','ai_purposes',
        'pm_ai_providers','pm_roles','pm_members','pm_teams','pm_projects'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS access_changes ON public.%I', tab);
        EXECUTE format('CREATE TRIGGER access_changes AFTER INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.record_access_change()', tab);
    END LOOP;
END $$;
CREATE OR REPLACE FUNCTION public.protect_access_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Audit records are append-only'; END $$;
DROP TRIGGER IF EXISTS access_audit_immutable ON public.access_audit;
CREATE TRIGGER access_audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.access_audit
    FOR EACH STATEMENT EXECUTE FUNCTION public.protect_access_audit();
