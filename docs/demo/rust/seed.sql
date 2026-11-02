-- Entirely fictional documentation fixture; not an evaluation or model output.
-- Refuse any other database, then replace only this fixture's owned tables.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'throughline_readme_demo' THEN
    RAISE EXCEPTION 'This fixture requires throughline_readme_demo';
  END IF;
END $$;
TRUNCATE messages, conversations, memory_chunks, projects, favorites RESTART IDENTITY CASCADE;
TRUNCATE secrets, secret_occurrences, secret_reveals, secret_scan_state RESTART IDENTITY CASCADE;

INSERT INTO projects (name, description) VALUES
('Atlas-demo', 'Fictional retrieval study: preserve decisions and investigate long-document failures.'),
('Harbor-demo', 'Fictional customer portal: accessible navigation and a reliable release handoff.'),
('Orchard-demo', 'Fictional data pipeline: contracts, incremental loads and recovery procedures.');

INSERT INTO conversations (session_id, project_path, source_tool, model, started_at,
 ended_at, message_count, summary, metadata)
SELECT ('d0000000-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid,
 '/fictional/' || (ARRAY['Atlas-demo','Harbor-demo','Orchard-demo'])[1 + (n-1)%3],
 (ARRAY['claude_code','codex','cursor','vibe','aider','opencode'])[1 + (n-1)%6],
 'fictional-demo-model',
 date_trunc('day', now()) - ((18-n)::text || ' days')::interval + interval '9 hours',
 date_trunc('day', now()) - ((18-n)::text || ' days')::interval + interval '9 hours 24 minutes',
 4,
 (ARRAY['Retrieval baseline and evidence review','Accessible navigation review','Incremental load recovery',
 'Long-document counterexample','Release checklist and handoff','Data contract decision'])[1 + (n-1)%6],
 '{"fixture":"throughline-readme-v1","fictional":true}'::jsonb
FROM generate_series(1,18) AS n;

INSERT INTO messages (conversation_id, uuid, role, content, tool_name, tool_calls, created_at)
SELECT c.id, ('e0000000-0000-4000-8000-' || lpad((c.id*10+m)::text,12,'0'))::uuid,
 (ARRAY['user','assistant','tool_result','assistant'])[m]::message_role,
 CASE m
 WHEN 1 THEN 'Review the ' || c.project_name || ' decision and identify the next check. Keep evidence separate from assumptions.'
 WHEN 2 THEN CASE c.project_name
   WHEN 'Atlas-demo' THEN 'Decision: keep lexical retrieval as the baseline. The long-document counterexample needs a separate evaluation before changing the design.'
   WHEN 'Harbor-demo' THEN 'Decision: retain keyboard navigation and visible focus. Recheck the mobile drawer before the release handoff.'
   ELSE 'Decision: validate the data contract before each incremental load. The next check is recovery from an interrupted batch.' END
   || ' [Tool: Read] path=/fictional/' || c.project_name || '/notes/decision.md'
 WHEN 3 THEN 'FICTIONAL TOOL RESULT: opened /fictional/' || c.project_name || '/notes/decision.md. This is demonstration content, not a command executed by a real agent.'
 ELSE 'Handoff: the decision is recorded; the follow-up check is still open. Read the source conversation before treating this note as current project state.' END,
 CASE WHEN m=3 THEN 'Read' ELSE NULL END,
 CASE WHEN m=2 THEN jsonb_build_array(jsonb_build_object('name','Read','input',jsonb_build_object('path','/fictional/'||c.project_name||'/notes/decision.md'))) ELSE NULL END,
 c.started_at + (m::text || ' minutes')::interval
FROM conversations c CROSS JOIN generate_series(1,4) AS m;

INSERT INTO memory_chunks (source_type, source_id, content, category, tags, project_name, confidence, created_at, source_at)
SELECT CASE WHEN c.id%3=0 THEN 'manual' ELSE 'conversation' END,
 CASE WHEN c.id%3=0 THEN NULL ELSE c.id END,
 CASE c.project_name
   WHEN 'Atlas-demo' THEN 'Decision: retain lexical retrieval as the baseline. Next: reproduce the long-document counterexample with fixed inputs.'
   WHEN 'Harbor-demo' THEN 'Decision: preserve keyboard navigation and visible focus. Next: verify the mobile drawer and document the release handoff.'
   ELSE 'Decision: enforce the data contract before loading. Next: rehearse interrupted-batch recovery on disposable data.' END,
 CASE WHEN c.id%2=0 THEN 'decision' ELSE 'project_context' END,
 ARRAY['agent-memory','fictional-demo'], c.project_name, 0.8,
 c.started_at + interval '25 minutes',
 CASE WHEN c.id%3=0 THEN c.started_at + interval '25 minutes' - interval '2 days' ELSE c.ended_at END
FROM conversations c WHERE c.id > 6;

-- Fictional secrets for the Secrets view. Each value is put together here at
-- run time, so no secret-shaped string is in the repository, and none is in
-- a message: the demo's message counts stay as they are.
WITH v(kind, val, conv) AS (VALUES
  ('github_token', 'gh' || 'p_' || repeat('Fict10nalDemo', 3), 2),
  ('openai_key', 'sk' || '-proj-' || repeat('Fict10nalDemoKey', 3), 5),
  ('aws_key_id', 'AK' || 'IA' || 'FICTIONALDEMOK3Y', 9),
  ('github_token', 'gh' || 'p_' || repeat('Demo0nlyFict', 3), 16)
)
INSERT INTO secrets (kind, value, fingerprint, masked, first_seen_at, last_seen_at,
  occurrences, source_type, message_id, conversation_id, project, tool)
SELECT v.kind, v.val, encode(sha256(convert_to(v.val, 'UTF8')), 'hex'),
  left(v.val, 4) || '•••' || right(v.val, 4), m.created_at, m.created_at,
  1, 'message', m.id, c.id, c.project_name, c.source_tool
FROM v
JOIN conversations c ON c.id = v.conv
JOIN LATERAL (SELECT id, created_at FROM messages WHERE conversation_id = c.id
              ORDER BY id LIMIT 1) m ON TRUE;
INSERT INTO secret_occurrences (secret_id, source_type, ref, seen_at)
SELECT id, 'message', message_id::text, first_seen_at FROM secrets;
-- The messages hold no secret, so a scan from here finds nothing new.
INSERT INTO secret_scan_state (id, last_message_id) SELECT 1, max(id) FROM messages;
COMMIT;
