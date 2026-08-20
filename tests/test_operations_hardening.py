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


@pytest.fixture()
def ci_workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


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


def test_schema_ci_stops_on_the_first_sql_error(ci_workflow: dict) -> None:
    """psql otherwise reports success after errors in a schema file."""
    apply_schema = next(
        step for step in ci_workflow["jobs"]["schema-validation"]["steps"] if step.get("name") == "Apply schema"
    )

    assert "ON_ERROR_STOP=1" in apply_schema["run"]


def test_markdownlint_is_a_required_ci_gate(ci_workflow: dict) -> None:
    """Documentation format regressions must fail the workflow."""
    markdownlint = next(
        step for step in ci_workflow["jobs"]["markdown-lint"]["steps"] if step.get("name") == "markdownlint"
    )

    assert markdownlint.get("continue-on-error") is not True


def test_application_image_runs_unprivileged_and_compose_owns_remote_bind(compose: dict) -> None:
    """The image stays safe when reused without the controlled Compose boundary."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    web_environment = compose["services"]["web"]["environment"]

    assert "USER throughline" in dockerfile
    assert "THROUGHLINE_ALLOW_REMOTE" not in dockerfile
    assert "THROUGHLINE_HOST=127.0.0.1" in dockerfile
    assert web_environment["THROUGHLINE_HOST"] == "0.0.0.0"
    assert web_environment["THROUGHLINE_ALLOW_REMOTE"] == "1"


def test_application_uid_and_gid_are_build_time_configuration(compose: dict) -> None:
    """A fixed image UID cannot read another local user's 0600 source files."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    build_args = compose["services"]["web"]["build"]["args"]

    assert "ARG THROUGHLINE_UID" in dockerfile
    assert "ARG THROUGHLINE_GID" in dockerfile
    assert build_args["THROUGHLINE_UID"] == "${THROUGHLINE_UID:-1000}"
    assert build_args["THROUGHLINE_GID"] == "${THROUGHLINE_GID:-1000}"


