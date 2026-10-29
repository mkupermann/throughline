"""Unit tests for ``throughline.config``.

These tests pin the env-var precedence and the default fallbacks so that
silent regressions (e.g. someone removing a ``USER`` fallback) trip a
test instead of producing surprise "permission denied" connection errors
on shared boxes.
"""

from __future__ import annotations

import os

import pytest

from throughline import config


class TestDbConfig:
    def test_defaults_when_no_env(self, monkeypatch):
        # Clear all relevant PG* env vars; force a known USER.
        for k in ("PGDATABASE", "PGUSER", "PGHOST", "PGPORT"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("USER", "testuser")
        cfg = config.get_db_config()
        assert cfg == {
            "dbname": "throughline",
            "user": "testuser",
            "host": "localhost",
            "port": 5432,
        }

    def test_pg_env_overrides_take_precedence(self, monkeypatch):
        monkeypatch.setenv("PGDATABASE", "custom_db")
        monkeypatch.setenv("PGUSER", "alice")
        monkeypatch.setenv("PGHOST", "db.internal")
        monkeypatch.setenv("PGPORT", "6543")
        cfg = config.get_db_config()
        assert cfg == {
            "dbname": "custom_db",
            "user": "alice",
            "host": "db.internal",
            "port": 6543,
        }

    def test_postgres_fallback_when_no_user_at_all(self, monkeypatch):
        monkeypatch.delenv("PGUSER", raising=False)
        monkeypatch.delenv("USER", raising=False)
        cfg = config.get_db_config()
        assert cfg["user"] == "postgres"

    def test_pgport_must_be_castable_to_int(self, monkeypatch):
        monkeypatch.setenv("PGPORT", "not-a-port")
        with pytest.raises(ValueError):
            config.get_db_config()


class TestClaudeDir:
    def test_default_resolves_to_home_claude(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_DIR", raising=False)
        p = config.get_claude_dir()
        assert p.name == ".claude"
        assert p.is_absolute()

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
        assert config.get_claude_dir() == tmp_path.resolve()


class TestRepoRoot:
    def test_walks_up_to_find_marker_pair(self):
        # The actual repo root must contain both ``scripts/`` and
        # ``pyproject.toml`` — this is the canonical layout assumption.
        root = config.repo_root()
        assert (root / "scripts").is_dir()
        assert (root / "pyproject.toml").is_file()


class TestLoadDotenv:
    def test_native_example_uses_the_resolved_login_user(self, monkeypatch):
        """dotenv has no shell expansion, so a literal $USER would break auth."""
        monkeypatch.setenv("USER", "native-test-user")
        monkeypatch.delenv("PGUSER", raising=False)

        template = config.repo_root() / ".env.example"
        applied = config.load_dotenv(template)

        assert "PGUSER" not in applied
        assert config.get_db_config()["user"] == "native-test-user"

    def test_sets_vars_from_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TL_TEST_KEY", raising=False)
        env = tmp_path / ".env"
        env.write_text("TL_TEST_KEY=hello\n")
        applied = config.load_dotenv(env)
        assert applied == {"TL_TEST_KEY": "hello"}
        assert os.environ["TL_TEST_KEY"] == "hello"

    def test_does_not_override_existing_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TL_TEST_KEY", "from-shell")
        env = tmp_path / ".env"
        env.write_text("TL_TEST_KEY=from-file\n")
        applied = config.load_dotenv(env)
        # Existing environment wins; the file value is not applied.
        assert "TL_TEST_KEY" not in applied
        assert os.environ["TL_TEST_KEY"] == "from-shell"

    def test_override_true_replaces_existing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TL_TEST_KEY", "from-shell")
        env = tmp_path / ".env"
        env.write_text("TL_TEST_KEY=from-file\n")
        config.load_dotenv(env, override=True)
        assert os.environ["TL_TEST_KEY"] == "from-file"

    def test_ignores_comments_blanks_and_strips_export_and_quotes(self, monkeypatch, tmp_path):
        for k in ("TL_A", "TL_B", "TL_C"):
            monkeypatch.delenv(k, raising=False)
        env = tmp_path / ".env"
        env.write_text("# a comment\n\nexport TL_A=plain\nTL_B=\"quoted value\"\nTL_C='has=equals'\n")
        applied = config.load_dotenv(env)
        assert applied == {"TL_A": "plain", "TL_B": "quoted value", "TL_C": "has=equals"}

    def test_missing_file_is_noop(self, tmp_path):
        applied = config.load_dotenv(tmp_path / "does-not-exist.env")
        assert applied == {}

    def test_defaults_to_repo_root_dotenv(self, monkeypatch):
        # With no path given it must resolve <repo_root>/.env. We only assert
        # it runs without error and returns a dict (the repo ships a .env in
        # dev; CI may not — both are acceptable).
        monkeypatch.delenv("TL_TEST_KEY", raising=False)
        applied = config.load_dotenv()
        assert isinstance(applied, dict)


def test_the_claude_binary_lookup_is_gone():
    """Generation no longer shells out to a vendor CLI, so nothing should be
    resolving one — a helper that still exists is an invitation to use it."""
    from throughline import config

    assert not hasattr(config, "get_claude_bin")
