"""Smoke test — every script should be importable without side effects."""

import importlib.util
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "scripts"
SKILL_SCRIPT_DIR = ROOT / "skill" / "scripts"


@pytest.mark.parametrize(
    "filename",
    [
        "ingest_sessions.py",
        "scan_skills.py",
        "scan_prompts.py",
        "extract_memory.py",
        "extract_entities.py",
        "generate_titles.py",
        "generate_embeddings.py",
        "search_semantic.py",
        "context_preload.py",
        "graph_query.py",
        "reflect_memory.py",
        "ingest_windsurf.py",
    ],
)
def test_script_imports(filename):
    """Each script under scripts/ should import without errors."""
    path = SCRIPT_DIR / filename
    assert path.exists(), f"Script file missing: {filename}"
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "filename",
    ["query.py", "add.py"],
)
def test_skill_scripts_import(filename):
    """Skill utility scripts should also import cleanly."""
    path = SKILL_SCRIPT_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present in skill/scripts/")
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
