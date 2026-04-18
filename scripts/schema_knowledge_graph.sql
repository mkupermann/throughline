-- Knowledge Graph Schema-Erweiterung für claude_memory
-- Tabellen: entities, relationships, entity_mentions

CREATE TABLE IF NOT EXISTS entities (
    id              BIGSERIAL PRIMARY KEY,
    entity_type     TEXT NOT NULL,  -- person | project | technology | decision | concept | organization
    name            TEXT NOT NULL,
    canonical_name  TEXT NOT NULL,  -- normalisierter Name (lowercase, ohne Akzente)
    attributes      JSONB DEFAULT '{}',
    first_seen      TIMESTAMPTZ DEFAULT now(),
    last_seen       TIMESTAMPTZ DEFAULT now(),
    mention_count   INT DEFAULT 1,
    project_name    TEXT,
    confidence      NUMERIC(3,2) DEFAULT 0.8,
    metadata        JSONB DEFAULT '{}',
    UNIQUE(entity_type, canonical_name, project_name)
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_project ON entities(project_name);
CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_name);

CREATE TABLE IF NOT EXISTS relationships (
    id              BIGSERIAL PRIMARY KEY,
    from_entity     BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    to_entity       BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    relation_type   TEXT NOT NULL,
    valid_from      TIMESTAMPTZ DEFAULT now(),
    valid_until     TIMESTAMPTZ,
    confidence      NUMERIC(3,2) DEFAULT 0.8,
    source_type     TEXT,
    source_id       BIGINT,
    attributes      JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_entity);
CREATE INDEX IF NOT EXISTS idx_rel_to ON relationships(to_entity);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relation_type);

CREATE TABLE IF NOT EXISTS entity_mentions (
    id              BIGSERIAL PRIMARY KEY,
    entity_id       BIGINT REFERENCES entities(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL,
    source_id       BIGINT NOT NULL,
    context_snippet TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_mentions_source ON entity_mentions(source_type, source_id);
