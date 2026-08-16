"""Deployment contracts that prevent accidental local-data exposure."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_publishes_every_network_service_on_loopback(compose: dict) -> None:
    """A missing loopback prefix would expose an unauthenticated service."""
    services = compose["services"]

    for service in ("postgres", "web", "ollama"):
        published = services[service]["ports"]
        assert all(str(port).startswith("127.0.0.1:") for port in published)


def test_compose_uses_configurable_database_credentials(compose: dict) -> None:
    """A published fixed password lets anybody who has the file connect."""
    postgres = compose["services"]["postgres"]["environment"]
    application = compose["x-pg-env"]

    for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        assert "${" in postgres[name]
        assert "throughline_dev_password" not in postgres[name]

    assert application["PGDATABASE"] == postgres["POSTGRES_DB"]
    assert application["PGUSER"] == postgres["POSTGRES_USER"]
    assert application["PGPASSWORD"] == postgres["POSTGRES_PASSWORD"]


def test_compose_ports_are_configurable_without_losing_loopback(compose: dict) -> None:
    """Changing a host port must not silently change the exposure boundary."""
    services = compose["services"]
    expected = {
        "postgres": "THROUGHLINE_DB_PORT",
        "web": "THROUGHLINE_WEB_PORT",
        "ollama": "THROUGHLINE_OLLAMA_PORT",
    }

    for service, variable in expected.items():
        assert variable in str(services[service]["ports"][0])


def test_application_image_runs_unprivileged_and_compose_owns_remote_bind(compose: dict) -> None:
    """The image stays safe when reused without the controlled Compose boundary."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "USER throughline" in dockerfile
    assert "THROUGHLINE_ALLOW_REMOTE" not in dockerfile
    assert compose["services"]["web"]["environment"]["THROUGHLINE_ALLOW_REMOTE"] == "1"


def test_compose_source_mounts_follow_the_unprivileged_home(compose: dict) -> None:
    """Moving off root must not make local read-only source mounts disappear."""
    source_mounts = compose["x-source-mounts"]
    assert source_mounts
    assert all(":/home/throughline/" in mount for mount in source_mounts)
    assert all(mount.endswith(":ro") for mount in source_mounts)


def test_systemd_services_default_to_the_current_database() -> None:
    """Stale unit defaults otherwise send scheduled work to an old database."""
    for unit in (ROOT / "systemd").glob("*.service"):
        text = unit.read_text(encoding="utf-8")
        assert 'Environment="PGDATABASE=throughline"' in text
        assert "claude_memory" not in text


@pytest.mark.parametrize("script", ["scripts/backup.sh", "throughline/shell/backup.sh"])
def test_backup_creates_owner_only_dump_files(script: str, tmp_path: Path) -> None:
    """A permissive umask would leak a full database dump to local users."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pg_dump = fake_bin / "pg_dump"
    pg_dump.write_text("#!/bin/sh\nprintf 'COPY public.demo (value) FROM stdin;\\nrow\\n\\\\.\\n'\n")
    pg_dump.chmod(0o755)

    backup_dir = tmp_path / "private" / "backups"
    env = {
        **os.environ,
        "PG_BIN": str(fake_bin),
        "PGDATABASE": "throughline",
        "PGUSER": "test-user",
        "CLAUDE_MEMORY_BACKUP_DIR": str(backup_dir),
        "CLAUDE_MEMORY_BACKUP_MIN_BYTES": "1",
    }
    subprocess.run(["bash", str(ROOT / script)], check=True, env=env, capture_output=True, text=True)

    dumps = list(backup_dir.glob("throughline_*.sql.gz"))
    assert len(dumps) == 1
    assert stat.S_IMODE(dumps[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
