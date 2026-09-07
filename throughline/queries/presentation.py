"""Read-only presentation of source messages; never rewrites imported evidence."""

import re
from pathlib import Path
from urllib.parse import unquote

from ._exec import rows

# Strip only known envelope pairs; arbitrary XML and quoted code remain evidence.
ENVELOPES = ("recommended_plugins", "environment_context", "in-app-browser-context")


def narrative(text):
    value = text or ""
    for tag in ENVELOPES:
        value = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", value, flags=re.S)
    return re.sub(r"^\s*## My request:\s*", "", value).strip()


def previews(conn, sessions):
    if not sessions:
        return
    # Select first/last meaningful text in SQL to avoid transferring whole transcripts.
    clean = "COALESCE(m.content, '')"
    for tag in ENVELOPES:
        clean = f"regexp_replace({clean}, '<{tag}\\y[^>]*>.*?</{tag}>', '', 'gs')"
    clean = f"regexp_replace(regexp_replace({clean}, '^\\s*## My request:\\s*', ''), '^\\s+|\\s+$', '', 'g')"
    found = rows(
        conn,
        f"""
        WITH meaningful AS (
          SELECT m.conversation_id, m.id, m.role, m.created_at, {clean} AS text
          FROM messages m WHERE m.conversation_id = ANY(%(ids)s)
            AND m.role IN ('user', 'assistant')
        )
        SELECT DISTINCT ON (conversation_id, role, direction)
          conversation_id, id, role, created_at, left(text, 600) AS text, direction
        FROM meaningful CROSS JOIN (VALUES ('first'), ('last')) AS d(direction)
        WHERE text <> '' AND text !~ '^\\[Tool:'
        ORDER BY conversation_id, role, direction,
          CASE WHEN direction = 'first' THEN created_at END ASC NULLS LAST,
          CASE WHEN direction = 'last' THEN created_at END DESC NULLS LAST,
          CASE WHEN direction = 'first' THEN id END ASC,
          CASE WHEN direction = 'last' THEN id END DESC
    """,
        {"ids": [s["id"] for s in sessions]},
    )
    by_id = {s["id"]: s for s in sessions}
    for s in sessions:
        for field in (
            "opening",
            "prompt_at",
            "prompt_id",
            "answer",
            "answer_at",
            "answer_id",
            "latest_request",
            "latest_request_at",
            "latest_request_id",
        ):
            s[field] = None
    for m in found:
        s = by_id[m["conversation_id"]]
        if m["role"] == "user":
            prefix = "prompt" if m["direction"] == "first" else "latest_request"
            s["opening" if prefix == "prompt" else prefix] = m["text"]
        elif m["direction"] == "last":
            prefix = "answer"
            s[prefix] = m["text"]
        else:
            continue
        s[prefix + "_at"] = m["created_at"]
        s[prefix + "_id"] = m["id"]
    for s in sessions:
        title = s.get("title")
        if not title or any(f"<{tag}" in title for tag in ENVELOPES) or title.startswith("[Tool:"):
            s["title"] = (s["opening"] or "")[:120] or None
        s["awaiting_answer"] = bool(
            s["latest_request_id"]
            and (
                not s["answer_id"]
                or (s["latest_request_at"] and s["answer_at"] and s["latest_request_at"] > s["answer_at"])
            )
        )


def markdown_files(text):
    """Read explicit destinations, including balanced/escaped parentheses and spaces."""
    for match in re.finditer(r"\[([^\]\n]+)\]\(", text):
        start = match.end()
        if text[start : start + 1] == "<":
            end = text.find(">)", start + 1)
            if end != -1 and "\n" not in text[start:end]:
                yield match[1], unquote(text[start + 1 : end])
            continue
        depth, escaped, chars = 1, False, []
        for char in text[start:]:
            if char == "\n":
                break
            if escaped:
                chars.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield match[1], unquote("".join(chars).strip())
                    break
            chars.append(char)


def artifacts(conn, conversation_id, project_path):
    """Explicit file references only. A reference is neither success nor file identity."""
    source = rows(
        conn,
        """SELECT id, content, content_blocks, created_at FROM messages
        WHERE conversation_id = %(id)s AND role = 'assistant'
        AND (content LIKE '%%](%%' OR content_blocks IS NOT NULL)
        ORDER BY created_at DESC, id DESC""",
        {"id": conversation_id},
    )
    result, seen = [], set()
    for m in source:
        refs = []
        text = narrative(m["content"])
        if not text.startswith("[Tool:"):
            refs.extend(markdown_files(text))
        blocks = m["content_blocks"] or []
        if isinstance(blocks, dict):
            blocks = [blocks]
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, dict) and block.get("type") in ("file", "output_file", "image", "output_image"):
                path = block.get("path") or block.get("file_path")
                if path:
                    refs.append((block.get("filename") or Path(path).name, path))
        for label, path in refs:
            if path in seen or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", path):
                continue
            seen.add(path)
            status = "unavailable"
            try:
                root = Path(project_path).resolve() if project_path else None
                target = (root / path).resolve() if root else None
                if root and target and target.is_relative_to(root) and target.is_file():
                    status = "available"
            except (OSError, ValueError):
                pass
            result.append(
                dict(label=label, path=path, message_id=m["id"], created_at=m["created_at"], availability=status)
            )
            if len(result) >= 100:
                return result
    return result
