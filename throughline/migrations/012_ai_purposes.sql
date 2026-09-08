-- Explicit purpose routing reuses the existing provider credential store.
CREATE TABLE IF NOT EXISTS public.ai_purposes (
    purpose text PRIMARY KEY CHECK (purpose IN ('answer','titles','project_names','extraction','reflection','embeddings')),
    provider_id bigint REFERENCES public.pm_ai_providers(id) ON DELETE RESTRICT,
    cli text CHECK (cli IN ('codex','vibe','claude')),
    model text NOT NULL DEFAULT '',
    embedding_dim integer CHECK (embedding_dim IN (768,1536)),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((provider_id IS NOT NULL)::int + (cli IS NOT NULL)::int = 1),
    CHECK (purpose <> 'embeddings' OR (cli IS NULL AND embedding_dim IS NOT NULL))
);
