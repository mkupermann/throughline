"""Projects are identified by name, not by a registry row.

`projects` is enrichment (description, status, contacts) and lags reality
badly: 53 registered rows against 81 names actually in use on the author's
database. Keying retrieval and detail on `projects.id` therefore hid most of
a user's projects even though their memory was searchable.
"""

from __future__ import annotations

import pytest

from throughline.queries import find as F, skills as S

pytestmark = pytest.mark.integration


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations (session_id, project_path, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/repo/registered', now(), 1, 'x'),
                   (gen_random_uuid(), '/repo/unregistered', now(), 1, 'y')
            """
        )
        cur.execute(
            """
            INSERT INTO memory_chunks (source_type, content, category, project_name)
            VALUES ('manual', 'a note about registered',   'insight', 'registered'),
                   ('manual', 'a note about unregistered', 'insight', 'unregistered')
            """
        )
        # Only one of the two projects has a registry row.
        cur.execute(
            "INSERT INTO projects (name, description, status) "
            "VALUES ('registered', 'has a registry row', 'active')"
        )
    db_connection.commit()
    return db_connection


def test_unregistered_project_is_listed(corpus):
    names = {r["title"] for r in F.browse(corpus, F.FindFilters(kinds=["project"]), limit=50).items}
    assert "unregistered" in names, "a project with memory but no registry row vanished"
    assert "registered" in names


def test_unregistered_project_is_searchable(corpus):
    res = F.find(corpus, "unregistered", filters=F.FindFilters(kinds=["project"]), limit=20)
    assert any(i["title"] == "unregistered" for i in res.items)


def test_registered_project_carries_its_id_unregistered_does_not(corpus):
    by_name = {r["title"]: r for r in F.browse(corpus, F.FindFilters(kinds=["project"]), limit=50).items}
    assert by_name["registered"]["id"] > 0
    assert by_name["unregistered"]["id"] == 0, "there is no registry id to report"


def test_detail_by_name_works_for_both(corpus):
    reg = S.get_project_by_name(corpus, "registered")
    unreg = S.get_project_by_name(corpus, "unregistered")

    assert reg is not None and reg["registered"] is True
    assert reg["description"] == "has a registry row"

    assert unreg is not None, "an unregistered project must still resolve"
    assert unreg["registered"] is False
    assert unreg["description"] is None


def test_detail_by_name_reports_activity(corpus):
    r = S.get_project_by_name(corpus, "unregistered")
    assert r["chunks_count"] == 1
    assert r["conversations_count"] == 1
    assert r["last_activity"] is not None


def test_unknown_project_name_is_none(corpus):
    assert S.get_project_by_name(corpus, "no-such-project") is None


def test_observed_names_cover_every_project_with_data(corpus):
    observed = {r["name"] for r in S.observed_project_names(corpus)}
    registered = {r["name"] for r in S.list_projects(corpus)}
    assert {"registered", "unregistered"} <= observed
    assert registered < observed, "the registry should be a subset of what exists"
