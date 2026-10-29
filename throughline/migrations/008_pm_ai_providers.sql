--
-- Welle D: Cline/Cursor-style AI provider & model management for the PM
-- area. Users add STANDARD providers (name, type, optional base URL, API
-- key) whose models are fetched live from the provider, plus CUSTOM model
-- ids of their own — both selectable in the Role editor and usable at
-- launch (throughline/queries/pm.py's ai_catalog(), throughline/jobs/
-- pm_launch.py's env injection).
--
-- Additive only. `pm_` prefix, same convention as 007_pm_ops_schema.sql.

CREATE TABLE IF NOT EXISTS public.pm_ai_providers (
    id BIGSERIAL PRIMARY KEY,
    name text NOT NULL,
    provider_type text NOT NULL CHECK (provider_type IN (
        'openai', 'anthropic', 'mistral', 'google', 'openrouter',
        'ollama', 'openai_compatible'
    )),
    -- Required for ollama/openai_compatible (enforced in the router, not
    -- here — a CHECK spanning two columns with a conditional per value is
    -- more trouble than it is worth for a local single-user app); optional
    -- override for the others, which have a sensible default base URL
    -- (see throughline/queries/pm.py's _PROVIDER_DEFAULT_BASE).
    base_url text,
    -- Stored plainly: this is a local, single-user Postgres instance — the
    -- same trust model as .env, not a multi-tenant secret store. Never
    -- returned by the API (throughline/api/routers/pm.py strips it and
    -- reports api_key_set instead).
    api_key text,
    -- User-added model ids not returned by the provider's live model list
    -- (or added while the provider is unreachable).
    custom_models jsonb NOT NULL DEFAULT '[]',
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
