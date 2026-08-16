"""An un-ingested source must be impossible to miss.

8,453 messages sat on disk, fully parseable, one command away — and nothing
in the product ever said so. Not the old GUI, not the new one, not `doctor`.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env, monkeypatch):
    from throughline.api import deps
    from throughline.queries import providers as Q

    # DiskCounts has no `pending` field — pending is derived inside coverage()
    # by diffing `ingestable_paths` against a live `ingestion_log` read (see
    # the module docstring). test_db's ingestion_log is empty, so all of
    # these paths diff as pending, same as test_api_providers.py's pattern.
    hermes_paths = frozenset(f"/fake/hermes/{i}.json" for i in range(33))
    vibe_paths = frozenset(f"/fake/vibe/{i}.json" for i in range(15))
    monkeypatch.setattr(
        Q,
        "_disk_scan",
        lambda: {
            "hermes": Q.DiskCounts(on_disk=33, excluded=0, present=True, ingestable_paths=hermes_paths),
            "vibe": Q.DiskCounts(on_disk=15, excluded=0, present=True, ingestable_paths=vibe_paths),
        },
    )
    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


def test_overview_raises_un_ingested_sources(client):
    items = client.get("/api/overview").json()["attention"]
    text = " ".join(i["title"] + " " + i.get("detail", "") for i in items)
    assert "Hermes" in text
    assert "33" in text


def test_the_attention_item_offers_an_action_not_just_a_complaint(client):
    items = client.get("/api/overview").json()["attention"]
    hermes = next(i for i in items if "Hermes" in i["title"])
    assert hermes["action"], "an alert with no next step is noise"
    assert hermes["action_label"]
    assert hermes["id"], "attention items are keyed by id"


def test_per_provider_ingest_jobs_exist(client):
    jobs = {j["name"] for j in client.get("/api/operate/status").json()["jobs"]}
    assert "ingest_hermes" in jobs
    assert "ingest_vibe" in jobs


def test_no_auto_ingestion_is_triggered(client):
    """Decision 2, explicitly: surfacing must never become acting."""
    before = client.get("/api/operate/status").json()["jobs"]
    client.get("/api/overview")
    after = client.get("/api/operate/status").json()["jobs"]
    assert [j.get("running") for j in before] == [j.get("running") for j in after]
