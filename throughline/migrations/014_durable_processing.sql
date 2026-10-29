CREATE TABLE IF NOT EXISTS public.processing_runs (
    id text PRIMARY KEY,
    name text NOT NULL,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','finished','failed','stopped')),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    heartbeat_at timestamptz,
    returncode integer,
    error text,
    requested_by text NOT NULL DEFAULT 'operator',
    request_id text,
    stop_requested boolean NOT NULL DEFAULT false,
    recoveries integer NOT NULL DEFAULT 0,
    stages jsonb NOT NULL DEFAULT '{}',
    lines jsonb NOT NULL DEFAULT '[]',
    dropped_lines bigint NOT NULL DEFAULT 0,
    options jsonb NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS processing_one_active_name ON public.processing_runs(name)
    WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS processing_queue ON public.processing_runs(created_at,id)
    WHERE state IN ('queued','running');

CREATE OR REPLACE FUNCTION public.audit_processing_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' OR OLD.state IS DISTINCT FROM NEW.state OR OLD.stop_requested IS DISTINCT FROM NEW.stop_requested OR OLD.recoveries IS DISTINCT FROM NEW.recoveries THEN
        INSERT INTO public.access_audit(actor,request_id,action,entity_type,entity_id,changed_fields)
        VALUES(COALESCE(NULLIF(current_setting('throughline.actor',true),''),NEW.requested_by),COALESCE(NULLIF(current_setting('throughline.request_id',true),''),NEW.request_id),'PROCESSING_' || upper(NEW.state),'processing_runs',NEW.id,
            CASE WHEN TG_OP='INSERT' THEN ARRAY['state'] ELSE ARRAY(SELECT key FROM jsonb_each(to_jsonb(NEW)) WHERE key IN ('state','stop_requested','recoveries') AND value IS DISTINCT FROM to_jsonb(OLD)->key) END);
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS processing_lifecycle_audit ON public.processing_runs;
CREATE TRIGGER processing_lifecycle_audit AFTER INSERT OR UPDATE ON public.processing_runs
    FOR EACH ROW EXECUTE FUNCTION public.audit_processing_lifecycle();
