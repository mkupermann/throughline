-- =============================================================================
-- Common Queries for Claude Memory
-- =============================================================================
-- A collection of ready-to-run SQL queries for frequent use-cases.
-- All queries target the public schema of the claude_memory database.
--
-- Run individual queries in psql:
--   psql -d claude_memory -c "<query>"
-- Or paste into the built-in SQL console in the Streamlit GUI.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. All decisions for a specific project
--    Useful when starting work on a project and needing to recall what was
--    previously decided — avoids re-litigating architecture choices.
-- -----------------------------------------------------------------------------
SELECT
    mc.id,
    mc.created_at::date          AS decided_on,
    mc.content,
    mc.confidence,
    mc.tags,
    c.summary                    AS source_conversation
FROM public.memory_chunks mc
LEFT JOIN public.conversations c ON c.id = mc.source_id AND mc.source_type = 'conversation'
WHERE mc.category   = 'decision'
  AND mc.project_name = :project_name   -- e.g. 'fintech-api'
  AND mc.status     = 'active'
ORDER BY mc.created_at DESC;


-- -----------------------------------------------------------------------------
-- 2. Top-10 most-used skills
--    Identifies which skills deliver the most value and which are underused.
-- -----------------------------------------------------------------------------
SELECT
    name,
    version,
    use_count,
    last_used::date              AS last_used,
    description
FROM public.skills
ORDER BY use_count DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- 3. Conversation count grouped by calendar month
--    Tracks usage trends over time — good for retrospectives or
--    "how much am I actually using this?" sanity checks.
-- -----------------------------------------------------------------------------
SELECT
    DATE_TRUNC('month', started_at)::date  AS month,
    COUNT(*)                               AS conversations,
    SUM(message_count)                     AS total_messages,
    ROUND(SUM(cost_usd)::numeric, 4)       AS total_cost_usd,
    ROUND(AVG(
        EXTRACT(EPOCH FROM (ended_at - started_at)) / 60
    )::numeric, 1)                         AS avg_duration_min
FROM public.conversations
WHERE ended_at IS NOT NULL
GROUP BY 1
ORDER BY 1 DESC;


-- -----------------------------------------------------------------------------
-- 4. Memory chunks with the lowest confidence (candidates for review)
--    Low-confidence chunks may be outdated, ambiguous, or incorrectly
--    extracted. Review regularly and either update or mark superseded.
-- -----------------------------------------------------------------------------
SELECT
    mc.id,
    mc.category,
    mc.project_name,
    mc.confidence,
    mc.created_at::date          AS created,
    mc.last_accessed::date       AS last_accessed,
    LEFT(mc.content, 120)        AS content_preview
FROM public.memory_chunks mc
WHERE mc.status = 'active'
  AND mc.confidence < 0.80
ORDER BY mc.confidence ASC, mc.created_at ASC
LIMIT 25;


-- -----------------------------------------------------------------------------
-- 5. Sessions that ran longer than N minutes
--    Highlights deep-work sessions worth revisiting for learning material
--    or that may have produced important decisions worth extracting.
-- -----------------------------------------------------------------------------
\set min_duration_minutes 60

SELECT
    c.id,
    c.project_name,
    c.git_branch,
    c.started_at::date           AS date,
    ROUND(
        EXTRACT(EPOCH FROM (c.ended_at - c.started_at)) / 60
    )::int                       AS duration_min,
    c.message_count,
    ROUND(c.cost_usd::numeric, 4) AS cost_usd,
    c.summary
FROM public.conversations c
WHERE c.ended_at IS NOT NULL
  AND (c.ended_at - c.started_at) > (:'min_duration_minutes' || ' minutes')::interval
ORDER BY (c.ended_at - c.started_at) DESC;


-- -----------------------------------------------------------------------------
-- 6. Entity with the most relationships (most connected node in the graph)
--    Useful for understanding which person, technology, or concept is most
--    central to your knowledge graph.
-- -----------------------------------------------------------------------------
SELECT
    e.id,
    e.entity_type,
    e.name,
    e.project_name,
    e.mention_count,
    COUNT(r.id)                  AS relationship_count
FROM public.entities e
JOIN public.relationships r
    ON r.from_entity = e.id OR r.to_entity = e.id
GROUP BY e.id, e.entity_type, e.name, e.project_name, e.mention_count
ORDER BY relationship_count DESC, e.mention_count DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- 7. Stale memory chunks (not accessed in > 90 days and older than 30 days)
--    Old, unaccessed chunks may be outdated facts. Review before the
--    information influences future decisions.
-- -----------------------------------------------------------------------------
\set stale_days 90

