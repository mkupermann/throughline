import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from throughline.jobs import cli_bridge
from throughline.self_referential import generated_by


def test_only_registered_installed_clis_can_run(monkeypatch):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda value: "/bin/true")
    with pytest.raises(ValueError):
        cli_bridge.complete({"cli": "sh", "prompt": "test"})
    with pytest.raises(ValueError):
        cli_bridge.complete({"cli": "codex", "prompt": "test", "model": "--inject"})
    assert generated_by("Throughline data processing: example") == "cli_bridge"


def test_bridge_requires_token_and_accepts_no_arbitrary_command(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_CLI_BRIDGE_TOKEN", "test-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), cli_bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(url + "/capabilities")
        assert e.value.code == 401
        req = urllib.request.Request(url + "/capabilities", headers={"Authorization": "Bearer test-token"})
        data = json.load(urllib.request.urlopen(req))
        assert set(data["clis"]) == {"codex", "vibe", "claude"}
        req = urllib.request.Request(
            url + "/complete",
            data=json.dumps({"cli": "sh", "prompt": "test"}).encode(),
            headers={"Authorization": "Bearer test-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("schema", ["not an object", ["array"], {"description": "x" * 64000}])
def test_bridge_rejects_oversized_or_non_object_schema(monkeypatch, schema):
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: "/bin/true")
    with pytest.raises(ValueError, match="Schema must be"):
        cli_bridge.complete({"cli": "codex", "prompt": "synthetic", "schema": schema})


def test_cli_environment_keeps_its_login_but_not_database_or_other_provider_secrets(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("MISTRAL_API_KEY", "retained-vibe-key")
    monkeypatch.setenv("OPENAI_API_KEY", "retained-codex-key")
    monkeypatch.setenv("PGPASSWORD", "database-key")
    monkeypatch.setenv("THROUGHLINE_CLI_BRIDGE_TOKEN", "bridge-key")
    monkeypatch.setenv("UNRELATED_PRIVATE_SECRET", "private")
    vibe = cli_bridge.cli_environment("vibe")
    assert vibe["MISTRAL_API_KEY"] == "retained-vibe-key"
    assert vibe["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert vibe["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert "OPENAI_API_KEY" not in vibe
    assert not {"PGPASSWORD", "THROUGHLINE_CLI_BRIDGE_TOKEN", "UNRELATED_PRIVATE_SECRET"} & vibe.keys()
    assert cli_bridge.cli_environment("codex")["OPENAI_API_KEY"] == "retained-codex-key"
    assert os.environ["PGPASSWORD"] == "database-key"


@pytest.mark.parametrize(
    "cli, output, code",
    [("vibe", '[{"role":"assistant","content":null}]', "cli_output"), ("claude", '{"is_error":true}', "cli_failed")],
)
def test_cli_invalid_output_has_a_safe_error_code(monkeypatch, cli, output, code):
    from throughline.ai_errors import AIConnectionError

    class Process:
        returncode = 0

        def communicate(self, **kwargs):
            return output, ""

    monkeypatch.setattr(cli_bridge.shutil, "which", lambda _: "/bin/vibe")
    monkeypatch.setattr(cli_bridge.subprocess, "Popen", lambda *a, **kw: Process())
    with pytest.raises(AIConnectionError) as error:
        cli_bridge.complete({"cli": cli, "prompt": "synthetic"})
    assert error.value.code == code
