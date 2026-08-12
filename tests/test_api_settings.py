"""Unit tests for API settings — no database, no server.

The loopback guard is the only thing standing between an unauthenticated API
over the whole memory database and the local network, so it gets tested as
carefully as anything that touches data.
"""

from __future__ import annotations

import pytest

from throughline.api.settings import (
    ALLOW_REMOTE_ENV,
    LOOPBACK_HOSTS,
    RemoteBindRefused,
    Settings,
    check_bind_allowed,
)


@pytest.mark.parametrize("host", sorted(LOOPBACK_HOSTS))
def test_loopback_hosts_are_allowed(host, monkeypatch):
    monkeypatch.delenv(ALLOW_REMOTE_ENV, raising=False)
    check_bind_allowed(host)  # must not raise


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
def test_non_loopback_is_refused_by_default(host, monkeypatch):
    monkeypatch.delenv(ALLOW_REMOTE_ENV, raising=False)
    with pytest.raises(RemoteBindRefused) as exc:
        check_bind_allowed(host)
    # The message has to explain *why*, or the next person just deletes the check.
    assert "no authentication" in str(exc.value)
    assert ALLOW_REMOTE_ENV in str(exc.value)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_explicit_opt_in_allows_remote(value, monkeypatch):
    monkeypatch.setenv(ALLOW_REMOTE_ENV, value)
    check_bind_allowed("0.0.0.0")


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_falsey_opt_in_still_refuses(value, monkeypatch):
    monkeypatch.setenv(ALLOW_REMOTE_ENV, value)
    with pytest.raises(RemoteBindRefused):
        check_bind_allowed("0.0.0.0")


def test_defaults_are_loopback(monkeypatch):
    for var in ("THROUGHLINE_HOST", "THROUGHLINE_PORT", "THROUGHLINE_WEB_DIST"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert s.host in LOOPBACK_HOSTS
    # 8790, not 8787: 8787 is claimed by an unrelated launchd agent on the
    # author's machine and 8788 is the Docker publish mapping, so the native
    # server and the container can run side by side. The value matters less
    # than the three staying distinct — they reach different databases.
    assert s.port == 8790
    assert s.redact is True


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("THROUGHLINE_HOST", "::1")
    monkeypatch.setenv("THROUGHLINE_PORT", "9999")
    monkeypatch.setenv("THROUGHLINE_WEB_DIST", str(tmp_path))
    monkeypatch.setenv("THROUGHLINE_REDACT", "0")
    s = Settings.from_env()
    assert (s.host, s.port, s.redact) == ("::1", 9999, False)
    assert s.web_dist == tmp_path.resolve()
