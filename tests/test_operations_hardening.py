"""Deployment contracts that prevent accidental local-data exposure."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
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


def test_application_uid_and_gid_are_build_time_configuration(compose: dict) -> None:
    """A fixed image UID cannot read another local user's 0600 source files."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    build_args = compose["services"]["web"]["build"]["args"]

    assert "ARG THROUGHLINE_UID" in dockerfile
    assert "ARG THROUGHLINE_GID" in dockerfile
    assert build_args["THROUGHLINE_UID"] == "${THROUGHLINE_UID:-1000}"
    assert build_args["THROUGHLINE_GID"] == "${THROUGHLINE_GID:-1000}"


def test_compose_bootstrap_creates_private_self_contained_environment(tmp_path: Path) -> None:
    """A fresh checkout needs a generated secret and matching host identity."""
    env_file = tmp_path / ".env"

    subprocess.run(
        [sys.executable, str(ROOT / "scripts/init_compose_env.py"), "--env-file", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )

    values = dict(
        line.split("=", 1)
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert values["THROUGHLINE_UID"] == str(os.getuid())
    assert values["THROUGHLINE_GID"] == str(os.getgid())
    assert values["POSTGRES_DB"] == "throughline"
    assert values["POSTGRES_USER"] == "throughline"
    assert values["POSTGRES_PASSWORD"] not in {"", "replace-with-a-unique-local-secret"}
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "config", "--quiet"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_compose_bootstrap_refreshes_stale_identity_without_rotating_secret(tmp_path: Path) -> None:
    """Moving a checkout to another user must not leave 0600 mounts unreadable."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=preserve-this-secret\nTHROUGHLINE_UID=99999\nTHROUGHLINE_GID=99999\n",
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(ROOT / "scripts/init_compose_env.py"), "--env-file", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )

    values = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines())
    assert values["POSTGRES_PASSWORD"] == "preserve-this-secret"
    assert values["THROUGHLINE_UID"] == str(os.getuid())
    assert values["THROUGHLINE_GID"] == str(os.getgid())


def test_credential_rotation_uses_legacy_connection_and_never_exposes_new_secret_in_argv(
    tmp_path: Path,
) -> None:
    """Persistent old volumes need an explicit path before web waits on migrations."""
    fake_psql = tmp_path / "psql"
    received = tmp_path / "received.txt"
    fake_psql.write_text(
        '#!/bin/sh\nprintf \'%s\\n%s\\n\' "$PGPASSWORD" "$PGUSER" > "$ROTATION_CAPTURE"\ncat >> "$ROTATION_CAPTURE"\n'
    )
    fake_psql.chmod(0o755)
    env = {
        **os.environ,
        "PSQL_BIN": str(fake_psql),
        "ROTATION_CAPTURE": str(received),
        "THROUGHLINE_LEGACY_DB_PASSWORD": "old-password",
        "THROUGHLINE_LEGACY_DB_USER": "throughline",
        "THROUGHLINE_LEGACY_DB_NAME": "throughline",
        "POSTGRES_USER": "throughline",
        "POSTGRES_DB": "throughline",
        "POSTGRES_PASSWORD": "new'password",
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGDATABASE": "throughline",
    }

    subprocess.run(
        ["sh", str(ROOT / "throughline/shell/rotate_compose_credentials.sh")],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    old_password, old_user, statement = received.read_text(encoding="utf-8").splitlines()
    assert (old_password, old_user) == ("old-password", "throughline")
    assert statement == "ALTER ROLE CURRENT_USER PASSWORD 'new''password';"


def test_credential_rotation_refuses_to_rename_an_existing_role_or_database(tmp_path: Path) -> None:
    """Password rotation must not claim to migrate immutable Postgres names."""
    fake_psql = tmp_path / "psql"
    fake_psql.write_text("#!/bin/sh\nexit 99\n")
    fake_psql.chmod(0o755)
    env = {
        **os.environ,
        "PSQL_BIN": str(fake_psql),
        "THROUGHLINE_LEGACY_DB_PASSWORD": "old-password",
        "THROUGHLINE_LEGACY_DB_USER": "throughline",
        "THROUGHLINE_LEGACY_DB_NAME": "throughline",
        "POSTGRES_USER": "renamed-user",
        "POSTGRES_DB": "renamed-database",
        "POSTGRES_PASSWORD": "new-password",
    }

    result = subprocess.run(
        ["sh", str(ROOT / "throughline/shell/rotate_compose_credentials.sh")],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "POSTGRES_USER is immutable" in result.stderr
    assert "POSTGRES_DB is immutable" in result.stderr


def test_compose_exposes_an_explicit_credential_rotation_service(compose: dict) -> None:
    """The old-volume path must be runnable before migration-gated services start."""
    rotate = compose["services"]["credential-rotate"]

    assert rotate["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert rotate["profiles"] == ["credential-rotate"]
    assert rotate["environment"]["THROUGHLINE_LEGACY_DB_PASSWORD"] == "${THROUGHLINE_LEGACY_DB_PASSWORD:-}"
    assert rotate["environment"]["POSTGRES_USER"] == "${POSTGRES_USER:-throughline}"
    assert rotate["environment"]["POSTGRES_DB"] == "${POSTGRES_DB:-throughline}"
    assert rotate["command"] == ["sh", "/app/throughline/shell/rotate_compose_credentials.sh"]


def test_compose_source_mounts_follow_the_unprivileged_home(compose: dict) -> None:
    """Moving off root must not make local read-only source mounts disappear."""
    source_mounts = compose["x-source-mounts"]
    assert source_mounts
    assert all(":/home/throughline/" in mount for mount in source_mounts)
    assert all(mount.endswith(":ro") for mount in source_mounts)


@pytest.mark.integration
def test_configured_container_uid_reads_a_private_source_mount(tmp_path: Path) -> None:
    """A 0600 transcript must remain readable after the image drops root."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("Docker daemon is not available to this test user")

    fixture = tmp_path / "private.jsonl"
    fixture.write_text("private transcript", encoding="utf-8")
    fixture.chmod(0o600)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"POSTGRES_PASSWORD=test-only-password\nTHROUGHLINE_UID={os.getuid()}\nTHROUGHLINE_GID={os.getgid()}\n",
        encoding="utf-8",
    )
    override = tmp_path / "compose.uid-test.yml"
    override.write_text(
        "services:\n"
        "  web:\n"
        "    command: [python, -c, \"from pathlib import Path; print(Path('/home/throughline/.claude/private.jsonl').read_text())\"]\n"
        "    volumes:\n"
        f"      - {fixture}:/home/throughline/.claude/private.jsonl:ro\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            "docker-compose.yml",
            "-f",
            str(override),
            "build",
            "web",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            "docker-compose.yml",
            "-f",
            str(override),
            "run",
            "--rm",
            "--no-deps",
            "web",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "private transcript"


def test_systemd_services_default_to_the_current_database() -> None:
    """Every job reads the same DB defaults, so one override reaches all of them."""
    shared = ROOT / "systemd" / "throughline.env"
    assert shared.read_text(encoding="utf-8").splitlines()[0] == "PGDATABASE=throughline"

    for unit in (ROOT / "systemd").glob("*.service"):
        text = unit.read_text(encoding="utf-8")
        assert "EnvironmentFile=%h/.config/throughline/throughline.env" in text
        assert "PGDATABASE=" not in text


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
