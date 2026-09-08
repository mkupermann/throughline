import json
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
