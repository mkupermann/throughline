-- User-facing labels are independent of imported folder identifiers.
CREATE TABLE IF NOT EXISTS public.project_names (
    project_key text PRIMARY KEY,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
    updated_at timestamptz NOT NULL DEFAULT now()
);
