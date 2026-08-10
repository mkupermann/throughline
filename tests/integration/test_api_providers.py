"""Coverage answers 'what exists, what is imported' — the question nothing asked.

8,453 messages sat on disk fully parseable, one command away, and no surface
in the product ever said so.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env):
    from throughline.api import deps

    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


def test_every_provider_is_reported(client):
    body = client.get("/api/providers").json()
    names = {p["name"] for p in body["providers"]}
    assert {"claude_code", "windsurf", "hermes", "vibe", "cline", "codex"} <= names


def test_row_shape(client):
    p = client.get("/api/providers").json()["providers"][0]
    assert set(p) >= {
        "name", "label", "on_disk", "pending", "excluded", "ingested", "last_run", "status",
    }


def test_status_derives_from_pending_not_from_ingested(client):
    """Spec §4.3: ingested can legitimately exceed on_disk, because files rotate."""
    rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    for p in rows.values():
        if p["status"] == "ok":
            assert p["pending"] == 0
        if p["pending"] > 0 and p["ingested"] > 0:
            assert p["status"] == "pending"


def test_a_source_with_files_and_no_rows_is_not_ingested(client, monkeypatch):
    from throughline.queries import providers as Q

    fake_paths = frozenset(f"/fake/hermes/{i}.json" for i in range(33))
    monkeypatch.setattr(
        Q, "_disk_scan",
        lambda: {"hermes": Q.DiskCounts(on_disk=33, excluded=0, present=True,
                                         ingestable_paths=fake_paths)},
    )
    # test_db's ingestion_log is empty, so all 33 fake paths diff as pending —
    # pending is computed live against the caller's connection (Finding 1),
    # not carried on DiskCounts anymore.
    rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    assert rows["hermes"]["status"] == "not_ingested"
    assert rows["hermes"]["pending"] == 33


def test_an_installed_source_with_no_files_reports_no_data(client, monkeypatch):
    """§4.4: cline has a directory but contributes nothing."""
    from throughline.queries import providers as Q

    monkeypatch.setattr(
        Q, "_disk_scan",
        lambda: {"cline": Q.DiskCounts(on_disk=0, excluded=0, present=False)},
    )
    rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    assert rows["cline"]["status"] == "no_data"


def test_a_broken_adapter_does_not_read_as_no_data(client, monkeypatch):
    """Finding 2: a scan failure must not look like 'nothing here'.

    A permission error or an adapter bug is more likely in practice than a
    genuinely empty source, and the two must not render identically.
    """
    from throughline.queries import providers as Q

    class _BrokenAdapter:
        name = "codex"

        def discover_all(self):
            raise RuntimeError("permission denied")

        def discover(self):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(Q, "all_adapters", lambda: [_BrokenAdapter()])
    Q.invalidate_scan_cache()
    try:
        rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    finally:
        Q.invalidate_scan_cache()  # don't leak the broken scan into later tests
    assert rows["codex"]["status"] != "no_data"
    assert rows["codex"]["status"] == "unknown"


def test_present_with_nothing_importable_is_not_ok(client, monkeypatch):
    """Finding 3: files on disk that were 100% excluded must not read 'ok'.

    `present=True, pending=0, ingested=0` previously fell through to "ok",
    which claims "fully synced" when in truth nothing was ever imported.
    """
    from throughline.queries import providers as Q

    monkeypatch.setattr(
        Q, "_disk_scan",
        lambda: {"cline": Q.DiskCounts(on_disk=12, excluded=12, present=True)},
    )
    rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    assert rows["cline"]["pending"] == 0
    assert rows["cline"]["status"] != "ok"
    assert rows["cline"]["status"] == "no_data"


def test_unattributed_rows_are_surfaced_not_hidden(client, db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (session_id, project_path, started_at, message_count) "
            "VALUES (gen_random_uuid(), '/u', now(), 1)"
        )
    db_connection.commit()
    rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    assert "(unattributed)" in rows or any(
        p["label"] == "(unattributed)" for p in rows.values()
    )


def test_the_scan_is_cached(monkeypatch):
    """§4.5: it changes when you ingest, not per request, and Overview polls."""
    from throughline.queries import providers as Q

    calls = []
    monkeypatch.setattr(Q, "_scan_uncached", lambda: (calls.append(1), {})[1])
    Q.invalidate_scan_cache()
    Q._disk_scan()
    Q._disk_scan()
    assert len(calls) == 1
