--
-- Virtual Team Ops: Projects -> Teams -> Roles -> Members -> Assignments,
-- launched/watched/stopped by Throughline against ~/ai-pipeline/pipeline.sh.
-- See docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md.
--
-- Additive only. No existing table is touched. `pm_` prefix keeps the new
-- domain visually separate from the memory-layer tables it sits next to.

-- ── Catalogs (defined once, reused everywhere) ─────────────────────────────

CREATE TABLE public.pm_roles (
    id BIGSERIAL PRIMARY KEY,
    name text NOT NULL,
    description text,
    default_ai_tool text,
    default_ai_model text,
    -- FKs into the existing `skills` table (jobs/scan_skills.py), not a
    -- second skills store. No FK constraint on array elements is possible
    -- in Postgres; validity is enforced in throughline/queries/pm.py.
    skill_refs bigint[] NOT NULL DEFAULT '{}',
    instructions text,
    document_refs jsonb NOT NULL DEFAULT '[]',
    token_budget bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.pm_members (
    id BIGSERIAL PRIMARY KEY,
    name text NOT NULL,
    member_type text NOT NULL CHECK (member_type IN ('human', 'agent')),
    contact_info jsonb NOT NULL DEFAULT '{}',
    skill_refs bigint[] NOT NULL DEFAULT '{}',
    instructions text,
    document_refs jsonb NOT NULL DEFAULT '[]',
    token_budget bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.pm_teams (
    id BIGSERIAL PRIMARY KEY,
    name text NOT NULL,
    description text,
    token_budget bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.pm_projects (
    id BIGSERIAL PRIMARY KEY,
    name text NOT NULL,
    description text,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'completed', 'archived')),
    token_budget bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ── Relationships (many-to-many, per spec) ─────────────────────────────────

CREATE TABLE public.pm_project_repos (
    pm_project_id bigint NOT NULL REFERENCES public.pm_projects(id) ON DELETE CASCADE,
    project_id    bigint NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    PRIMARY KEY (pm_project_id, project_id)
);

CREATE TABLE public.pm_project_teams (
    pm_project_id bigint NOT NULL REFERENCES public.pm_projects(id) ON DELETE CASCADE,
    team_id       bigint NOT NULL REFERENCES public.pm_teams(id) ON DELETE CASCADE,
    PRIMARY KEY (pm_project_id, team_id)
);

CREATE TABLE public.pm_team_roles (
    team_id bigint NOT NULL REFERENCES public.pm_teams(id) ON DELETE CASCADE,
    role_id bigint NOT NULL REFERENCES public.pm_roles(id) ON DELETE CASCADE,
    PRIMARY KEY (team_id, role_id)
);

CREATE TABLE public.pm_assignments (
    id BIGSERIAL PRIMARY KEY,
    pm_project_id bigint NOT NULL REFERENCES public.pm_projects(id) ON DELETE CASCADE,
    team_id       bigint NOT NULL REFERENCES public.pm_teams(id) ON DELETE CASCADE,
    role_id       bigint NOT NULL REFERENCES public.pm_roles(id) ON DELETE CASCADE,
    member_id     bigint NOT NULL REFERENCES public.pm_members(id) ON DELETE CASCADE,
    ai_tool  text,   -- override; NULL inherits pm_roles.default_ai_tool
    ai_model text,   -- override; NULL inherits pm_roles.default_ai_model
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_pm_assignments_project_team
    ON public.pm_assignments (pm_project_id, team_id);

-- ── Execution ───────────────────────────────────────────────────────────────

CREATE TABLE public.pm_tasks (
    id BIGSERIAL PRIMARY KEY,
    pm_project_id bigint NOT NULL REFERENCES public.pm_projects(id),
    team_id       bigint NOT NULL REFERENCES public.pm_teams(id),
    title text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'pass', 'fail',
                           'budget_exceeded', 'crashed', 'stopped')),
    run_id    text NOT NULL,
    repo_path text NOT NULL,
    log_dir   text NOT NULL,
    pid       integer,   -- NULL for a run Throughline did not launch (adopted)
    tokens_used bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    ended_at   timestamptz
);

CREATE UNIQUE INDEX idx_pm_tasks_repo_run ON public.pm_tasks (repo_path, run_id);

CREATE TABLE public.pm_task_events (
    id BIGSERIAL PRIMARY KEY,
    task_id       bigint NOT NULL REFERENCES public.pm_tasks(id) ON DELETE CASCADE,
    assignment_id bigint REFERENCES public.pm_assignments(id),
    step        text NOT NULL CHECK (step IN ('analyst', 'executor', 'tester')),
    iteration   integer,
    event_type  text NOT NULL CHECK (event_type IN ('started', 'log_update', 'verdict', 'error')),
    message     text,
    detail_path text,
    tokens_used bigint,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_pm_task_events_task ON public.pm_task_events (task_id, created_at);
