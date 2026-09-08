"""Explicit, reversible membership independent of imported source working folders."""

import uuid

from ._exec import one, rows
from .projects import project_filter_params, project_name_sql


def create_project(conn, display_name):
    key = "project:" + uuid.uuid4().hex
    result = one(
        conn,
        """INSERT INTO project_names(project_key,display_name,is_curated)
        VALUES (%s,%s,true) RETURNING project_key AS project,display_name,is_curated""",
        (key, display_name),
    )
    conn.commit()
    return result


def assign(conn, ids, target, expected):
    params = {**project_filter_params(expected), "ids": ids}
    current = rows(
        conn,
        f"SELECT c.id,{project_name_sql()} AS project FROM conversations c WHERE c.id=ANY(%(ids)s) ORDER BY c.id FOR UPDATE",
        params,
    )
    if len(current) != len(ids):
        raise ValueError("One or more conversations no longer exist.")
    if any(row["project"] != expected for row in current):
        raise ValueError("Project membership changed. Refresh before applying this correction.")
    if target is not None:
        if not one(conn, "SELECT project_key FROM project_names WHERE project_key=%s", (target,)):
            raise ValueError("Choose a named project, or create a project first.")
    with conn.cursor() as cur:
        cur.execute("UPDATE conversations SET assigned_project=%s WHERE id=ANY(%s)", (target, ids))
    conn.commit()
    return {"updated": len(ids), "project": target}


def history(conn, conversation_id):
    return rows(
        conn,
        """SELECT h.*, u.display_name AS actor_name, old.display_name AS previous_name, new.display_name AS assigned_name
        FROM conversation_project_changes h
        LEFT JOIN access_users u ON h.actor='user:' || u.id::text
        LEFT JOIN project_names old ON old.project_key=h.previous_project
        LEFT JOIN project_names new ON new.project_key=h.assigned_project
        WHERE conversation_id=%s ORDER BY occurred_at DESC,id DESC LIMIT 100""",
        (conversation_id,),
    )
