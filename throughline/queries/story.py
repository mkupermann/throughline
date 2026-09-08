"""A bounded, source-linked project history. No model calls or inferred edges."""

from __future__ import annotations

from ._exec import one, rows
from .presentation import artifacts, previews
from .project_names import attach
from .projects import project_filter_params, project_filter_sql


def scope(project: str, path: str | None, generated: bool, providers=None) -> tuple[str, dict]:
    params = {**project_filter_params(project), "path": path}
    predicate = project_filter_sql("c")
    if path is not None:
        predicate += " AND c.project_path = %(path)s"
    if not generated:
        predicate += " AND c.generated_by IS NULL"
    if providers:
        params["providers"] = providers
        predicate += " AND COALESCE(c.source_tool, 'unattributed') = ANY(%(providers)s)"
    return predicate, params


def history(
    conn, project: str, *, path=None, generated=False, q="", order="newest", offset=0, limit=30, providers=None
):
    predicate, params = scope(project, path, generated, providers)
    params.update(term=f"%{q}%", offset=offset, limit=limit)
    paths = rows(
        conn,
        f"""
        SELECT c.project_path AS path, count(*) AS sessions
        FROM conversations c WHERE {project_filter_sql("c")}
        GROUP BY c.project_path ORDER BY c.project_path NULLS LAST
    """,
        params,
    )
    coverage = one(
        conn,
        f"""
        SELECT count(*) AS sessions, COALESCE(sum(c.message_count), 0) AS messages,
               max(c.updated_at) AS refreshed_at,
               count(*) FILTER (WHERE c.source_tool IS NULL) AS unattributed
        FROM conversations c WHERE {predicate}
    """,
        params,
    )
    hidden = one(
        conn,
        f"""
        SELECT count(*) AS n FROM conversations c
        WHERE {project_filter_sql("c")} AND c.generated_by IS NOT NULL
          AND (%(path)s::text IS NULL OR c.project_path = %(path)s)
    """,
        params,
    )["n"]
    search = ""
    if q:
        search = """ AND (c.summary ILIKE %(term)s OR EXISTS (
            SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.content ILIKE %(term)s))"""
    total = one(conn, f"SELECT count(*) AS n FROM conversations c WHERE {predicate}{search}", params)["n"]
    direction = "ASC" if order == "oldest" else "DESC"
    sessions = rows(
        conn,
        f"""
        SELECT c.id, c.session_id, c.summary AS title, c.started_at, c.ended_at,
               c.source_tool, c.model, c.git_branch, c.project_path, c.source_project_name, c.assigned_project, c.message_count,
               c.generated_by,
               (SELECT left(m.content, 240) FROM messages m WHERE m.conversation_id = c.id
                AND m.role = 'tool_result' ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS result,
               (SELECT m.created_at FROM messages m WHERE m.conversation_id = c.id
                AND m.role = 'tool_result' ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS result_at,
               (SELECT count(*) FROM messages m CROSS JOIN LATERAL jsonb_array_elements(
                  CASE jsonb_typeof(m.content_blocks) WHEN 'array' THEN m.content_blocks
                    WHEN 'object' THEN jsonb_build_array(m.content_blocks) ELSE '[]'::jsonb END) b
                WHERE m.conversation_id = c.id AND m.role <> 'user'
                  AND b->>'type' IN ('file', 'output_file', 'image', 'output_image')) AS file_count,
               (SELECT max(m.created_at) FROM messages m CROSS JOIN LATERAL jsonb_array_elements(
                  CASE jsonb_typeof(m.content_blocks) WHEN 'array' THEN m.content_blocks
                    WHEN 'object' THEN jsonb_build_array(m.content_blocks) ELSE '[]'::jsonb END) b
                WHERE m.conversation_id = c.id AND m.role <> 'user'
                  AND b->>'type' IN ('file', 'output_file', 'image', 'output_image')) AS file_at,
               (SELECT count(*) FROM memory_chunks mc WHERE mc.source_type = 'conversation'
                AND mc.source_id = c.id AND COALESCE(mc.status, 'active') <> 'forgotten') AS knowledge_count
        FROM conversations c WHERE {predicate}{search}
        ORDER BY c.started_at {direction}, c.id {direction}
        LIMIT %(limit)s OFFSET %(offset)s
    """,
        params,
    )
    previews(conn, sessions)
    recovery = rows(
        conn,
        f"SELECT c.id, c.summary AS title FROM conversations c WHERE {predicate} ORDER BY c.started_at DESC NULLS LAST, c.id DESC LIMIT 1",
        params,
    )
    previews(conn, recovery)
    checkpoint_sql = f"""
        SELECT p.id, p.kind, p.content, p.created_at, p.recorded_by,
               p.source_conversation_id, p.source_message_id, p.source_session_id,
               left(p.source_excerpt, 4000) AS source_excerpt,
               c.id IS NOT NULL AS source_available,
               ({project_filter_sql("c")} AND (p.project_path IS NULL OR c.project_path=p.project_path)) AS source_in_scope,
               m.id IS NOT NULL AS message_available,
               CASE WHEN p.source_message_id IS NOT NULL THEN m.content IS DISTINCT FROM p.source_excerpt
                    ELSE false END AS source_changed
        FROM project_checkpoints p
        LEFT JOIN conversations c ON c.id = p.source_conversation_id
        LEFT JOIN messages m ON m.id = p.source_message_id
        WHERE p.project_name = %(project)s AND p.project_path IS NOT DISTINCT FROM %(path)s::text
    """
    checkpoints = rows(conn, checkpoint_sql + " ORDER BY p.created_at DESC, p.id DESC LIMIT 100", params)
    latest_checkpoints = rows(
        conn,
        checkpoint_sql.replace("SELECT p.id", "SELECT DISTINCT ON (p.kind) p.id")
        + " ORDER BY p.kind, p.created_at DESC, p.id DESC",
        params,
    )
    return dict(
        identity=attach(conn, [{"project": project}])[0],
        recovery=recovery[0] if recovery else None,
        project=project,
        path=path,
        paths=paths,
        coverage=coverage,
        sessions=sessions,
        total=total,
        offset=offset,
        has_more=offset + len(sessions) < total,
        checkpoints=checkpoints,
        latest_checkpoints=latest_checkpoints,
        hidden_generated=hidden,
        order=order,
        query=q,
    )


