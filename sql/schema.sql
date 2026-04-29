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
    project_path text,
    project_name text GENERATED ALWAYS AS (
CASE
    WHEN (project_path IS NULL) THEN 'unknown'::text
    ELSE split_part(project_path, '/'::text, '-1'::integer)
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
    updated_at timestamp with time zone DEFAULT now()
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
    source_type text NOT NULL,
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
    source_type text NOT NULL,
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
-- PostgreSQL database dump complete
--

