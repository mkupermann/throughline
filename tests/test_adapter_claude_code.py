"""Unit tests for throughline.adapters.claude_code against synthetic JSONL.

The Claude Code adapter delegates to the legacy ``scripts/ingest_sessions.py``
helpers (role mapping, content-block extraction, token aggregation), so these
tests are a contract: feed it a tiny JSONL, assert the shape that lands in
``NormalisedConversation``. They are the canary if the legacy helpers ever
drift from the adapter's expectations.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from throughline.adapters.claude_code import ClaudeCodeAdapter


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestClaudeCodeAdapter:
    def test_name_and_label(self):
        a = ClaudeCodeAdapter()
        assert a.name == "claude_code"
        assert a.label == "Claude Code"

    def test_parse_minimal_session(self, tmp_path):
        sid = str(uuid.uuid4())
        ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        path = tmp_path / f"{sid}.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "isSidechain": False,
                    "message": {"role": "user", "content": "Hello"},
                    "timestamp": ts,
                    "sessionId": sid,
                    "cwd": "/repo/myproject",
                    "entrypoint": "cli",
                    "gitBranch": "main",
                },
                {
                    "type": "assistant",
                    "uuid": str(uuid.uuid4()),
                    "isSidechain": False,
                    "message": {
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "content": [{"type": "text", "text": "Hi back"}],
                    },
                    "timestamp": ts,
                    "sessionId": sid,
                },
            ],
        )
        conv = ClaudeCodeAdapter().parse(path)
        assert conv is not None
        # cwd is the authoritative project path — NOT the parent-dir hyphen-replace
        assert conv.project_path == "/repo/myproject"
        assert conv.git_branch == "main"
        assert conv.entrypoint == "cli"
        assert conv.model == "claude-sonnet-4-6"
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello"
        assert conv.messages[1].role == "assistant"
        assert "Hi back" in conv.messages[1].content

    def test_parse_returns_none_on_empty_file(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        assert ClaudeCodeAdapter().parse(path) is None

    def test_parse_returns_none_when_no_message_entries(self, tmp_path):
        path = tmp_path / "no-msgs.jsonl"
        # Lines that lack a "message" dict are silently dropped.
        _write_jsonl(
            path,
            [
                {"type": "system", "metadata": {"foo": 1}},
                {"type": "system", "summary": "init"},
            ],
        )
        assert ClaudeCodeAdapter().parse(path) is None

    def test_corrupt_json_lines_are_skipped_not_fatal(self, tmp_path):
        sid = str(uuid.uuid4())
        ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        path = tmp_path / f"{sid}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json\n")
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": str(uuid.uuid4()),
                        "message": {"role": "user", "content": "survived"},
                        "timestamp": ts,
                        "sessionId": sid,
                        "cwd": "/x",
                    }
                )
                + "\n"
            )
            f.write("garbage tail\n")
        conv = ClaudeCodeAdapter().parse(path)
        assert conv is not None
        assert any("survived" in m.content for m in conv.messages)

    def test_session_id_is_taken_from_jsonl_not_filename(self, tmp_path):
        # The first message's sessionId wins; filename is just incidental.
        real_sid = str(uuid.uuid4())
        ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        path = tmp_path / "weird-filename.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "message": {"role": "user", "content": "hi"},
                    "timestamp": ts,
                    "sessionId": real_sid,
                    "cwd": "/x",
                }
            ],
        )
        conv = ClaudeCodeAdapter().parse(path)
        assert conv is not None
        assert conv.session_id == real_sid

    @pytest.mark.parametrize(
        "first_user_prompt",
        [
            # generate_titles.py
            "Du bekommst einen Auszug aus einer Claude Code Session. "
            "Generiere einen prägnanten deutschen Titel (max 60 Zeichen) …",
            # extract_entities.py
            "Du analysierst ein Session-Transcript und extrahierst "
            "strukturierte Entitäten + Beziehungen als JSON. …",
            # extract_memory.py (current phrasing)
            "Du analysierst eine Entwickler-Session (Claude Code, Codex, "
            "Hermes, Continue, Windsurf, Cline) und extrahierst …",
            # extract_memory.py (older phrasing still present in ~/.claude)
            "Du analysierst eine Claude Code Entwickler-Session und "
            "extrahierst verwertbare Erkenntnisse …",
            # reflect_memory.py — DEDUP
            "Du bekommst zwei Memory-Chunks aus einer persoenlichen "
            "Wissensdatenbank. …",
            # reflect_memory.py — MERGE
            "Du bekommst zwei Memory-Chunks die denselben Sachverhalt "
            "beschreiben. …",
            # reflect_memory.py — CONTRA
            "Zwei Memory-Chunks aus einer persoenlichen Wissensdatenbank …",
            # reflect_memory.py — STALE
            "Ein Memory-Chunk aus einer persoenlichen Wissensdatenbank: …",
            # reflect_memory.py — CONSOLIDATE
            "Du bekommst mehrere Memory-Chunks zum gleichen Thema. …",
        ],
    )
    def test_skips_internal_pipeline_echo_sessions(self, tmp_path, first_user_prompt):
        # Every headless `claude -p` call issued by scripts/*.py is itself
        # logged as its own Claude Code session. Without this filter the
        # ingest re-imports them, producing hundreds of duplicate meta rows.
        # The adapter must drop them — covering title-gen, entity extraction,
        # memory extraction, and every reflect-memory prompt shape.
        sid = str(uuid.uuid4())
        ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        path = tmp_path / f"{sid}.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "isSidechain": False,
                    "message": {"role": "user", "content": first_user_prompt},
                    "timestamp": ts,
                    "sessionId": sid,
                    "cwd": "/repo/throughline",
                },
                {
                    "type": "assistant",
                    "uuid": str(uuid.uuid4()),
                    "isSidechain": False,
                    "message": {
                        "role": "assistant",
                        "model": "claude-sonnet-4-6",
                        "content": [{"type": "text", "text": "response"}],
                    },
                    "timestamp": ts,
                    "sessionId": sid,
                },
            ],
        )

        a = ClaudeCodeAdapter()
        assert a.parse(path) is None

    def test_does_not_skip_session_that_merely_mentions_generator(self, tmp_path):
        # Only the *first* user message starting with the marker triggers the
        # skip — sessions that quote the prompt later (e.g. debugging
        # generate_titles.py) must still be ingested.
        sid = str(uuid.uuid4())
        ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        path = tmp_path / f"{sid}.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "isSidechain": False,
                    "message": {
                        "role": "user",
                        "content": "Schau dir bitte scripts/generate_titles.py an.",
                    },
                    "timestamp": ts,
                    "sessionId": sid,
                    "cwd": "/repo/throughline",
                },
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "isSidechain": False,
                    "message": {
                        "role": "user",
                        "content": (
                            "Du bekommst einen Auszug aus einer Claude Code "
                            "Session. Generiere einen prägnanten deutschen "
                            "Titel — das ist der Prompt aus dem Script."
                        ),
                    },
                    "timestamp": ts,
                    "sessionId": sid,
                },
            ],
        )

        a = ClaudeCodeAdapter()
        conv = a.parse(path)
        assert conv is not None
        assert conv.session_id == sid

    def test_discover_walks_per_project_subdirs(self, tmp_path, monkeypatch):
        # Claude Code stores ~/.claude/projects/<slug>/*.jsonl; the adapter
        # must enumerate sub-dirs, not flat-glob the root.
        root = tmp_path / "projects"
        (root / "proj-a").mkdir(parents=True)
        (root / "proj-b").mkdir(parents=True)
        (root / "proj-a" / "s1.jsonl").write_text("")
        (root / "proj-a" / "s2.jsonl").write_text("")
        (root / "proj-b" / "s3.jsonl").write_text("")
        # Random non-dir at root must be ignored.
        (root / "ignored.txt").write_text("")

        a = ClaudeCodeAdapter()
        monkeypatch.setattr(a, "home", root)
        names = sorted(p.name for p in a.discover())
        assert names == ["s1.jsonl", "s2.jsonl", "s3.jsonl"]
