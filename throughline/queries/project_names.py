"""Explicit labels survive imports; a folder-derived group is never a confirmed project."""

from ._exec import one, rows


def attach(conn, projects):
    if not projects:
        return projects
    names = {
        r["project_key"]: r["display_name"]
        for r in rows(
            conn,
            "SELECT project_key, display_name FROM project_names WHERE project_key = ANY(%(keys)s)",
            {"keys": [p["project"] for p in projects]},
        )
    }
    for project in projects:
        project["display_name"] = names.get(project["project"])
        project["name_origin"] = "user" if project["display_name"] else "folder"
    return projects


def save(conn, project, display_name):
    from .projects import project_filter_params, project_filter_sql

    params = {**project_filter_params(project), "display_name": display_name}
    if not one(conn, f"SELECT id FROM conversations c WHERE {project_filter_sql()} LIMIT 1", params):
        return None
    return one(
        conn,
        """INSERT INTO project_names(project_key, display_name) VALUES (%(project)s, %(display_name)s)
        ON CONFLICT(project_key) DO UPDATE SET display_name=EXCLUDED.display_name, updated_at=now()
        RETURNING project_key, display_name""",
        params,
    )
