"""Unit tests for throughline.adapters.continue_dev against synthetic JSON.

Continue.dev has shipped two on-disk layouts (per-session ``<id>.json`` and
the aggregate ``sessions.json`` array). The adapter handles both; these
tests pin both code paths.
"""

from __future__ import annotations

import json

from throughline.adapters.continue_dev import ContinueDevAdapter


class TestContinueDevAdapter:
    def test_name_and_label(self):
        a = ContinueDevAdapter()
        assert a.name == "continue"
        assert "Continue" in a.label

    def test_parse_per_session_file(self, tmp_path):
        sess = {
            "sessionId": "abc-123",
            "title": "Refactor auth",
            "history": [
                {"role": "user", "content": "extract login()"},
                {"role": "assistant", "content": "Reading auth.py..."},
                {"role": "tool", "content": "tool output"},
            ],
            "lastUpdated": 1_700_000_000,
        }
        path = tmp_path / "abc-123.json"
        path.write_text(json.dumps(sess), encoding="utf-8")
        conv = ContinueDevAdapter().parse(path)
        assert conv is not None
        assert conv.project_path == "continue"
        assert conv.summary == "Refactor auth"
        roles = [m.role for m in conv.messages]
        # "tool" maps to tool_result via _ROLE_MAP
        assert roles == ["user", "assistant", "tool_result"]

    def test_parse_sessions_json_array_folds_into_one_conversation(self, tmp_path):
        agg = [
            {
                "sessionId": "s1",
                "title": "First",
                "history": [{"role": "user", "content": "one"}],
            },
            {
                "sessionId": "s2",
                "title": "Second",
                "history": [{"role": "user", "content": "two"}],
            },
        ]
        path = tmp_path / "sessions.json"
        path.write_text(json.dumps(agg), encoding="utf-8")
        conv = ContinueDevAdapter().parse(path)
        assert conv is not None
        # Combined conversation gets a separator system message before each
        # session's messages.
        roles = [m.role for m in conv.messages]
        assert roles.count("system") == 2
        assert any("First" in m.content for m in conv.messages)
        assert any("Second" in m.content for m in conv.messages)
        assert conv.metadata.get("aggregate") is True
        assert conv.metadata.get("session_count") == 2

    def test_messages_key_is_accepted_as_alias_for_history(self, tmp_path):
        # Some Continue versions use ``messages`` instead of ``history``.
        sess = {
            "sessionId": "msg-form",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        }
        path = tmp_path / "msg-form.json"
        path.write_text(json.dumps(sess), encoding="utf-8")
        conv = ContinueDevAdapter().parse(path)
        assert conv is not None
        assert len(conv.messages) == 2

    def test_corrupt_json_returns_none(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert ContinueDevAdapter().parse(path) is None

    def test_session_id_is_deterministic_uuid5(self, tmp_path):
        sess = {
            "sessionId": "stable",
            "history": [{"role": "user", "content": "x"}],
        }
        p1 = tmp_path / "a.json"
        p2 = tmp_path / "b.json"
        p1.write_text(json.dumps(sess), encoding="utf-8")
        p2.write_text(json.dumps(sess), encoding="utf-8")
        # Same sessionId → same derived session_id, regardless of filename.
        c1 = ContinueDevAdapter().parse(p1)
        c2 = ContinueDevAdapter().parse(p2)
        assert c1.session_id == c2.session_id

    def test_discover_excludes_sessions_json_from_per_session_glob(self, tmp_path, monkeypatch):
        # sessions.json is appended explicitly so it's still discovered, but
        # the per-session glob must not double-count it.
        root = tmp_path / ".continue" / "sessions"
        root.mkdir(parents=True)
        (root / "s1.json").write_text("{}")
        (root / "sessions.json").write_text("[]")
        a = ContinueDevAdapter()
        monkeypatch.setattr(a, "home", root)
        files = sorted(p.name for p in a.discover())
        # Both present, neither duplicated.
        assert files == ["s1.json", "sessions.json"]