def session_detail(
    conn, project, conversation_id, *, path=None, generated=False, q="", offset=0, limit=100, providers=None
):
    predicate, params = scope(project, path, generated, providers)
    params.update(id=conversation_id, term=f"%{q}%", has_query=bool(q), offset=offset, limit=limit)
    session = one(
        conn,
        f"SELECT c.id, c.source_tool, c.metadata, c.project_path FROM conversations c WHERE c.id = %(id)s AND {predicate}",
        params,
    )
    if not session:
        return None
    relations = source_relations(conn, session)
    messages = rows(
        conn,
        """
        SELECT m.id, m.uuid, m.role, m.content, m.content_blocks, m.tool_calls, m.created_at, m.model, m.tool_name,
               m.parent_uuid, m.is_sidechain, (%(has_query)s AND m.content ILIKE %(term)s) AS matches
        FROM messages m WHERE m.conversation_id = %(id)s
        ORDER BY m.created_at, m.id LIMIT %(limit)s OFFSET %(offset)s
    """,
        params,
    )
    total = one(conn, "SELECT count(*) AS n FROM messages WHERE conversation_id = %(id)s", params)["n"]
    knowledge = rows(
        conn,
        """
        SELECT id, content, category::text, status, confidence, superseded_by, created_at,
               (SELECT c.id FROM memory_chunks replacement JOIN conversations c
                  ON replacement.source_type = 'conversation' AND replacement.source_id = c.id
                WHERE replacement.id = mc.superseded_by) AS replacement_conversation_id
        FROM memory_chunks mc WHERE source_type = 'conversation' AND source_id = %(id)s
          AND COALESCE(status, 'active') <> 'forgotten'
        ORDER BY created_at, id LIMIT 100
    """,
        params,
    )
    knowledge_total = one(
        conn,
        """SELECT count(*) AS n FROM memory_chunks
        WHERE source_type = 'conversation' AND source_id = %(id)s
        AND COALESCE(status, 'active') <> 'forgotten'""",
        params,
    )["n"]
    matches = rows(
        conn,
        """SELECT id, left(content, 500) AS excerpt FROM messages
        WHERE conversation_id = %(id)s AND %(has_query)s AND content ILIKE %(term)s
        ORDER BY created_at, id LIMIT 30""",
        params,
    )
    return dict(
        artifacts=artifacts(conn, conversation_id, session["project_path"]),
        messages=messages,
        total=total,
        offset=offset,
        has_more=offset + len(messages) < total,
        knowledge=knowledge,
        knowledge_total=knowledge_total,
        matches=matches,
        relations=relations,
    )


def checkpoint(conn, project, data, recorded_by="local user"):
    predicate, params = scope(project, data.path, True)
    params.update(id=data.conversation_id, message_id=data.message_id)
    source = one(
        conn,
        f"""
        SELECT c.id, c.session_id, m.id AS message_id, m.uuid, m.content
        FROM conversations c LEFT JOIN messages m
          ON m.conversation_id = c.id AND m.id = %(message_id)s
        WHERE c.id = %(id)s AND {predicate} FOR SHARE OF c
    """,
        params,
    )
    if not source or (data.message_id is not None and source["message_id"] is None):
        return None
    result = one(
        conn,
        """
        INSERT INTO project_checkpoints (project_name, project_path, kind, content,
            source_conversation_id, source_message_id, source_session_id, source_message_uuid, source_excerpt, recorded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """,
        (
            project,
            data.path,
            data.kind,
            data.content.strip(),
            source["id"],
            source["message_id"],
            source["session_id"],
            source["uuid"],
            source["content"] or "",
            recorded_by,
        ),
    )
    conn.commit()
    return result


def source_relations(conn, session):
    """Resolve only explicit source IDs. Ambiguity never creates an edge."""
    metadata = session.get("metadata") or {}
    if not isinstance(metadata, dict):
        return []
    tool = session.get("source_tool")
    field = None
    reference = None
    if tool == "codex" and isinstance(metadata.get("source_metadata"), dict):
        reference = metadata["source_metadata"].get("forked_from_id")
        field = "source_metadata.forked_from_id"
    elif tool == "vibe":
        reference = metadata.get("parent_session_id")
        field = "parent_session_id"
    if not isinstance(reference, str) or not reference:
        return []
    candidates = rows(
        conn,
        """
        SELECT id, summary AS title FROM conversations
        WHERE source_tool = %s AND id <> %s
          AND (session_id::text = %s OR metadata->>'codex_session_id' = %s
               OR metadata->>'vibe_session_id' = %s)
        LIMIT 2
    """,
        (tool, session["id"], reference, reference, reference),
    )
    target = candidates[0] if len(candidates) == 1 else None
    return [
        {
            "kind": "Forked from" if tool == "codex" else "Parent session",
            "target_id": target["id"] if target else None,
            "title": target["title"] if target else None,
            "reference": reference,
            "source_field": field,
            "resolution": "resolved" if target else "ambiguous" if candidates else "not imported",
        }
    ]
