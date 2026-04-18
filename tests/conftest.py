"""Shared pytest fixtures."""

import os
import sys
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone

import pytest

# Ensure scripts/ is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skill" / "scripts"))


@pytest.fixture
def tmp_jsonl(tmp_path):
    """Create a sample Claude Code JSONL session file."""
    session_id = str(uuid.uuid4())
    ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    lines = [
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "parentUuid": None,
            "isSidechain": False,
            "message": {"role": "user", "content": "Hello Claude"},
            "timestamp": ts,
            "sessionId": session_id,
            "cwd": "/Users/test/project",
            "entrypoint": "cli",
            "gitBranch": "main",
        },
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "parentUuid": None,
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "Hi! How can I help?"}],
            },
            "timestamp": ts,
            "sessionId": session_id,
        },
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "parentUuid": None,
            "isSidechain": False,
            "message": {"role": "user", "content": "Tell me about pgvector"},
            "timestamp": ts,
            "sessionId": session_id,
        },
    ]
    jsonl_file = tmp_path / f"{session_id}.jsonl"
    with open(jsonl_file, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return jsonl_file, session_id


@pytest.fixture
def tmp_skill_dir(tmp_path):
    """Create a sample skill directory with SKILL.md."""
    skill_dir = tmp_path / "sample-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        'name: sample-skill\n'
        'description: A sample skill that does X. Trigger bei "sample", "test"\n'
        "version: 1.2.3\n"
        "---\n\n"
        "# Sample skill body\n\n"
        "Use this skill when you need to sample things.\n"
    )
    return skill_dir


@pytest.fixture
def tmp_claude_md(tmp_path):
    """Create a sample CLAUDE.md file."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Project Guidelines\n\n"
        "Always use {{project_name}} when generating commits.\n"
        "Respect the ${coding_style} defined in docs.\n"
    )
    return claude_md