def test_dockerfile_handles_an_existing_gid_without_assuming_its_group_name() -> None:
    """macOS GID 20 already exists in Debian-based images under another name."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '(getent group "$THROUGHLINE_GID" || groupadd --gid "$THROUGHLINE_GID" throughline)' in dockerfile
    assert 'useradd --create-home --uid "$THROUGHLINE_UID" --gid "$THROUGHLINE_GID"' in dockerfile
    assert 'install -d -m 700 -o throughline -g "$THROUGHLINE_GID" /var/lib/throughline/backups' in dockerfile
    assert "install -d -m 700 -o throughline -g throughline /var/lib/throughline/backups" not in dockerfile


@pytest.mark.integration
def test_docker_build_accepts_a_colliding_host_gid() -> None:
    """The application image must build for the common macOS numeric GID 20."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("Docker daemon is not available to this test user")

    subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            "THROUGHLINE_UID=10001",
            "--build-arg",
            "THROUGHLINE_GID=20",
            "--tag",
            "throughline-gid-collision-test",
            ".",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_current_outbound_templates_do_not_advertise_an_unused_anthropic_key() -> None:
    """A key documented as consumed changes the user's data-egress assessment."""
    for path in (ROOT / ".env.example", ROOT / "SECURITY.md"):
        text = path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" not in text
        assert "OPENAI_API_KEY" in text

    assert "embed --backend auto` whenever it is set" in (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "auto` selects hosted OpenAI" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_compose_persists_private_backups_in_a_named_volume(compose: dict) -> None:
    """A container-only backup path disappears when the web container is replaced."""
    web = compose["services"]["web"]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert web["environment"]["CLAUDE_MEMORY_BACKUP_DIR"] == "/var/lib/throughline/backups"
    assert "backup_data:/var/lib/throughline/backups" in web["volumes"]
    assert compose["volumes"]["backup_data"]["name"] == "throughline_backup_data"
    assert 'install -d -m 700 -o throughline -g "$THROUGHLINE_GID" /var/lib/throughline/backups' in dockerfile


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

    fixture_dir = tmp_path / ".claude"
    fixture_dir.mkdir()
    fixture = fixture_dir / "private.jsonl"
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
        # Compose replaces the base source mount by its target path. Mounting a
        # file below that read-only base directory would require OCI to create
        # a nested mountpoint and fails before the permission assertion runs.
        f"      - {fixture_dir}:/home/throughline/.claude:ro\n",
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


def test_systemd_ingest_uses_the_packaged_cli_and_shared_environment() -> None:
    """Timers must follow the supported installed command, not a source wrapper."""
    text = (ROOT / "systemd" / "throughline-ingest.service").read_text(encoding="utf-8")

    assert "ExecStart=/usr/bin/env throughline ingest --all" in text
    assert "scripts/ingest_sessions.py" not in text
    assert "EnvironmentFile=%h/.config/throughline/throughline.env" in text


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


@pytest.mark.parametrize("script", ["scripts/backup.sh", "throughline/shell/backup.sh"])
def test_backup_discovers_pg_dump_on_path_without_a_homebrew_location(script: str, tmp_path: Path) -> None:
    """Packaged backup must work on Linux and non-Homebrew installations."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pg_dump = fake_bin / "pg_dump"
    pg_dump.write_text("#!/bin/sh\nprintf 'COPY public.demo (value) FROM stdin;\\nrow\\n\\\\.\\n'\n")
    pg_dump.chmod(0o755)

    backup_dir = tmp_path / "private" / "backups"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PG_BIN": "",
        "PG_DUMP_BIN": "",
        "PGDATABASE": "throughline",
        "PGUSER": "test-user",
        "CLAUDE_MEMORY_BACKUP_DIR": str(backup_dir),
        "CLAUDE_MEMORY_BACKUP_MIN_BYTES": "1",
    }
    subprocess.run(["bash", str(ROOT / script)], check=True, env=env, capture_output=True, text=True)

    assert len(list(backup_dir.glob("throughline_*.sql.gz"))) == 1


def test_the_export_has_a_destination_that_survives_the_container(compose: dict) -> None:
    """An export written into the container's own filesystem is thrown away.

    Every source directory is mounted read-only and the rest of the image is
    a throwaway layer, so the export's default destination — the container
    user's home — looks like it worked and is gone on the next `up`. The
    export needs one writable place that lives on the host.
    """
    web = compose["services"]["web"]
    export_root = web["environment"]["THROUGHLINE_EXPORT_ROOT"]

    # The boundary the API enforces must be a mount, not the container's home.
    assert export_root != "/home/throughline"

    def parts(mount: str) -> tuple[str, str, str]:
        """Split source:target[:options], tolerating `${VAR:-default}` sources."""
        options = ""
        rest = mount
        if rest.endswith((":ro", ":rw")):
            rest, options = rest.rsplit(":", 1)
        source, _, target = rest.rpartition(":")
        return source, target, options

    target = [parts(str(v)) for v in web["volumes"]]
    match = [t for t in target if t[1] == export_root]
    assert match, f"nothing is mounted at {export_root}"
    source, _, options = match[0]

    # It has to be writable — read-only would fail at the first file.
    assert options != "ro"

    # And it comes from the host, not a named volume the user cannot reach:
    # an export nobody can open in their editor is not an export.
    assert source.startswith(("~", "./", "/", "${"))


def test_the_web_container_is_told_where_ollama_is(compose: dict) -> None:
    """The optional `embeddings` profile ships an Ollama service, and nothing
    ever told the web container to use it.

    Unset, the probe looks for Ollama on the container's own loopback, where
    there is never anything — so with the profile running, generation still
    reported no model available. The default names the compose service; a host
    install is one override away.
    """
    web = compose["services"]["web"]
    host = web["environment"]["OLLAMA_HOST"]

    assert "ollama" in host
    assert "localhost" not in host and "127.0.0.1" not in host
    # Overridable, because plenty of people run Ollama on the host instead.
    assert host.startswith("${")


def test_compose_does_not_borrow_variable_names_the_native_tool_reads(compose: dict) -> None:
    """`.env` is read by Compose *and* by `throughline.config.load_dotenv`.

    A Compose-only value stored under a name the native CLI also reads breaks
    the native install: setting `OLLAMA_HOST=http://host.docker.internal:11434`
    so the container could reach the host made the host's own server report no
    embedding and no generation backend, because that name resolves nowhere
    outside a container. Compose must map its own variable into the container's
    environment, not reuse the application's name on the host side.
    """
    web = compose["services"]["web"]

    # Names the application itself reads, on the host as well as in a container.
    shared = ("OLLAMA_HOST", "OPENAI_API_KEY", "THROUGHLINE_ANSWER_BASE_URL", "THROUGHLINE_ANSWER_MODEL")
    for key, value in web["environment"].items():
        if key in shared and isinstance(value, str) and "${" in value:
            interpolated = value[value.index("${") + 2 :].split(":-")[0].rstrip("}")
            assert interpolated != key, f"{key} is filled from ${{{key}}} in .env, which the native CLI reads too"
