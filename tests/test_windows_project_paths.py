"""project_name has to work for a corpus written on more than one platform.

`project_path` is the `cwd` recorded inside the session file, so it carries
whatever separator the machine that ran the session used. The generated column
split on `/` alone, which on a Windows path finds nothing: every session became
a project named by its own absolute path, and the same repository on two
machines never grouped together.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "throughline/migrations/006_project_name_separators.sql"


def test_the_migration_exists_and_rebuilds_the_generated_column():
    assert MIGRATION.is_file()
    body = MIGRATION.read_text(encoding="utf-8")
    assert "project_name" in body
    assert "GENERATED ALWAYS AS" in body


def test_the_expression_normalises_backslashes_before_splitting():
    body = MIGRATION.read_text(encoding="utf-8")
    expression = body[body.index("GENERATED ALWAYS AS") :]
    assert "replace(" in expression
    assert re.search(r"split_part", expression)


def test_the_schema_and_the_migration_agree():
    # A migration the baseline schema does not match leaves a fresh install
    # behaving differently from an upgraded one.
    schema = (Path(__file__).resolve().parents[1] / "sql/schema.sql").read_text(encoding="utf-8")
    block = schema[schema.index("project_name text GENERATED ALWAYS AS") :][:400]
    assert "replace(" in block
