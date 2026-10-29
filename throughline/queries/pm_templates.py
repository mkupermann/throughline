"""Versioned templates and atomic creation of editable operational resources."""

from __future__ import annotations

from psycopg2.extras import Json

from ._exec import execute, one, rows


class TemplateConflict(ValueError):
    pass


def list_templates(conn):
    return rows(conn, "SELECT * FROM pm_templates ORDER BY kind, name, id")


def _snapshot(conn, template_id, version):
    row = one(
        conn, "SELECT snapshot FROM pm_template_versions WHERE template_id=%s AND version=%s", (template_id, version)
    )
    if row is None:
        raise ValueError(f"Template {template_id} version {version} does not exist")
    return row["snapshot"]


def _references(conn, kind, content):
    refs = []
    if "team_template" in content:
        if kind != "project":
            raise ValueError("Only project templates can reference a team template")
        refs.append((content["team_template"], "team"))
    if "role_templates" in content:
        if kind != "team" or not isinstance(content["role_templates"], list):
            raise ValueError("role_templates must be a list on a team template")
        refs.extend((ref, "role") for ref in content["role_templates"])
    resolved = []
    for ref, expected_kind in refs:
        if not isinstance(ref, dict) or any(
            not isinstance(ref.get(key), int) or isinstance(ref.get(key), bool) for key in ("id", "version")
        ):
            raise ValueError("Template references require integer id and version")
        snapshot = _snapshot(conn, ref["id"], ref["version"])
        if snapshot["kind"] != expected_kind:
            raise ValueError(f"Expected a {expected_kind} template reference")
        resolved.append(snapshot)
    return resolved


def _record_version(conn, row):
    execute(
        conn,
        "INSERT INTO pm_template_versions(template_id,version,snapshot) VALUES(%s,%s,%s)",
        (
            row["id"],
            row["version"],
            Json({**row, "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}),
        ),
    )


def create_template(conn, *, kind, name, description, content):
    with conn:
        _references(conn, kind, content)
        row = one(
            conn,
            "INSERT INTO pm_templates(kind,name,description,content) VALUES(%s,%s,%s,%s) RETURNING *",
            (kind, name, description, Json(content)),
        )
        _record_version(conn, row)
    return row


def update_template(conn, template_id, fields):
    with conn:
        current = one(conn, "SELECT * FROM pm_templates WHERE id=%s FOR UPDATE", (template_id,))
        if current is None:
            raise LookupError("Template not found")
        expected = fields.pop("expected_version", None)
        if expected is not None and expected != current["version"]:
            raise TemplateConflict("Template changed; reload before saving")
        effective = {**current, **fields}
        _references(conn, current["kind"], effective["content"])
        row = one(
            conn,
            "UPDATE pm_templates SET name=%s,description=%s,content=%s,archived=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *",
            (
                effective["name"],
                effective["description"],
                Json(effective["content"]),
                effective["archived"],
                template_id,
            ),
        )
        _record_version(conn, row)
    return row


def _instantiate(conn, snapshot, name, description):
    kind = snapshot["kind"]
    children = _references(conn, kind, snapshot["content"])
    # The whitelist is fixed; no user input is interpolated as an SQL identifier.
    table = {"project": "pm_projects", "team": "pm_teams", "role": "pm_roles"}[kind]
    if kind == "role":
        content = snapshot["content"]
        instructions = content.get("instructions", "")
        for key, label in (
            ("expected_output", "Expected output"),
            ("allowed_tools", "Requested tools (configuration required)"),
        ):
            if content.get(key):
                instructions += f"\n\n{label}:\n{content[key]}"
        row = one(
            conn,
            "INSERT INTO pm_roles(name,description,instructions,template_snapshot) VALUES(%s,%s,%s,%s) RETURNING *",
            (name, description, instructions.strip(), Json(snapshot)),
        )
    else:
        sections = {
            "project": (
                ("objective", "Objective"),
                ("stages", "Planned stages"),
                ("deliverables", "Deliverables"),
                ("acceptance_criteria", "Acceptance criteria"),
            ),
            "team": (
                ("workflow", "Planned workflow"),
                ("review_policy", "Review requirements (human verification required)"),
            ),
        }
        description = description or ""
        for key, label in sections[kind]:
            value = snapshot["content"].get(key)
            if value:
                description += f"\n\n{label}:\n{value}"
        row = one(
            conn,
            f"INSERT INTO {table}(name,description,template_snapshot) VALUES(%s,%s,%s) RETURNING *",
            (name, description.strip(), Json(snapshot)),
        )
    for child in children:
        instance = _instantiate(conn, child, child["name"], child["description"])
        if kind == "project":
            execute(
                conn, "INSERT INTO pm_project_teams(pm_project_id,team_id) VALUES(%s,%s)", (row["id"], instance["id"])
            )
        else:
            execute(conn, "INSERT INTO pm_team_roles(team_id,role_id) VALUES(%s,%s)", (row["id"], instance["id"]))
    return row


def instantiate_template(conn, template_id: int, *, name: str, description: str | None, version: int | None):
    with conn:
        current = one(conn, "SELECT * FROM pm_templates WHERE id=%s FOR SHARE", (template_id,))
        if current is None:
            raise LookupError("Template not found")
        if current["archived"]:
            raise TemplateConflict("This template is archived")
        snapshot = _snapshot(conn, template_id, version or current["version"])
        row = _instantiate(conn, snapshot, name, description if description is not None else snapshot["description"])
    return {**row, "kind": snapshot["kind"], "template_id": template_id, "template_version": snapshot["version"]}
