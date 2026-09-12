--
-- PostgreSQL database dump
--

-- Dumped from database version 16.13 (Homebrew)
-- Dumped by pg_dump version 16.13 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: memory_category; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.memory_category AS ENUM (
    'decision',
    'pattern',
    'insight',
    'preference',
    'contact',
    'error_solution',
    'project_context',
    'workflow'
);


--
-- Name: message_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.message_role AS ENUM (
    'user',
    'assistant',
    'system',
    'tool_result'
);


--
-- Name: project_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.project_status AS ENUM (
    'active',
    'paused',
    'completed',
    'archived'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id bigint NOT NULL,
    session_id uuid NOT NULL,
    -- Name of the script or scheduled tool that produced this conversation;
    -- NULL when a person did. Every default listing filters on IS NULL: on the
    -- corpus this was written against, 3,017 of 3,606 conversations were the
    -- tool's own `claude -p` calls. See sql/migrations/004_generated_by.sql.
    generated_by text,
    project_path text,
    project_name text GENERATED ALWAYS AS (
CASE
    WHEN (project_path IS NULL) THEN 'unknown'::text
    ELSE split_part(replace(project_path, '\'::text, '/'::text), '/'::text, '-1'::integer)
END) STORED,
    model text,
    entrypoint text,
    git_branch text,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    message_count integer DEFAULT 0,
    token_count_in integer,
    token_count_out integer,
    cost_usd numeric(10,4),
    summary text,
    tags text[] DEFAULT '{}'::text[],
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    source_tool text
);


--
-- Name: conversations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversations_id_seq OWNED BY public.conversations.id;


--
-- Name: embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.embeddings (
    id bigint NOT NULL,
    -- Closed vocabulary; see memory_chunks.source_type above.
    source_type text NOT NULL
        CONSTRAINT embeddings_source_type_check
        CHECK (source_type IN ('memory_chunk', 'message')),
    source_id bigint NOT NULL,
    embedding_1536 public.vector(1536),
    model text DEFAULT 'text-embedding-3-small'::text,
    created_at timestamp with time zone DEFAULT now(),
    embedding_768 public.vector(768)
);


--
-- Name: embeddings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.embeddings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: embeddings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.embeddings_id_seq OWNED BY public.embeddings.id;


--
-- Name: entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entities (
    id bigint NOT NULL,
    entity_type text NOT NULL,
    name text NOT NULL,
    canonical_name text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb,
    first_seen timestamp with time zone DEFAULT now(),
    last_seen timestamp with time zone DEFAULT now(),
    mention_count integer DEFAULT 1,
    project_name text,
    confidence numeric(3,2) DEFAULT 0.8,
    metadata jsonb DEFAULT '{}'::jsonb
);


--
-- Name: entities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.entities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: entities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.entities_id_seq OWNED BY public.entities.id;


--
-- Name: entity_mentions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entity_mentions (
    id bigint NOT NULL,
    entity_id bigint,
    source_type text NOT NULL,
    source_id bigint NOT NULL,
    context_snippet text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: entity_mentions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.entity_mentions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: entity_mentions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.entity_mentions_id_seq OWNED BY public.entity_mentions.id;


--
-- Name: ingestion_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingestion_log (
    id bigint NOT NULL,
    file_path text NOT NULL,
    file_hash text NOT NULL,
    ingested_at timestamp with time zone DEFAULT now(),
    record_count integer
);


--
-- Name: ingestion_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ingestion_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ingestion_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ingestion_log_id_seq OWNED BY public.ingestion_log.id;


--
-- Name: memory_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_chunks (
    id bigint NOT NULL,
    -- Closed vocabulary. See sql/migrations/003_source_type_vocabularies.sql:
    -- a status query once filtered on two spellings that had never been
    -- written, matched nothing, and reported "no extraction yet" on a
    -- database full of it. Adding a source type means editing this list and
    -- writing a migration.
    source_type text NOT NULL
        CONSTRAINT memory_chunks_source_type_check
        CHECK (source_type IN ('conversation', 'manual', 'mcp_write',
                               'reflection_merge', 'consolidation')),
    source_id bigint,
    content text NOT NULL,
    category public.memory_category NOT NULL,
    tags text[] DEFAULT '{}'::text[],
    confidence numeric(3,2) DEFAULT 0.80,
    project_name text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    superseded_by bigint,
    superseded_at timestamp with time zone,
    status text DEFAULT 'active'::text,
    merged_from bigint[] DEFAULT '{}'::bigint[],
    access_count integer DEFAULT 0,
    last_accessed timestamp with time zone
);


--
-- Name: memory_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.memory_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: memory_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.memory_chunks_id_seq OWNED BY public.memory_chunks.id;


--
-- Name: memory_reflections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_reflections (
    id bigint NOT NULL,
    reflection_type text NOT NULL,
    affected_chunks bigint[],
    action_taken text,
    reasoning text,
    confidence numeric(3,2),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: memory_reflections_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.memory_reflections_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: memory_reflections_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.memory_reflections_id_seq OWNED BY public.memory_reflections.id;


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id bigint NOT NULL,
    conversation_id bigint NOT NULL,
    uuid uuid,
    parent_uuid uuid,
    role public.message_role NOT NULL,
    content text,
    content_blocks jsonb,
    tool_calls jsonb,
    tool_name text,
    token_count integer,
    is_sidechain boolean DEFAULT false,
    model text,
    duration_ms integer,
    created_at timestamp with time zone NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb
);


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id bigint NOT NULL,
    name text NOT NULL,
    description text,
    contacts jsonb DEFAULT '[]'::jsonb,
    decisions jsonb DEFAULT '[]'::jsonb,
    status public.project_status DEFAULT 'active'::public.project_status,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.projects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;


--
-- Name: prompts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prompts (
    id bigint NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    content text NOT NULL,
    variables jsonb DEFAULT '[]'::jsonb,
    source_path text,
    usage_count integer DEFAULT 0,
    tags text[] DEFAULT '{}'::text[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: prompts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.prompts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: prompts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.prompts_id_seq OWNED BY public.prompts.id;


--
-- Name: relationships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.relationships (
    id bigint NOT NULL,
    from_entity bigint,
    to_entity bigint,
    relation_type text NOT NULL,
    valid_from timestamp with time zone DEFAULT now(),
    valid_until timestamp with time zone,
    confidence numeric(3,2) DEFAULT 0.8,
    source_type text,
    source_id bigint,
    attributes jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: relationships_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.relationships_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: relationships_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.relationships_id_seq OWNED BY public.relationships.id;


--
-- Name: skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skills (
    id bigint NOT NULL,
    name text NOT NULL,
    version text DEFAULT '1.0.0'::text,
    description text,
    path text NOT NULL,
    triggers text[],
    last_used timestamp with time zone,
    use_count integer DEFAULT 0,
    config jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    file_created timestamp with time zone,
    file_modified timestamp with time zone
);


--
-- Name: skills_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.skills_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: skills_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.skills_id_seq OWNED BY public.skills.id;


--
-- Name: v_conversation_stats; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_conversation_stats AS
 SELECT project_name,
    count(*) AS sessions,
    sum(message_count) AS total_messages,
    round(avg((token_count_in + token_count_out))) AS avg_tokens,
    sum(cost_usd) AS total_cost
   FROM public.conversations
  GROUP BY project_name
  ORDER BY (count(*)) DESC;


--
-- Name: conversations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations ALTER COLUMN id SET DEFAULT nextval('public.conversations_id_seq'::regclass);


--
-- Name: embeddings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings ALTER COLUMN id SET DEFAULT nextval('public.embeddings_id_seq'::regclass);


--
-- Name: entities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities ALTER COLUMN id SET DEFAULT nextval('public.entities_id_seq'::regclass);


--
-- Name: entity_mentions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_mentions ALTER COLUMN id SET DEFAULT nextval('public.entity_mentions_id_seq'::regclass);


--
-- Name: ingestion_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_log ALTER COLUMN id SET DEFAULT nextval('public.ingestion_log_id_seq'::regclass);


--
-- Name: memory_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_chunks ALTER COLUMN id SET DEFAULT nextval('public.memory_chunks_id_seq'::regclass);


--
-- Name: memory_reflections id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_reflections ALTER COLUMN id SET DEFAULT nextval('public.memory_reflections_id_seq'::regclass);


--
-- Name: messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);


--
-- Name: projects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);


--
-- Name: prompts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompts ALTER COLUMN id SET DEFAULT nextval('public.prompts_id_seq'::regclass);


--
-- Name: relationships id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relationships ALTER COLUMN id SET DEFAULT nextval('public.relationships_id_seq'::regclass);


--
-- Name: skills id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills ALTER COLUMN id SET DEFAULT nextval('public.skills_id_seq'::regclass);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_session_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_session_id_key UNIQUE (session_id);


--
-- Name: embeddings embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_pkey PRIMARY KEY (id);


--
-- Name: embeddings embeddings_source_model_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.embeddings
    ADD CONSTRAINT embeddings_source_model_key UNIQUE (source_type, source_id, model);


--
-- Name: entities entities_entity_type_canonical_name_project_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities
    ADD CONSTRAINT entities_entity_type_canonical_name_project_name_key UNIQUE (entity_type, canonical_name, project_name);


--
-- Name: entities entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities
    ADD CONSTRAINT entities_pkey PRIMARY KEY (id);


--
-- Name: entity_mentions entity_mentions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_mentions
    ADD CONSTRAINT entity_mentions_pkey PRIMARY KEY (id);


--
-- Name: ingestion_log ingestion_log_file_path_file_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_log
    ADD CONSTRAINT ingestion_log_file_path_file_hash_key UNIQUE (file_path, file_hash);


--
-- Name: ingestion_log ingestion_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_log
    ADD CONSTRAINT ingestion_log_pkey PRIMARY KEY (id);


--
-- Name: memory_chunks memory_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_chunks
    ADD CONSTRAINT memory_chunks_pkey PRIMARY KEY (id);


--
-- Name: memory_reflections memory_reflections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_reflections
    ADD CONSTRAINT memory_reflections_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: projects projects_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_name_key UNIQUE (name);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: prompts prompts_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompts
    ADD CONSTRAINT prompts_name_key UNIQUE (name);


--
-- Name: prompts prompts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompts
    ADD CONSTRAINT prompts_pkey PRIMARY KEY (id);


--
-- Name: relationships relationships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_pkey PRIMARY KEY (id);


--
-- Name: skills skills_name_path_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_name_path_key UNIQUE (name, path);


--
-- Name: skills skills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_pkey PRIMARY KEY (id);


--
-- Name: idx_chunks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_status ON public.memory_chunks USING btree (status);


--
-- Name: idx_conversations_project_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_project_name ON public.conversations USING btree (project_name);


--
-- Name: idx_conversations_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_session_id ON public.conversations USING btree (session_id);


--
-- Name: idx_conversations_source_tool; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_source_tool ON public.conversations USING btree (source_tool);


--
-- Name: idx_conversations_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_started_at ON public.conversations USING btree (started_at DESC);


--
-- Name: idx_conversations_tags; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_tags ON public.conversations USING gin (tags);


--
-- Name: idx_embeddings_1536_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embeddings_1536_hnsw ON public.embeddings USING hnsw (embedding_1536 public.vector_cosine_ops) WHERE (embedding_1536 IS NOT NULL);


--
-- Name: idx_embeddings_768_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embeddings_768_hnsw ON public.embeddings USING hnsw (embedding_768 public.vector_cosine_ops) WHERE (embedding_768 IS NOT NULL);


--
-- Name: idx_embeddings_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_embeddings_source ON public.embeddings USING btree (source_type, source_id);


--
-- Name: idx_entities_canonical; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entities_canonical ON public.entities USING btree (canonical_name);


--
-- Name: idx_entities_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entities_project ON public.entities USING btree (project_name);


--
-- Name: idx_entities_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_entities_type ON public.entities USING btree (entity_type);


--
-- Name: idx_memory_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memory_category ON public.memory_chunks USING btree (category);


--
-- Name: idx_memory_content_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memory_content_trgm ON public.memory_chunks USING gin (content public.gin_trgm_ops);


--
-- Name: idx_memory_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memory_project ON public.memory_chunks USING btree (project_name);


--
-- Name: idx_memory_tags; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memory_tags ON public.memory_chunks USING gin (tags);


--
-- Name: idx_mentions_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mentions_entity ON public.entity_mentions USING btree (entity_id);


--
-- Name: idx_mentions_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mentions_source ON public.entity_mentions USING btree (source_type, source_id);


--
-- Name: idx_messages_content_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_content_trgm ON public.messages USING gin (content public.gin_trgm_ops);


--
-- Name: idx_messages_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_conversation_id ON public.messages USING btree (conversation_id);


--
-- Name: idx_messages_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_created_at ON public.messages USING btree (created_at DESC);


--
-- Name: idx_messages_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_role ON public.messages USING btree (role);


--
-- Name: idx_messages_tool_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_tool_name ON public.messages USING btree (tool_name) WHERE (tool_name IS NOT NULL);


--
-- Name: idx_reflections_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reflections_created ON public.memory_reflections USING btree (created_at DESC);


--
-- Name: idx_reflections_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reflections_type ON public.memory_reflections USING btree (reflection_type);


--
-- Name: idx_rel_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rel_from ON public.relationships USING btree (from_entity);


--
-- Name: idx_rel_to; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rel_to ON public.relationships USING btree (to_entity);


--
-- Name: idx_rel_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rel_type ON public.relationships USING btree (relation_type);


--
-- Name: entity_mentions entity_mentions_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entity_mentions
    ADD CONSTRAINT entity_mentions_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: memory_chunks memory_chunks_superseded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_chunks
    ADD CONSTRAINT memory_chunks_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES public.memory_chunks(id) ON DELETE SET NULL;


--
-- Name: messages messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: relationships relationships_from_entity_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_from_entity_fkey FOREIGN KEY (from_entity) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: relationships relationships_to_entity_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.relationships
    ADD CONSTRAINT relationships_to_entity_fkey FOREIGN KEY (to_entity) REFERENCES public.entities(id) ON DELETE CASCADE;


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

--
-- Welle D: Cline/Cursor-style AI provider & model management. See
-- migrations/008_pm_ai_providers.sql.
--

CREATE TABLE public.pm_ai_providers (
    id BIGSERIAL PRIMARY KEY,
    name text NOT NULL,
    provider_type text NOT NULL CHECK (provider_type IN (
        'openai', 'anthropic', 'mistral', 'google', 'openrouter',
        'ollama', 'openai_compatible'
    )),
    base_url text,
    api_key text,
    custom_models jsonb NOT NULL DEFAULT '[]',
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);


--
-- PostgreSQL database dump complete
--
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_conversation_uuid
    ON public.messages (conversation_id, uuid) WHERE uuid IS NOT NULL;
-- User-facing labels are independent of imported folder identifiers.
CREATE TABLE IF NOT EXISTS public.project_names (
    project_key text PRIMARY KEY,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- Generated display names retain their origin and sampled source conversations.
ALTER TABLE public.project_names ADD COLUMN IF NOT EXISTS name_origin text NOT NULL DEFAULT 'user'
    CHECK (name_origin IN ('user', 'model'));
ALTER TABLE public.project_names ADD COLUMN IF NOT EXISTS source_conversation_ids bigint[] NOT NULL DEFAULT '{}';
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

-- Curated catalog v1: additive seed; user templates and historical snapshots stay unchanged.
-- Stable catalog_key values distinguish built-ins from user names. IDs are resolved here,
-- never hardcoded, and all project/team references pin immutable version 1 snapshots.
DO $catalog$
DECLARE
    entry jsonb;
    body jsonb;
    ids jsonb := '{}'::jsonb;
    created_id bigint;
    child_key text;
    role_refs jsonb;
BEGIN
    FOR entry IN SELECT value FROM jsonb_array_elements($templates$
[
  {
    "key": "finance-role-0",
    "kind": "role",
    "name": "Finance data analyst",
    "description": "Source reconciliation, data dictionary and exception register.",
    "content": {
      "category": "finance",
      "instructions": "Reconcile approved ledger exports, time periods and currency assumptions; document missing accounts and unmatched totals. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Source reconciliation, data dictionary and exception register",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "finance-role-1",
    "kind": "role",
    "name": "Financial scenario modeller",
    "description": "Formula-backed model, assumption register and sensitivity analysis.",
    "content": {
      "category": "finance",
      "instructions": "Build transparent base, downside and upside scenarios with editable drivers; distinguish observed values from forecasts. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Formula-backed model, assumption register and sensitivity analysis",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "finance-role-2",
    "kind": "role",
    "name": "Finance control reviewer",
    "description": "Recalculation evidence, control findings and finance-owner approval checklist.",
    "content": {
      "category": "finance",
      "instructions": "Independently recompute material figures and challenge assumptions. A qualified finance owner approves decisions; do not place trades, submit filings or move funds. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Recalculation evidence, control findings and finance-owner approval checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "finance-team",
    "kind": "team",
    "name": "Finance planning team",
    "description": "Specialized analysis, planning and independent review for finance.",
    "role_keys": [
      "finance-role-0",
      "finance-role-1",
      "finance-role-2"
    ],
    "content": {
      "category": "finance",
      "workflow": "Finance data analyst → Financial scenario modeller → Finance control reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls. Qualified domain professionals must approve conclusions before reliance; the team does not provide autonomous regulated advice or decisions."
    }
  },
  {
    "key": "finance-project-0",
    "kind": "project",
    "name": "Rolling cash-flow forecast",
    "description": "Prepare a thirteen-week liquidity view from approved cash, receivables and payables exports.",
    "team_key": "finance-team",
    "content": {
      "category": "finance",
      "objective": "Prepare a thirteen-week liquidity view from approved cash, receivables and payables exports.",
      "stages": "Reconcile opening cash → schedule receipts and payments → stress-test timing → finance review",
      "deliverables": "Weekly cash schedule; overdue-receivables map; downside scenario; funding decision brief",
      "acceptance_criteria": "Opening cash ties to the approved statement; totals recalculate; timing assumptions have owners; finance owner signs off before action."
    }
  },
  {
    "key": "finance-project-1",
    "kind": "project",
    "name": "Budget variance review",
    "description": "Explain actual versus approved budget and identify accountable follow-up actions.",
    "team_key": "finance-team",
    "content": {
      "category": "finance",
      "objective": "Explain actual versus approved budget and identify accountable follow-up actions.",
      "stages": "Confirm period and cost centres → reconcile actuals → isolate price and volume drivers → review actions",
      "deliverables": "Variance bridge; materiality-ranked explanations; owner/action register; management summary",
      "acceptance_criteria": "Actuals tie to the source ledger; every material variance has evidence or an explicit unknown; proposed changes are approved by the budget owner."
    }
  },
  {
    "key": "finance-project-2",
    "kind": "project",
    "name": "Unit economics assessment",
    "description": "Evaluate product or customer-segment economics without hiding allocation assumptions.",
    "team_key": "finance-team",
    "content": {
      "category": "finance",
      "objective": "Evaluate product or customer-segment economics without hiding allocation assumptions.",
      "stages": "Define unit and cohort → reconcile revenue and costs → model contribution and break-even → challenge sensitivity",
      "deliverables": "Unit-economics workbook; allocation policy; cohort comparison; sensitivity chart",
      "acceptance_criteria": "Contribution reconciles to inputs; acquisition and retention assumptions are explicit; no unsupported investment recommendation; finance owner approves use."
    }
  },
  {
    "key": "science-role-0",
    "kind": "role",
    "name": "Research evidence curator",
    "description": "Search protocol, evidence table and exclusion log.",
    "content": {
      "category": "science",
      "instructions": "Define inclusion criteria before screening; record source identifiers, exclusions and conflicting evidence. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Search protocol, evidence table and exclusion log",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "science-role-1",
    "kind": "role",
    "name": "Experiment designer",
    "description": "Protocol, analysis plan, environment specification and results table.",
    "content": {
      "category": "science",
      "instructions": "Translate the research question into falsifiable hypotheses, baselines, controls and reproducible analysis steps. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Protocol, analysis plan, environment specification and results table",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "science-role-2",
    "kind": "role",
    "name": "Reproducibility reviewer",
    "description": "Reproduction log, uncertainty critique and limitations statement.",
    "content": {
      "category": "science",
      "instructions": "Independently reproduce key computations, inspect leakage and uncertainty, and separate exploratory from confirmatory findings. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Reproduction log, uncertainty critique and limitations statement",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "science-team",
    "kind": "team",
    "name": "Reproducible research team",
    "description": "Specialized analysis, planning and independent review for science.",
    "role_keys": [
      "science-role-0",
      "science-role-1",
      "science-role-2"
    ],
    "content": {
      "category": "science",
      "workflow": "Research evidence curator → Experiment designer → Reproducibility reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "science-project-0",
    "kind": "project",
    "name": "Reproducible experiment",
    "description": "Test a bounded hypothesis against an explicit baseline with reproducible data and code.",
    "team_key": "science-team",
    "content": {
      "category": "science",
      "objective": "Test a bounded hypothesis against an explicit baseline with reproducible data and code.",
      "stages": "Specify hypothesis → freeze protocol → run pilot → execute comparisons → independent reproduction",
      "deliverables": "Preregisterable protocol; data provenance; scripts and environment; effect-size report",
      "acceptance_criteria": "Baseline and ablations use comparable budgets; seeds and exclusions are recorded; uncertainty accompanies estimates; another reviewer reproduces key outputs."
    }
  },
  {
    "key": "science-project-1",
    "kind": "project",
    "name": "Structured literature review",
    "description": "Map published evidence for a narrowly defined question with transparent selection.",
    "team_key": "science-team",
    "content": {
      "category": "science",
      "objective": "Map published evidence for a narrowly defined question with transparent selection.",
      "stages": "Define question → document search → screen and extract → assess quality → synthesize",
      "deliverables": "Search strings and dates; inclusion/exclusion ledger; evidence matrix; disagreement and gap map",
      "acceptance_criteria": "Claims link to inspected sources; exclusions are auditable; study limitations are retained; synthesis does not equate study count with evidence strength."
    }
  },
  {
    "key": "science-project-2",
    "kind": "project",
    "name": "Dataset quality assessment",
    "description": "Determine whether a dataset supports its intended scientific analysis.",
    "team_key": "science-team",
    "content": {
      "category": "science",
      "objective": "Determine whether a dataset supports its intended scientific analysis.",
      "stages": "Inventory provenance → profile missingness → inspect leakage and bias → test suitability → review",
      "deliverables": "Data dictionary; quality checks; leakage report; subgroup coverage; fitness-for-use memo",
      "acceptance_criteria": "Checks are rerunnable; train/test boundaries are explicit; sensitive fields are handled under approved access; suitability claims stay within measured coverage."
    }
  },
  {
    "key": "education-role-0",
    "kind": "role",
    "name": "Learning needs analyst",
    "description": "Learner assumptions, prerequisite map and outcome specification.",
    "content": {
      "category": "education",
      "instructions": "Identify learner prerequisites, context and measurable learning outcomes; use anonymized examples and avoid inferring student ability from demographics. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Learner assumptions, prerequisite map and outcome specification",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "education-role-1",
    "kind": "role",
    "name": "Instructional designer",
    "description": "Lesson sequence, practice tasks and assessment rubric.",
    "content": {
      "category": "education",
      "instructions": "Align explanations, worked examples, practice and feedback to the target outcomes and available teaching time. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Lesson sequence, practice tasks and assessment rubric",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "education-role-2",
    "kind": "role",
    "name": "Assessment accessibility reviewer",
    "description": "Alignment matrix, accessibility findings and teacher review checklist.",
    "content": {
      "category": "education",
      "instructions": "Check answer keys, rubric alignment, language clarity and accessibility; a teacher approves assessment and accommodations. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Alignment matrix, accessibility findings and teacher review checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "education-team",
    "kind": "team",
    "name": "Learning design team",
    "description": "Specialized analysis, planning and independent review for education.",
    "role_keys": [
      "education-role-0",
      "education-role-1",
      "education-role-2"
    ],
    "content": {
      "category": "education",
      "workflow": "Learning needs analyst → Instructional designer → Assessment accessibility reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "education-project-0",
    "kind": "project",
    "name": "Course module design",
    "description": "Create a teachable module with aligned outcomes, practice and assessment.",
    "team_key": "education-team",
    "content": {
      "category": "education",
      "objective": "Create a teachable module with aligned outcomes, practice and assessment.",
      "stages": "Assess prerequisites → map outcomes → draft lessons → build practice → teacher review",
      "deliverables": "Module plan; worked examples; differentiated exercises; rubric; accessible handouts",
      "acceptance_criteria": "Each outcome has practice and assessment; timing fits the timetable; examples and answer keys are checked; teacher approves before classroom use."
    }
  },
  {
    "key": "education-project-1",
    "kind": "project",
    "name": "Assessment and feedback pack",
    "description": "Prepare fair assessment materials and actionable feedback for a specified course outcome.",
    "team_key": "education-team",
    "content": {
      "category": "education",
      "objective": "Prepare fair assessment materials and actionable feedback for a specified course outcome.",
      "stages": "Define construct → draft items → create rubric → test ambiguity → moderate",
      "deliverables": "Assessment blueprint; question set; annotated answer key; feedback bank",
      "acceptance_criteria": "Items assess stated outcomes; marking criteria distinguish performance levels; accommodations are documented; a teacher verifies grading decisions."
    }
  },
  {
    "key": "education-project-2",
    "kind": "project",
    "name": "Learning support intervention",
    "description": "Plan a small evidence-informed intervention for an identified learning difficulty.",
    "team_key": "education-team",
    "content": {
      "category": "education",
      "objective": "Plan a small evidence-informed intervention for an identified learning difficulty.",
      "stages": "Collect consented baseline → identify skill gap → design practice → define progress checks → review",
      "deliverables": "Skill-gap map; practice schedule; progress measures; teacher/learner reflection prompts",
      "acceptance_criteria": "Baseline and follow-up use comparable measures; no diagnostic claim is made; personal student data is minimized; educator approves the support plan."
    }
  },
  {
    "key": "enterprise-role-0",
    "kind": "role",
    "name": "Enterprise discovery analyst",
    "description": "Current-state map, stakeholder register and dependency inventory.",
    "content": {
      "category": "enterprise",
      "instructions": "Map systems, stakeholders, dependencies and decision rights from approved records; identify evidence gaps. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Current-state map, stakeholder register and dependency inventory",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "enterprise-role-1",
    "kind": "role",
    "name": "Transformation planner",
    "description": "Options paper, phased roadmap and ownership plan.",
    "content": {
      "category": "enterprise",
      "instructions": "Compare options across rollout risk, operating cost, integration effort and organizational readiness. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Options paper, phased roadmap and ownership plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "enterprise-role-2",
    "kind": "role",
    "name": "Architecture governance reviewer",
    "description": "Decision record, gate criteria and unresolved-risk register.",
    "content": {
      "category": "enterprise",
      "instructions": "Challenge integration, security, reversibility and accountability; obtain named business and architecture owners for decisions. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Decision record, gate criteria and unresolved-risk register",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "enterprise-team",
    "kind": "team",
    "name": "Enterprise transformation team",
    "description": "Specialized analysis, planning and independent review for enterprise.",
    "role_keys": [
      "enterprise-role-0",
      "enterprise-role-1",
      "enterprise-role-2"
    ],
    "content": {
      "category": "enterprise",
      "workflow": "Enterprise discovery analyst → Transformation planner → Architecture governance reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "enterprise-project-0",
    "kind": "project",
    "name": "Application portfolio review",
    "description": "Create an evidence-backed retain, modernize or retire proposal for a defined application portfolio.",
    "team_key": "enterprise-team",
    "content": {
      "category": "enterprise",
      "objective": "Create an evidence-backed retain, modernize or retire proposal for a defined application portfolio.",
      "stages": "Inventory applications → map business capability → assess cost and risk → compare options → governance review",
      "deliverables": "Application scorecards; dependency graph; disposition proposals; sequenced roadmap",
      "acceptance_criteria": "Every disposition cites evidence and an owner; shared dependencies are checked; cost estimates state scope; governance board approves any retirement."
    }
  },
  {
    "key": "enterprise-project-1",
    "kind": "project",
    "name": "Enterprise AI pilot",
    "description": "Define a bounded AI pilot with measurable value, data boundaries and a stop decision.",
    "team_key": "enterprise-team",
    "content": {
      "category": "enterprise",
      "objective": "Define a bounded AI pilot with measurable value, data boundaries and a stop decision.",
      "stages": "Select workflow → assess data and controls → define baseline → design pilot → evaluate gate",
      "deliverables": "Pilot charter; data-flow map; evaluation dataset plan; cost envelope; go/no-go rubric",
      "acceptance_criteria": "Success and stop thresholds are agreed before evaluation; human accountability is explicit; security and process owners approve deployment."
    }
  },
  {
    "key": "enterprise-project-2",
    "kind": "project",
    "name": "Change readiness assessment",
    "description": "Assess whether a proposed enterprise change has owners, capacity and adoption support.",
    "team_key": "enterprise-team",
    "content": {
      "category": "enterprise",
      "objective": "Assess whether a proposed enterprise change has owners, capacity and adoption support.",
      "stages": "Map impacted groups → collect readiness evidence → identify gaps → plan support → sponsor review",
      "deliverables": "Impact map; readiness scorecard; communications draft; training plan; adoption measures",
      "acceptance_criteria": "Scores cite evidence; resistance is represented fairly; actions have owners and dates; sponsor approves the rollout and communication plan."
    }
  },
  {
    "key": "mid-size-role-0",
    "kind": "role",
    "name": "Business process analyst",
    "description": "Process map, baseline metrics and bottleneck register.",
    "content": {
      "category": "mid-size",
      "instructions": "Map real work, handoffs and constraints using owner interviews and approved operational data. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Process map, baseline metrics and bottleneck register",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "mid-size-role-1",
    "kind": "role",
    "name": "Practical improvement planner",
    "description": "Option comparison, implementation checklist and capacity plan.",
    "content": {
      "category": "mid-size",
      "instructions": "Prioritize affordable changes with named owners, estimated effort and a reversible pilot. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Option comparison, implementation checklist and capacity plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "mid-size-role-2",
    "kind": "role",
    "name": "Business outcome reviewer",
    "description": "Benefit validation, rollout risks and owner acceptance checklist.",
    "content": {
      "category": "mid-size",
      "instructions": "Check savings assumptions, operational disruption and measurement quality; business owner authorizes spending and process changes. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Benefit validation, rollout risks and owner acceptance checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "mid-size-team",
    "kind": "team",
    "name": "Mid-market improvement team",
    "description": "Specialized analysis, planning and independent review for mid-size business.",
    "role_keys": [
      "mid-size-role-0",
      "mid-size-role-1",
      "mid-size-role-2"
    ],
    "content": {
      "category": "mid-size",
      "workflow": "Business process analyst → Practical improvement planner → Business outcome reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "mid-size-project-0",
    "kind": "project",
    "name": "Sales-to-delivery handoff",
    "description": "Reduce lost requirements and delays between sales commitments and service delivery.",
    "team_key": "mid-size-team",
    "content": {
      "category": "mid-size",
      "objective": "Reduce lost requirements and delays between sales commitments and service delivery.",
      "stages": "Map handoff → sample recent cases → define required brief → pilot checklist → owner review",
      "deliverables": "Handoff brief; responsibility matrix; exception path; cycle-time baseline",
      "acceptance_criteria": "Required fields map to delivery needs; pilot exceptions are recorded; no customer commitment changes without owner approval; handoff time is measured."
    }
  },
  {
    "key": "mid-size-project-1",
    "kind": "project",
    "name": "ERP selection brief",
    "description": "Prepare a comparable requirements and vendor-evaluation pack before procurement.",
    "team_key": "mid-size-team",
    "content": {
      "category": "mid-size",
      "objective": "Prepare a comparable requirements and vendor-evaluation pack before procurement.",
      "stages": "Map critical workflows → separate needs from preferences → define scoring → plan demos → approve shortlist",
      "deliverables": "Prioritized requirements; weighted scorecard; demo scenarios; migration risk list",
      "acceptance_criteria": "Weights are agreed before scoring; total cost includes migration and support; vendor claims remain unverified until demonstrated; procurement owner approves."
    }
  },
  {
    "key": "mid-size-project-2",
    "kind": "project",
    "name": "Capacity and hiring plan",
    "description": "Compare workload, capacity and process alternatives before adding headcount.",
    "team_key": "mid-size-team",
    "content": {
      "category": "mid-size",
      "objective": "Compare workload, capacity and process alternatives before adding headcount.",
      "stages": "Establish demand baseline → map available capacity → model scenarios → compare alternatives → management review",
      "deliverables": "Demand/capacity model; skill-gap map; workload scenarios; hiring decision brief",
      "acceptance_criteria": "Assumptions use an explicit planning period; overtime and ramp-up are visible; alternatives are compared; managers validate workload and approve staffing decisions."
    }
  },
  {
    "key": "startup-role-0",
    "kind": "role",
    "name": "Customer discovery researcher",
    "description": "Hypothesis map, interview guide and evidence ledger.",
    "content": {
      "category": "startup",
      "instructions": "Separate founder assumptions from customer evidence; design non-leading interviews and preserve contradictory observations. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Hypothesis map, interview guide and evidence ledger",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "startup-role-1",
    "kind": "role",
    "name": "Experiment operator",
    "description": "Experiment brief, prototype specification and results log.",
    "content": {
      "category": "startup",
      "instructions": "Design a small reversible market experiment with a clear metric, resource cap and stop condition. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Experiment brief, prototype specification and results log",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "startup-role-2",
    "kind": "role",
    "name": "Venture evidence reviewer",
    "description": "Evidence-strength assessment and continue/change/stop recommendation.",
    "content": {
      "category": "startup",
      "instructions": "Challenge sample bias, vanity metrics and causal claims; founder owns product and funding decisions. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Evidence-strength assessment and continue/change/stop recommendation",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "startup-team",
    "kind": "team",
    "name": "Startup validation team",
    "description": "Specialized analysis, planning and independent review for startups.",
    "role_keys": [
      "startup-role-0",
      "startup-role-1",
      "startup-role-2"
    ],
    "content": {
      "category": "startup",
      "workflow": "Customer discovery researcher → Experiment operator → Venture evidence reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "startup-project-0",
    "kind": "project",
    "name": "Problem validation sprint",
    "description": "Test whether a specific customer segment experiences a problem worth solving.",
    "team_key": "startup-team",
    "content": {
      "category": "startup",
      "objective": "Test whether a specific customer segment experiences a problem worth solving.",
      "stages": "State assumptions → recruit target users → conduct interviews → synthesize patterns → decide next experiment",
      "deliverables": "Interview script; consent approach; anonymized findings; problem evidence scorecard",
      "acceptance_criteria": "Claims are linked to observations; disconfirming evidence is included; sample limits are explicit; no traction is fabricated."
    }
  },
  {
    "key": "startup-project-1",
    "kind": "project",
    "name": "MVP scope and launch plan",
    "description": "Define the smallest usable product that tests the riskiest value assumption.",
    "team_key": "startup-team",
    "content": {
      "category": "startup",
      "objective": "Define the smallest usable product that tests the riskiest value assumption.",
      "stages": "Choose hypothesis → map core journey → cut scope → define instrumented pilot → review",
      "deliverables": "MVP brief; must-have backlog; excluded scope; activation metric; rollout checklist",
      "acceptance_criteria": "Every included feature supports the hypothesis or basic usability; success and stop thresholds are set; rollback and support ownership are defined."
    }
  },
  {
    "key": "startup-project-2",
    "kind": "project",
    "name": "Pricing experiment design",
    "description": "Design a limited pricing test with transparent offer terms and interpretable outcomes.",
    "team_key": "startup-team",
    "content": {
      "category": "startup",
      "objective": "Design a limited pricing test with transparent offer terms and interpretable outcomes.",
      "stages": "Define segment → map current offer → compare test designs → set guardrails → approve experiment",
      "deliverables": "Pricing hypotheses; test matrix; metrics; customer communication draft; decision rubric",
      "acceptance_criteria": "Existing commitments are respected; sample and seasonality limits are stated; no automatic billing changes occur; founder approves the experiment."
    }
  },
  {
    "key": "engineering-role-0",
    "kind": "role",
    "name": "Systems analyst",
    "description": "Behavior map, constraints and implementation brief.",
    "content": {
      "category": "engineering",
      "instructions": "Trace the requested behavior through code, data and interfaces before proposing a change. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Behavior map, constraints and implementation brief",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "engineering-role-1",
    "kind": "role",
    "name": "Software implementer",
    "description": "Reviewable patch, test evidence and release notes.",
    "content": {
      "category": "engineering",
      "instructions": "Implement the approved scope with focused tests, migration notes and a reversible release path. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Reviewable patch, test evidence and release notes",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "engineering-role-2",
    "kind": "role",
    "name": "Technical verification reviewer",
    "description": "Severity-ranked findings, reproduction steps and release recommendation.",
    "content": {
      "category": "engineering",
      "instructions": "Independently reproduce failures and check correctness, compatibility and security boundaries. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Severity-ranked findings, reproduction steps and release recommendation",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "engineering-team",
    "kind": "team",
    "name": "Software delivery team",
    "description": "Specialized analysis, planning and independent review for engineering.",
    "role_keys": [
      "engineering-role-0",
      "engineering-role-1",
      "engineering-role-2"
    ],
    "content": {
      "category": "engineering",
      "workflow": "Systems analyst → Software implementer → Technical verification reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "engineering-project-0",
    "kind": "project",
    "name": "API integration delivery",
    "description": "Deliver a bounded integration with clear contracts and failure behavior.",
    "team_key": "engineering-team",
    "content": {
      "category": "engineering",
      "objective": "Deliver a bounded integration with clear contracts and failure behavior.",
      "stages": "Inspect contracts → model authentication and errors → implement adapter → test failures → review",
      "deliverables": "Contract map; adapter; integration tests; retry policy; operations notes",
      "acceptance_criteria": "Timeouts and malformed responses are tested; secrets stay outside code; retries avoid duplicate effects; owner approves rollout."
    }
  },
  {
    "key": "engineering-project-1",
    "kind": "project",
    "name": "Legacy refactoring plan",
    "description": "Improve a constrained component while preserving its externally observable behavior.",
    "team_key": "engineering-team",
    "content": {
      "category": "engineering",
      "objective": "Improve a constrained component while preserving its externally observable behavior.",
      "stages": "Map behavior → capture characterization checks → isolate change → refactor → compare results",
      "deliverables": "Behavior inventory; dependency map; focused patch; before/after tests",
      "acceptance_criteria": "Public behavior remains compatible or changes are documented; tests cover meaningful edge cases; rollback is defined; independent reviewer verifies scope."
    }
  },
  {
    "key": "engineering-project-2",
    "kind": "project",
    "name": "Incident analysis and prevention",
    "description": "Explain a service incident from evidence and prioritize verifiable prevention actions.",
    "team_key": "engineering-team",
    "content": {
      "category": "engineering",
      "objective": "Explain a service incident from evidence and prioritize verifiable prevention actions.",
      "stages": "Build timeline → collect signals → test causal explanations → propose controls → review",
      "deliverables": "Evidence-linked timeline; contributing factors; corrective-action register; follow-up checks",
      "acceptance_criteria": "Uncertainty remains visible; people are not blamed for systemic gaps; each action has an owner and verification method; incident owner validates the report."
    }
  },
  {
    "key": "product-design-role-0",
    "kind": "role",
    "name": "Product discovery researcher",
    "description": "Journey map, research notes and prioritized user needs.",
    "content": {
      "category": "product-design",
      "instructions": "Observe the target workflow and connect friction to evidence rather than preference. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Journey map, research notes and prioritized user needs",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "product-design-role-1",
    "kind": "role",
    "name": "Interaction designer",
    "description": "Flow specification, screen proposal and component behavior notes.",
    "content": {
      "category": "product-design",
      "instructions": "Produce a focused interaction proposal covering primary, empty, loading, error and recovery states. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Flow specification, screen proposal and component behavior notes",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "product-design-role-2",
    "kind": "role",
    "name": "Usability accessibility reviewer",
    "description": "Browser evidence, accessibility findings and acceptance review.",
    "content": {
      "category": "product-design",
      "instructions": "Verify keyboard use, screen-reader semantics, contrast and task completion; distinguish automated checks from user research. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Browser evidence, accessibility findings and acceptance review",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "product-design-team",
    "kind": "team",
    "name": "Product discovery team",
    "description": "Specialized analysis, planning and independent review for product and design.",
    "role_keys": [
      "product-design-role-0",
      "product-design-role-1",
      "product-design-role-2"
    ],
    "content": {
      "category": "product-design",
      "workflow": "Product discovery researcher → Interaction designer → Usability accessibility reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "product-design-project-0",
    "kind": "project",
    "name": "Usability improvement sprint",
    "description": "Improve one important workflow and verify that users can complete it reliably.",
    "team_key": "product-design-team",
    "content": {
      "category": "product-design",
      "objective": "Improve one important workflow and verify that users can complete it reliably.",
      "stages": "Choose task → observe baseline → design changes → implement prototype → evaluate",
      "deliverables": "Task script; friction map; revised flow; browser evidence; prioritized findings",
      "acceptance_criteria": "Primary and failure paths are exercised; keyboard and narrow-screen use are checked; measured results identify the test population and limitations."
    }
  },
  {
    "key": "product-design-project-1",
    "kind": "project",
    "name": "Design system consolidation",
    "description": "Reduce inconsistent interface patterns through documented reusable components.",
    "team_key": "product-design-team",
    "content": {
      "category": "product-design",
      "objective": "Reduce inconsistent interface patterns through documented reusable components.",
      "stages": "Inventory screens → identify variants → define tokens → consolidate components → regression review",
      "deliverables": "Component inventory; token map; usage rules; migration list; visual comparisons",
      "acceptance_criteria": "Tokens cover light and dark states where supported; components define focus and error behavior; representative screens are visually checked."
    }
  },
  {
    "key": "product-design-project-2",
    "kind": "project",
    "name": "Onboarding activation review",
    "description": "Identify and reduce friction between first visit and a meaningful first outcome.",
    "team_key": "product-design-team",
    "content": {
      "category": "product-design",
      "objective": "Identify and reduce friction between first visit and a meaningful first outcome.",
      "stages": "Define activation → map first-run journey → inspect drop-offs → redesign guidance → evaluate",
      "deliverables": "Activation definition; onboarding flow; empty-state copy; instrumentation plan",
      "acceptance_criteria": "First success is observable; optional setup can be deferred where feasible; no misleading progress or consent patterns; product owner approves metrics."
    }
  },
  {
    "key": "marketing-role-0",
    "kind": "role",
    "name": "Audience insight researcher",
    "description": "Audience needs, channel evidence and message hypotheses.",
    "content": {
      "category": "marketing",
      "instructions": "Build a bounded audience and channel brief from approved analytics and inspected sources; mark hypotheses. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Audience needs, channel evidence and message hypotheses",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "marketing-role-1",
    "kind": "role",
    "name": "Campaign content planner",
    "description": "Campaign brief, channel drafts and measurement plan.",
    "content": {
      "category": "marketing",
      "instructions": "Translate approved positioning into a coordinated content plan with consistent claims and measurable goals. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Campaign brief, channel drafts and measurement plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "marketing-role-2",
    "kind": "role",
    "name": "Claims brand reviewer",
    "description": "Claim-evidence matrix, editorial findings and publishing checklist.",
    "content": {
      "category": "marketing",
      "instructions": "Check evidence for claims, brand consistency and consent requirements; human owner approves publication and spending. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Claim-evidence matrix, editorial findings and publishing checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "marketing-team",
    "kind": "team",
    "name": "Marketing evidence team",
    "description": "Specialized analysis, planning and independent review for marketing.",
    "role_keys": [
      "marketing-role-0",
      "marketing-role-1",
      "marketing-role-2"
    ],
    "content": {
      "category": "marketing",
      "workflow": "Audience insight researcher → Campaign content planner → Claims brand reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "marketing-project-0",
    "kind": "project",
    "name": "Campaign planning kit",
    "description": "Plan a measurable campaign for a defined audience and offer.",
    "team_key": "marketing-team",
    "content": {
      "category": "marketing",
      "objective": "Plan a measurable campaign for a defined audience and offer.",
      "stages": "Clarify objective → inspect audience evidence → design message and channels → allocate budget → review",
      "deliverables": "Campaign brief; content calendar; channel drafts; attribution assumptions",
      "acceptance_criteria": "Every claim has support; tracking respects approved consent practices; spend and publication require owner approval; success measures are set in advance."
    }
  },
  {
    "key": "marketing-project-1",
    "kind": "project",
    "name": "Content refresh audit",
    "description": "Prioritize existing content updates based on usefulness, accuracy and business relevance.",
    "team_key": "marketing-team",
    "content": {
      "category": "marketing",
      "objective": "Prioritize existing content updates based on usefulness, accuracy and business relevance.",
      "stages": "Inventory content → inspect quality and performance → identify stale claims → draft priorities → editorial review",
      "deliverables": "Content inventory; keep/update/retire queue; revised outlines; measurement baseline",
      "acceptance_criteria": "Performance data has a date range; factual edits cite sources; redirects and ownership are considered; editor approves publication."
    }
  },
  {
    "key": "marketing-project-2",
    "kind": "project",
    "name": "Customer case study draft",
    "description": "Turn approved customer evidence into an accurate, permission-ready case study.",
    "team_key": "marketing-team",
    "content": {
      "category": "marketing",
      "objective": "Turn approved customer evidence into an accurate, permission-ready case study.",
      "stages": "Collect source material → verify outcomes → structure narrative → draft → obtain approval",
      "deliverables": "Interview guide; evidence matrix; case-study draft; quote and permission checklist",
      "acceptance_criteria": "Quotes are exact or clearly paraphrased; metrics include context; customer identity and publication permission are verified before release."
    }
  },
  {
    "key": "operations-role-0",
    "kind": "role",
    "name": "Operations baseline analyst",
    "description": "Process baseline, exception map and data-quality notes.",
    "content": {
      "category": "operations",
      "instructions": "Map task sequence, queues and exceptions using approved records; distinguish observed cycle times from estimates. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Process baseline, exception map and data-quality notes",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "operations-role-1",
    "kind": "role",
    "name": "Process improvement designer",
    "description": "Operating procedure draft, pilot plan and owner checklist.",
    "content": {
      "category": "operations",
      "instructions": "Design practical controls, standard work and a reversible pilot around the bottleneck. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Operating procedure draft, pilot plan and owner checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "operations-role-2",
    "kind": "role",
    "name": "Operational assurance reviewer",
    "description": "Walkthrough findings, control checks and acceptance record.",
    "content": {
      "category": "operations",
      "instructions": "Test procedure clarity, exception recovery and benefit assumptions with process owners. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Walkthrough findings, control checks and acceptance record",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "operations-team",
    "kind": "team",
    "name": "Operational excellence team",
    "description": "Specialized analysis, planning and independent review for operations.",
    "role_keys": [
      "operations-role-0",
      "operations-role-1",
      "operations-role-2"
    ],
    "content": {
      "category": "operations",
      "workflow": "Operations baseline analyst → Process improvement designer → Operational assurance reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "operations-project-0",
    "kind": "project",
    "name": "Standard operating procedure",
    "description": "Document a repeatable process including ownership, exceptions and recovery.",
    "team_key": "operations-team",
    "content": {
      "category": "operations",
      "objective": "Document a repeatable process including ownership, exceptions and recovery.",
      "stages": "Observe process → map decisions → draft procedure → walk through exceptions → approve",
      "deliverables": "SOP; responsibility table; exception checklist; training example",
      "acceptance_criteria": "A new operator can follow the procedure in a supervised walkthrough; escalation owners are named; safety-critical steps receive qualified review."
    }
  },
  {
    "key": "operations-project-1",
    "kind": "project",
    "name": "Supplier performance review",
    "description": "Assess supplier delivery using an agreed period, contractual measures and recorded exceptions.",
    "team_key": "operations-team",
    "content": {
      "category": "operations",
      "objective": "Assess supplier delivery using an agreed period, contractual measures and recorded exceptions.",
      "stages": "Confirm scorecard → reconcile deliveries → classify exceptions → draft actions → procurement review",
      "deliverables": "Supplier scorecard; exception register; meeting brief; improvement actions",
      "acceptance_criteria": "Metrics tie to source records; contractual terms are checked by an authorized owner; disputed events are flagged; no supplier messages are sent automatically."
    }
  },
  {
    "key": "operations-project-2",
    "kind": "project",
    "name": "Service capacity improvement",
    "description": "Reduce queue delays by comparing demand, capacity and scheduling alternatives.",
    "team_key": "operations-team",
    "content": {
      "category": "operations",
      "objective": "Reduce queue delays by comparing demand, capacity and scheduling alternatives.",
      "stages": "Measure arrivals and service → locate bottleneck → model options → design pilot → review",
      "deliverables": "Queue baseline; capacity scenarios; pilot schedule; service-level measurement plan",
      "acceptance_criteria": "Peak and average demand are distinguished; model assumptions are explicit; service quality is monitored alongside speed; process owner approves changes."
    }
  },
  {
    "key": "security-role-0",
    "kind": "role",
    "name": "Security scope analyst",
    "description": "Asset map, threat assumptions and authorized-scope statement.",
    "content": {
      "category": "security",
      "instructions": "Inventory approved assets, data flows and trust boundaries within an explicitly authorized scope. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Asset map, threat assumptions and authorized-scope statement",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "security-role-1",
    "kind": "role",
    "name": "Control assessment specialist",
    "description": "Control evidence matrix and prioritized remediation plan.",
    "content": {
      "category": "security",
      "instructions": "Evaluate defensive controls against stated threats using read-only evidence or an approved test environment. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Control evidence matrix and prioritized remediation plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "security-role-2",
    "kind": "role",
    "name": "Security validation reviewer",
    "description": "Validated risk register, reproduction evidence and owner review gate.",
    "content": {
      "category": "security",
      "instructions": "Independently validate findings and false positives; require authorization before intrusive tests or production changes. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Validated risk register, reproduction evidence and owner review gate",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "security-team",
    "kind": "team",
    "name": "Security assurance team",
    "description": "Specialized analysis, planning and independent review for security.",
    "role_keys": [
      "security-role-0",
      "security-role-1",
      "security-role-2"
    ],
    "content": {
      "category": "security",
      "workflow": "Security scope analyst → Control assessment specialist → Security validation reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "security-project-0",
    "kind": "project",
    "name": "Threat modelling workshop",
    "description": "Identify threats and controls for a bounded application or service.",
    "team_key": "security-team",
    "content": {
      "category": "security",
      "objective": "Identify threats and controls for a bounded application or service.",
      "stages": "Define scope → map data and trust boundaries → enumerate threats → evaluate controls → review",
      "deliverables": "Data-flow diagram; threat register; mitigations; residual-risk decisions",
      "acceptance_criteria": "Threats reference actual boundaries; assumptions and unknowns are explicit; owners accept residual risk; no exploit is executed outside authorized scope."
    }
  },
  {
    "key": "security-project-1",
    "kind": "project",
    "name": "Access review preparation",
    "description": "Prepare an auditable review of access assignments for authorized system owners.",
    "team_key": "security-team",
    "content": {
      "category": "security",
      "objective": "Prepare an auditable review of access assignments for authorized system owners.",
      "stages": "Collect approved exports → normalize identities → flag anomalies → assign reviewers → reconcile decisions",
      "deliverables": "Entitlement inventory; privileged-access flags; reviewer worksheet; decision log",
      "acceptance_criteria": "Identity joins are checked; stale accounts are evidence-backed; removals require owner approval; sensitive exports remain in approved storage."
    }
  },
  {
    "key": "security-project-2",
    "kind": "project",
    "name": "Security incident tabletop",
    "description": "Design a safe discussion exercise to test incident response coordination.",
    "team_key": "security-team",
    "content": {
      "category": "security",
      "objective": "Design a safe discussion exercise to test incident response coordination.",
      "stages": "Choose scenario → define injects → map decision points → facilitate walkthrough → document gaps",
      "deliverables": "Scenario pack; facilitator script; participant roles; response gaps; action register",
      "acceptance_criteria": "Exercise is clearly labelled simulated; no live attack or alert is triggered; escalation contacts are verified; actions have accountable owners."
    }
  },
  {
    "key": "legal-compliance-role-0",
    "kind": "role",
    "name": "Compliance evidence analyst",
    "description": "Obligation register, source index and evidence gaps.",
    "content": {
      "category": "legal-compliance",
      "instructions": "Index approved policies and obligations with jurisdiction, effective dates and source references; flag uncertainty for counsel. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Obligation register, source index and evidence gaps",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "legal-compliance-role-1",
    "kind": "role",
    "name": "Policy process drafter",
    "description": "Policy draft, responsibility map and implementation checklist.",
    "content": {
      "category": "legal-compliance",
      "instructions": "Draft operational policy language and evidence-collection workflows under an approved legal brief. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Policy draft, responsibility map and implementation checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "legal-compliance-role-2",
    "kind": "role",
    "name": "Qualified review coordinator",
    "description": "Review packet, unresolved interpretations and counsel approval checklist.",
    "content": {
      "category": "legal-compliance",
      "instructions": "Check traceability and prepare questions for qualified legal or compliance review. Do not assert legal sufficiency or submit official filings. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Review packet, unresolved interpretations and counsel approval checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "legal-compliance-team",
    "kind": "team",
    "name": "Compliance evidence team",
    "description": "Specialized analysis, planning and independent review for legal and compliance.",
    "role_keys": [
      "legal-compliance-role-0",
      "legal-compliance-role-1",
      "legal-compliance-role-2"
    ],
    "content": {
      "category": "legal-compliance",
      "workflow": "Compliance evidence analyst → Policy process drafter → Qualified review coordinator → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls. Qualified domain professionals must approve conclusions before reliance; the team does not provide autonomous regulated advice or decisions."
    }
  },
  {
    "key": "legal-compliance-project-0",
    "kind": "project",
    "name": "Policy gap assessment",
    "description": "Compare an internal policy against a supplied, authoritative requirements set.",
    "team_key": "legal-compliance-team",
    "content": {
      "category": "legal-compliance",
      "objective": "Compare an internal policy against a supplied, authoritative requirements set.",
      "stages": "Confirm jurisdiction and scope → map clauses → inspect evidence → draft gaps → qualified review",
      "deliverables": "Requirement-to-policy matrix; gap register; policy edits; counsel questions",
      "acceptance_criteria": "Requirements carry source and date; uncertain interpretation is flagged; qualified counsel or compliance owner approves conclusions before reliance."
    }
  },
  {
    "key": "legal-compliance-project-1",
    "kind": "project",
    "name": "Contract review preparation",
    "description": "Organize a contract and business context for review by qualified counsel.",
    "team_key": "legal-compliance-team",
    "content": {
      "category": "legal-compliance",
      "objective": "Organize a contract and business context for review by qualified counsel.",
      "stages": "Confirm agreement version → extract obligations → map commercial concerns → prepare questions → legal review",
      "deliverables": "Clause index; obligation calendar; deviation summary; questions for counsel",
      "acceptance_criteria": "Citations resolve to exact clauses; omissions and ambiguity are explicit; no legal advice or approval is implied; qualified counsel reviews before signature."
    }
  },
  {
    "key": "legal-compliance-project-2",
    "kind": "project",
    "name": "Audit evidence readiness",
    "description": "Prepare a traceable evidence pack for a defined internal or external audit.",
    "team_key": "legal-compliance-team",
    "content": {
      "category": "legal-compliance",
      "objective": "Prepare a traceable evidence pack for a defined internal or external audit.",
      "stages": "Confirm audit scope → map requests → collect approved evidence → check freshness → owner review",
      "deliverables": "Evidence index; request tracker; control-owner map; missing-evidence register",
      "acceptance_criteria": "Artifacts have owner, period and source; sensitive access is limited; no evidence is fabricated; authorized compliance owner approves submission."
    }
  },
  {
    "key": "healthcare-role-0",
    "kind": "role",
    "name": "Health workflow analyst",
    "description": "Workflow map, data boundary and administrative bottleneck evidence.",
    "content": {
      "category": "healthcare",
      "instructions": "Map non-diagnostic administrative workflows using de-identified, approved inputs; flag points requiring clinical judgment. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Workflow map, data boundary and administrative bottleneck evidence",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "healthcare-role-1",
    "kind": "role",
    "name": "Health service planner",
    "description": "Process proposal, escalation map and pilot measurement plan.",
    "content": {
      "category": "healthcare",
      "instructions": "Draft administrative process improvements that preserve escalation to qualified clinicians and patient safety responsibilities. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Process proposal, escalation map and pilot measurement plan",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "healthcare-role-2",
    "kind": "role",
    "name": "Clinical governance coordinator",
    "description": "Safety questions, privacy checks and qualified-human approval record.",
    "content": {
      "category": "healthcare",
      "instructions": "Prepare a review packet for qualified clinical, privacy and operational owners. Do not diagnose, recommend treatment or automate care decisions. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Safety questions, privacy checks and qualified-human approval record",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "healthcare-team",
    "kind": "team",
    "name": "Health operations review team",
    "description": "Specialized analysis, planning and independent review for healthcare.",
    "role_keys": [
      "healthcare-role-0",
      "healthcare-role-1",
      "healthcare-role-2"
    ],
    "content": {
      "category": "healthcare",
      "workflow": "Health workflow analyst → Health service planner → Clinical governance coordinator → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls. Qualified domain professionals must approve conclusions before reliance; the team does not provide autonomous regulated advice or decisions."
    }
  },
  {
    "key": "healthcare-project-0",
    "kind": "project",
    "name": "Clinic administration workflow",
    "description": "Improve a bounded scheduling or referral administration process without changing clinical triage.",
    "team_key": "healthcare-team",
    "content": {
      "category": "healthcare",
      "objective": "Improve a bounded scheduling or referral administration process without changing clinical triage.",
      "stages": "Map administrative steps → assess delays → draft process → check escalation → qualified review",
      "deliverables": "Workflow map; scheduling checklist; escalation rules; pilot metrics",
      "acceptance_criteria": "No clinical priority is inferred by the model; patient identifiers are removed from examples; qualified clinical and operational owners approve changes."
    }
  },
  {
    "key": "healthcare-project-1",
    "kind": "project",
    "name": "Patient information readability",
    "description": "Improve clarity of supplied patient information while preserving clinician-approved medical meaning.",
    "team_key": "healthcare-team",
    "content": {
      "category": "healthcare",
      "objective": "Improve clarity of supplied patient information while preserving clinician-approved medical meaning.",
      "stages": "Confirm approved source → assess readability → draft plain-language version → compare meaning → clinical review",
      "deliverables": "Annotated draft; terminology glossary; meaning-preservation checklist; review questions",
      "acceptance_criteria": "No medical claim is added or removed without clinician approval; accessibility is checked; qualified clinician approves the final text before distribution."
    }
  },
  {
    "key": "healthcare-project-2",
    "kind": "project",
    "name": "Healthcare service quality review",
    "description": "Organize de-identified administrative service feedback into actionable quality improvements.",
    "team_key": "healthcare-team",
    "content": {
      "category": "healthcare",
      "objective": "Organize de-identified administrative service feedback into actionable quality improvements.",
      "stages": "Define service scope → inspect de-identified feedback → classify themes → prioritize actions → governance review",
      "deliverables": "Feedback evidence table; service themes; improvement plan; measurement schedule",
      "acceptance_criteria": "Small-group reidentification risk is reviewed; anecdotes are not treated as clinical evidence; safety concerns route to qualified staff; governance owner approves actions."
    }
  },
  {
    "key": "nonprofit-role-0",
    "kind": "role",
    "name": "Community needs researcher",
    "description": "Needs assessment, stakeholder map and evidence gaps.",
    "content": {
      "category": "nonprofit",
      "instructions": "Map needs from consented community input and inspected evidence; preserve minority perspectives and uncertainty. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Needs assessment, stakeholder map and evidence gaps",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "nonprofit-role-1",
    "kind": "role",
    "name": "Programme planning specialist",
    "description": "Programme plan, resource budget and outcome framework.",
    "content": {
      "category": "nonprofit",
      "instructions": "Connect activities, resources and outcomes through an explicit theory of change with feasible measures. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Programme plan, resource budget and outcome framework",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "nonprofit-role-2",
    "kind": "role",
    "name": "Impact accountability reviewer",
    "description": "Impact review, reporting limitations and stakeholder approval checklist.",
    "content": {
      "category": "nonprofit",
      "instructions": "Challenge attribution, inclusion and reporting claims; programme owner approves commitments and public statements. Record sources, assumptions and unresolved questions. Work only within the approved project scope.",
      "expected_output": "Impact review, reporting limitations and stakeholder approval checklist",
      "allowed_tools": "Approved documents and read-only data inspection; local drafting and analysis. External actions and additional access require explicit human authorization."
    }
  },
  {
    "key": "nonprofit-team",
    "kind": "team",
    "name": "Impact delivery team",
    "description": "Specialized analysis, planning and independent review for nonprofit and public good.",
    "role_keys": [
      "nonprofit-role-0",
      "nonprofit-role-1",
      "nonprofit-role-2"
    ],
    "content": {
      "category": "nonprofit",
      "workflow": "Community needs researcher → Programme planning specialist → Impact accountability reviewer → human owner approval",
      "review_policy": "The third role reviews independently of the author. A named human owner approves the result before publication, spending, deployment or operational change. Template instructions are review requirements, not enforced runtime controls."
    }
  },
  {
    "key": "nonprofit-project-0",
    "kind": "project",
    "name": "Grant proposal preparation",
    "description": "Prepare a funder-aligned proposal grounded in a feasible programme and verified evidence.",
    "team_key": "nonprofit-team",
    "content": {
      "category": "nonprofit",
      "objective": "Prepare a funder-aligned proposal grounded in a feasible programme and verified evidence.",
      "stages": "Read funding criteria → map programme fit → draft outcomes and budget → check eligibility → owner review",
      "deliverables": "Compliance checklist; proposal draft; budget narrative; evidence and attachments index",
      "acceptance_criteria": "Eligibility and deadlines are verified from funder materials; outcomes are not exaggerated; authorized owner approves submission; no application is sent automatically."
    }
  },
  {
    "key": "nonprofit-project-1",
    "kind": "project",
    "name": "Programme impact framework",
    "description": "Define how a programme will measure outcomes without overstating causality.",
    "team_key": "nonprofit-team",
    "content": {
      "category": "nonprofit",
      "objective": "Define how a programme will measure outcomes without overstating causality.",
      "stages": "Map theory of change → select indicators → define collection → assess burden → stakeholder review",
      "deliverables": "Theory of change; indicator dictionary; collection plan; limitations and learning agenda",
      "acceptance_criteria": "Indicators distinguish outputs from outcomes; consent and data minimization are defined; causal claims match evaluation design; programme owner approves."
    }
  },
  {
    "key": "nonprofit-project-2",
    "kind": "project",
    "name": "Volunteer onboarding programme",
    "description": "Create an inclusive onboarding process with clear responsibilities and safeguarding escalation.",
    "team_key": "nonprofit-team",
    "content": {
      "category": "nonprofit",
      "objective": "Create an inclusive onboarding process with clear responsibilities and safeguarding escalation.",
      "stages": "Map volunteer roles → identify prerequisites → draft training → test scenarios → coordinator review",
      "deliverables": "Role brief; onboarding checklist; training outline; safeguarding contact map",
      "acceptance_criteria": "Responsibilities and boundaries are explicit; required checks are confirmed by the coordinator; scenarios test escalation; accessibility needs are addressed."
    }
  }
]
$templates$::jsonb) LOOP
        -- schema.sql consumers may adopt the migration ledger afterwards.
        -- Reuse existing catalog rows; never replace a customized version or snapshot.
        SELECT id INTO created_id FROM public.pm_templates
        WHERE content->>'catalog_key' = entry->>'key' AND kind = entry->>'kind'
        ORDER BY id LIMIT 1;
        IF FOUND THEN
            ids := ids || jsonb_build_object(entry->>'key', created_id);
            CONTINUE;
        END IF;
        body := entry->'content' || jsonb_build_object('catalog_key', entry->>'key');
        IF entry ? 'role_keys' THEN
            role_refs := '[]'::jsonb;
            FOR child_key IN SELECT jsonb_array_elements_text(entry->'role_keys') LOOP
                role_refs := role_refs || jsonb_build_array(jsonb_build_object('id', (ids->>child_key)::bigint, 'version', 1));
            END LOOP;
            body := body || jsonb_build_object('role_templates', role_refs);
        END IF;
        IF entry ? 'team_key' THEN
            body := body || jsonb_build_object('team_template', jsonb_build_object('id', (ids->>(entry->>'team_key'))::bigint, 'version', 1));
        END IF;
        INSERT INTO public.pm_templates(kind, name, description, content)
        VALUES(entry->>'kind', entry->>'name', entry->>'description', body)
        RETURNING id INTO created_id;
        ids := ids || jsonb_build_object(entry->>'key', created_id);
        INSERT INTO public.pm_template_versions(template_id, version, snapshot)
        SELECT id, version, to_jsonb(t) FROM public.pm_templates t WHERE id = created_id;
    END LOOP;
END $catalog$;

-- Source-version checkpoints are committed with derived records, including valid empty results.
CREATE TABLE IF NOT EXISTS public.processing_checkpoints (
    stage text NOT NULL CHECK (stage IN ('extract', 'entities')),
    conversation_id bigint NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    fingerprint text NOT NULL,
    model text NOT NULL,
    elapsed_seconds double precision NOT NULL CHECK (elapsed_seconds >= 0),
    output_count integer NOT NULL CHECK (output_count >= 0),
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stage, conversation_id)
);
