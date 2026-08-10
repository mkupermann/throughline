"""Unit tests for the Hermes adapter's JSON export path.

``test_adapter_hermes_db.py`` covers the live ``state.db`` SQLite path
(which is the primary source on a real install). This file covers the
companion ``~/.hermes/sessions/session_*.json`` snapshots — used when
state.db is unavailable or the user has only the JSON exports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from throughline.adapters.hermes import HermesAdapter


class TestHermesJSONAdapter:
    def test_name_and_label(self):
        a = HermesAdapter()
        assert a.name == "hermes"
        assert "Hermes" in a.label

    def test_parse_minimal_session_json(self, tmp_path):
        data = {
            "session_id": "20260511_160653_6f89a7",
            "platform": "cli",
            "model": "claude-opus-4-7",
            "session_start": 1_700_000_000.0,
            "last_updated": 1_700_000_900.0,
            "messages": [
                {"role": "user", "content": "Train me in Hermes"},
                {"role": "assistant", "content": "Hermes is..."},
            ],
            "system_prompt": "you are hermes",
            "tools": [{"name": "shell"}, {"name": "edit"}],
        }
        path = tmp_path / "session_20260511_160653_6f89a7.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        conv = HermesAdapter().parse(path)
        assert conv is not None
        assert conv.project_path == "hermes"
        assert conv.entrypoint == "cli"
        assert conv.model == "claude-opus-4-7"
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[1].role == "assistant"
        # Metadata round-trips per-session bookkeeping.
        assert conv.metadata["source"] == "hermes"
        assert conv.metadata["hermes_session_id"] == "20260511_160653_6f89a7"
        assert conv.metadata["tool_count"] == 2
        assert conv.metadata["system_prompt_chars"] == len("you are hermes")

    def test_unknown_roles_are_skipped(self, tmp_path):
        data = {
            "session_id": "s1",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "wizard", "content": "casts spell"},  # unknown → drop
                {"role": "assistant", "content": "hello"},
            ],
        }
        path = tmp_path / "session_s1.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        conv = HermesAdapter().parse(path)
        assert conv is not None
        roles = [m.role for m in conv.messages]
        assert roles == ["user", "assistant"]

    def test_session_id_is_deterministic_across_runs(self, tmp_path):
        data = {
            "session_id": "stable-id",
            "messages": [{"role": "user", "content": "x"}],
        }
        # Write the same payload to two different filenames — derived
        # session_id (uuid5 of "hermes:stable-id") must match.
        p1 = tmp_path / "session_a.json"
        p2 = tmp_path / "session_b.json"
        p1.write_text(json.dumps(data), encoding="utf-8")
        p2.write_text(json.dumps(data), encoding="utf-8")
        a = HermesAdapter()
        assert a.parse(p1).session_id == a.parse(p2).session_id

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "session_broken.json"
        p.write_text("{not json", encoding="utf-8")
        assert HermesAdapter().parse(p) is None

    def test_empty_messages_returns_none(self, tmp_path):
        p = tmp_path / "session_empty.json"
        p.write_text(json.dumps({"session_id": "e", "messages": []}), encoding="utf-8")
        assert HermesAdapter().parse(p) is None

    def test_is_present_when_neither_source_exists(self, tmp_path, monkeypatch):
        # Re-point the adapter's hermes root at an empty tmp dir.
        a = HermesAdapter()
        monkeypatch.setattr(
            HermesAdapter, "_hermes_root", property(lambda self: tmp_path / "missing")
        )
        assert a.is_present() is False

    def test_is_present_when_sessions_dir_exists_but_is_empty(self, tmp_path, monkeypatch):
        # Spec §4.4: an existing-but-empty data dir must not report present.
        fake_root = tmp_path / ".hermes"
        (fake_root / "sessions").mkdir(parents=True)
        a = HermesAdapter()
        monkeypatch.setattr(
            HermesAdapter, "_hermes_root", property(lambda self: fake_root)
        )
        assert a.is_present() is False

    def test_is_present_when_a_session_export_exists(self, tmp_path, monkeypatch):
        fake_root = tmp_path / ".hermes"
        sessions = fake_root / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "session_1.json").write_text(
            json.dumps({"session_id": "1", "messages": [{"role": "user", "content": "hi"}]}),
            encoding="utf-8",
        )
        a = HermesAdapter()
        monkeypatch.setattr(
            HermesAdapter, "_hermes_root", property(lambda self: fake_root)
        )
        assert a.is_present() is True

    def test_is_present_when_only_state_db_exists(self, tmp_path, monkeypatch):
        # state.db counts as a discovered file even before its rows are
        # checked — same "discovered, not necessarily parseable" rule
        # Task 5 established for claude_code.
        fake_root = tmp_path / ".hermes"
        fake_root.mkdir(parents=True)
        (fake_root / "state.db").write_bytes(b"")
        a = HermesAdapter()
        monkeypatch.setattr(
            HermesAdapter, "_hermes_root", property(lambda self: fake_root)
        )
        assert a.is_present() is True
