import io
import json
import urllib.error

import pytest

from throughline import ai_runtime
from throughline.ai_errors import AIConnectionError
from throughline.api.routers.ai_settings import test_binding as check_binding
from throughline.jobs.cli_bridge import classify_failure


@pytest.mark.parametrize("status,code", [(409, "bridge_busy"), (401, "bridge_auth"), (400, "cli_failed")])
def test_bridge_failure_has_safe_actionable_reason(monkeypatch, status, code):
    monkeypatch.setenv("THROUGHLINE_CLI_BRIDGE_URL", "http://localhost:1")
    monkeypatch.setenv("THROUGHLINE_CLI_BRIDGE_TOKEN", "private-token")

    def fail(*args):
        raise urllib.error.HTTPError(
            "http://localhost:1", status, "failure", {}, io.BytesIO(b'{"error":"private-provider-key"}')
        )

    monkeypatch.setattr(ai_runtime, "post", fail)
    with pytest.raises(AIConnectionError) as error:
        ai_runtime.bridge("/complete", {"prompt": "synthetic"})
    assert error.value.code == code
    assert "private-" not in str(error.value)


def test_connection_api_preserves_busy_reason_without_provider_body(monkeypatch):
    monkeypatch.setattr(ai_runtime, "route", lambda purpose: {"cli": "vibe"})

    def busy(*args, **kwargs):
        raise AIConnectionError("bridge_busy")

    monkeypatch.setattr(ai_runtime, "generate", busy)
    result = check_binding("titles")
    assert result["ok"] is False
    assert result["error_code"] == "bridge_busy"
    assert "Wait" in result["error"]


def test_provider_error_does_not_echo_remote_body(monkeypatch):
    monkeypatch.setattr(ai_runtime, "route", lambda purpose: {"provider_type": "openai"})

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid?key=private-key", 401, "private-key", {}, io.BytesIO(b"private-key")
        )

    monkeypatch.setattr(ai_runtime, "generate", fail)
    result = check_binding("answer")
    assert result["error_code"] == "provider_auth"
    assert "private-key" not in json.dumps(result)


def test_cli_failure_classification_never_returns_raw_stderr():
    assert classify_failure("Rate limit exceeded private-key") == "cli_quota"
    assert classify_failure("Unauthorized private-key") == "cli_auth"
    assert classify_failure("Unknown model private-key") == "cli_model"
    assert classify_failure("Unrecognized error private-key") == "cli_failed"