SELECT
    mc.id,
    mc.category,
    mc.project_name,
    mc.confidence,
    mc.created_at::date          AS created,
    mc.last_accessed::date       AS last_accessed,
    (NOW() - mc.last_accessed)   AS age_since_last_access,
    LEFT(mc.content, 120)        AS content_preview
FROM public.memory_chunks mc
WHERE mc.status       = 'active'
  AND mc.created_at   < NOW() - INTERVAL '30 days'
  AND (
      mc.last_accessed IS NULL
      OR mc.last_accessed < NOW() - (:'stale_days' || ' days')::interval
  )
ORDER BY mc.last_accessed ASC NULLS FIRST;


-- -----------------------------------------------------------------------------
-- 8. Memory coverage per project
--    Shows how much structured memory exists for each project, broken down
--    by category. Gaps (zero counts in a category) suggest areas to document.
-- -----------------------------------------------------------------------------
SELECT
    mc.project_name,
    COUNT(*)                                                 AS total_chunks,
    COUNT(*) FILTER (WHERE mc.category = 'decision')         AS decisions,
    COUNT(*) FILTER (WHERE mc.category = 'pattern')          AS patterns,
    COUNT(*) FILTER (WHERE mc.category = 'insight')          AS insights,
    COUNT(*) FILTER (WHERE mc.category = 'preference')       AS preferences,
    COUNT(*) FILTER (WHERE mc.category = 'contact')          AS contacts,
    COUNT(*) FILTER (WHERE mc.category = 'error_solution')   AS error_solutions,
    COUNT(*) FILTER (WHERE mc.category = 'project_context')  AS project_contexts,
    COUNT(*) FILTER (WHERE mc.category = 'workflow')         AS workflows,
    ROUND(AVG(mc.confidence)::numeric, 2)                    AS avg_confidence
FROM public.memory_chunks mc
WHERE mc.status = 'active'
GROUP BY mc.project_name
ORDER BY total_chunks DESC;


-- -----------------------------------------------------------------------------
-- 9. Full-text search across all memory chunks
--    Quick recall query — replace the search term with any keyword.
--    The GIN index on content makes this fast even at tens of thousands of rows.
-- -----------------------------------------------------------------------------
\set search_term 'idempotency'

SELECT
    mc.id,
    mc.category,
    mc.project_name,
    mc.confidence,
    mc.created_at::date          AS created,
    mc.content
FROM public.memory_chunks mc
WHERE mc.status = 'active'
  AND mc.content ILIKE ('%' || :'search_term' || '%')
ORDER BY mc.confidence DESC, mc.created_at DESC
LIMIT 20;


-- -----------------------------------------------------------------------------
-- 10. Error solutions grouped by project (quick debugging reference)
--     When hitting an error, check here before spending time debugging —
--     past solutions are often directly applicable.
-- -----------------------------------------------------------------------------
SELECT
    mc.project_name,
    mc.created_at::date          AS logged,
    mc.tags,
    mc.content
FROM public.memory_chunks mc
WHERE mc.category = 'error_solution'
  AND mc.status   = 'active'
ORDER BY mc.project_name, mc.created_at DESC;


-- -----------------------------------------------------------------------------
-- 11. Conversation messages containing tool calls (agent activity log)
--     Shows where Claude invoked tools — useful for auditing automated
--     actions or understanding what happened in a long session.
-- -----------------------------------------------------------------------------
SELECT
    c.project_name,
    c.started_at::date           AS session_date,
    m.tool_name,
    COUNT(*)                     AS invocations,
    ROUND(AVG(m.duration_ms))    AS avg_duration_ms
FROM public.messages m
JOIN public.conversations c ON c.id = m.conversation_id
WHERE m.tool_name IS NOT NULL
GROUP BY c.project_name, c.started_at::date, m.tool_name
ORDER BY c.started_at::date DESC, invocations DESC;


-- -----------------------------------------------------------------------------
-- 12. Projects sorted by total conversation time (most-worked-on first)
--     Gives a quick picture of where time has been invested across projects.
-- -----------------------------------------------------------------------------
SELECT
    c.project_name,
    COUNT(DISTINCT c.id)                               AS sessions,
    SUM(c.message_count)                               AS total_messages,
    ROUND(SUM(
        EXTRACT(EPOCH FROM (c.ended_at - c.started_at)) / 60
    )::numeric)                                        AS total_minutes,
    ROUND(SUM(c.cost_usd)::numeric, 2)                 AS total_cost_usd
FROM public.conversations c
WHERE c.ended_at IS NOT NULL
GROUP BY c.project_name
ORDER BY total_minutes DESC;
