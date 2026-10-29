"""Explicit labels survive imports; a folder-derived group is never a confirmed project."""

from ._exec import one, rows


def attach(conn, projects):
    if not projects:
        return projects
    names = {
        r["project_key"]: r
        for r in rows(
            conn,
            "SELECT project_key, display_name, name_origin, source_conversation_ids, is_curated FROM project_names WHERE project_key = ANY(%(keys)s)",
            {"keys": [p["project"] for p in projects]},
        )
    }
    fallback = {}
    for project in projects:
        name = names.get(project["project"], {})
        project["display_name"] = name.get("display_name")
        project["is_curated"] = name.get("is_curated", False)
        project["name_origin"] = name.get("name_origin", "folder")
        project["source_conversation_ids"] = name.get("source_conversation_ids", [])
        project["context_label"] = None
        if not project["display_name"] and project["project"] not in fallback:
            from .presentation import previews
            from .projects import sessions

            recent = sessions(conn, project["project"], limit=1)
            previews(conn, recent)
            if recent and recent[0].get("opening"):
                fallback[project["project"]] = " ".join(recent[0]["opening"].split())[:100]
        project["context_label"] = fallback.get(project["project"])

    return projects


def save(conn, project, display_name):
    from .projects import project_filter_params, project_filter_sql

    params = {**project_filter_params(project), "display_name": display_name}
    if not one(conn, f"SELECT id FROM conversations c WHERE {project_filter_sql()} LIMIT 1", params) and not one(
        conn, "SELECT project_key FROM project_names WHERE project_key=%(project)s AND is_curated", params
    ):
        return None
    return one(
        conn,
        """INSERT INTO project_names(project_key, display_name) VALUES (%(project)s, %(display_name)s)
        ON CONFLICT(project_key) DO UPDATE SET display_name=EXCLUDED.display_name, updated_at=now(), name_origin='user', source_conversation_ids='{}'
        RETURNING project_key, display_name""",
        params,
    )


def labels(conn, keys):
    return {
        row["project_key"]: row["display_name"]
        for row in rows(
            conn,
            "SELECT project_key,display_name FROM project_names WHERE project_key=ANY(%s)",
            (list({key for key in keys if key}),),
        )
    }
