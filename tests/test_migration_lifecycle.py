"""Migration discovery and Compose startup contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from throughline.jobs import migrate


def test_discovery_rejects_duplicate_ordinals(tmp_path: Path) -> None:
    """Two migrations must never silently share an execution position."""
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_second.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(migrate.MigrationValidationError, match="duplicate migration ordinal 001"):
        migrate.discover_migrations(tmp_path)


def test_renumbered_migration_honours_its_recorded_legacy_filename() -> None:
    """Renumbering a shipped file must not re-run it on existing databases."""
    migration = migrate.MIGRATIONS_DIR / "005_widen_conversation_token_counts.sql"

    assert migrate.is_applied(migration, {"001_widen_conversation_token_counts.sql"})


def test_compose_waits_for_migrations_before_starting_application_services() -> None:
    """Web and MCP must only start after the one-shot upgrade service succeeds."""
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    migration = services["migrate"]
    assert migration["command"] == ["throughline", "migrate"]
    assert migration["depends_on"]["postgres"]["condition"] == "service_healthy"

    for name in ("web", "mcp"):
        assert services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
