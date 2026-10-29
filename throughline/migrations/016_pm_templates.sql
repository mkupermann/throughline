-- Versioned blueprints are separate from editable operational resources.
CREATE TABLE IF NOT EXISTS public.pm_templates (
    id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('project', 'team', 'role')),
    name text NOT NULL CHECK (length(trim(name)) > 0),
    description text NOT NULL DEFAULT '',
    content jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(content) = 'object'),
    version integer NOT NULL DEFAULT 1 CHECK (version > 0),
    archived boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.pm_template_versions (
    template_id bigint NOT NULL REFERENCES public.pm_templates(id),
    version integer NOT NULL,
    snapshot jsonb NOT NULL,
    PRIMARY KEY (template_id, version)
);
ALTER TABLE public.pm_projects ADD COLUMN IF NOT EXISTS template_snapshot jsonb;
ALTER TABLE public.pm_teams ADD COLUMN IF NOT EXISTS template_snapshot jsonb;
ALTER TABLE public.pm_roles ADD COLUMN IF NOT EXISTS template_snapshot jsonb;

DO $$
DECLARE researcher bigint; builder bigint; reviewer bigint; team bigint;
BEGIN
    -- A schema snapshot may already contain starter templates. Preserve that data.
    IF EXISTS (SELECT 1 FROM public.pm_templates) THEN
        RETURN;
    END IF;
    INSERT INTO public.pm_templates(kind,name,description,content) VALUES
    ('role','UX researcher','Turn an unclear problem into an evidence-backed brief.',
    '{"instructions":"Investigate user goals, current workflows and friction. Separate observations from hypotheses.","expected_output":"Research brief with evidence, priorities and open questions","allowed_tools":"Read-only repository and browser inspection"}') RETURNING id INTO researcher;
    INSERT INTO public.pm_templates(kind,name,description,content) VALUES
    ('role','Product engineer','Implement a focused, testable improvement.',
    '{"instructions":"Implement the approved brief. Preserve existing behavior, verify the changed workflow and document limitations.","expected_output":"Reviewable implementation and verification evidence","allowed_tools":"Repository editing, local tests and browser verification"}') RETURNING id INTO builder;
    INSERT INTO public.pm_templates(kind,name,description,content) VALUES
    ('role','Independent reviewer','Challenge the implementation against the brief.',
    '{"instructions":"Review independently of the author. Check usability, accessibility, provenance and regression risk. Report evidence and severity.","expected_output":"Findings with reproduction steps and an explicit review decision","allowed_tools":"Read-only repository, tests and browser inspection"}') RETURNING id INTO reviewer;
    INSERT INTO public.pm_templates(kind,name,description,content) VALUES
    ('team','Product improvement team','Research, implement, then independently review.',
    jsonb_build_object('workflow','Research → implementation → independent review','review_policy','A separate reviewer verifies acceptance criteria before human approval.','role_templates',jsonb_build_array(jsonb_build_object('id',researcher,'version',1),jsonb_build_object('id',builder,'version',1),jsonb_build_object('id',reviewer,'version',1)))) RETURNING id INTO team;
    INSERT INTO public.pm_templates(kind,name,description,content) VALUES
    ('project','Evidence-led UX redesign','Improve a real workflow with measurable acceptance criteria.',
    jsonb_build_object('objective','Make the target workflow clear, reliable and accessible.','stages','Discovery → design → implementation → verification','deliverables','Research brief; implemented workflow; browser evidence; independent review','acceptance_criteria','Complete the core task with keyboard and mobile; preserve existing behavior; disclose remaining limitations.','team_template',jsonb_build_object('id',team,'version',1)));
    INSERT INTO public.pm_template_versions(template_id,version,snapshot)
    SELECT id,version,to_jsonb(t) FROM public.pm_templates t;
END $$;
