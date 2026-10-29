"""Tests for the Markdown vault exporter.

Everything here exercises the rendering layer, which is where the export
can silently lie: a tool call printed twice, a relative path linked to
the wrong place, a harness injection presented as something the person
typed, or a project file that grows past what an editor will open.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from throughline.jobs import export_markdown as em


def _msg(role: str, *, content: str = "", blocks=None, calls=None, minute: int = 0) -> dict:
    return {
        "role": role,
        "content": content,
        "content_blocks": blocks,
        "tool_calls": calls,
        "created_at": datetime(2026, 8, 13, 9, minute, tzinfo=timezone.utc),
    }


def _conv(**overrides) -> dict:
    base = {
        "id": 1,
        "session_id": "11111111-2222-3333-4444-555555555555",
        "project_name": "demo",
        "project_path": "/Users/dev/demo",
        "model": "claude-opus-5",
        "git_branch": "main",
        "started_at": datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
        "ended_at": None,
        "message_count": 2,
        "summary": "Ein Titel",
        "source_tool": "claude_code",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Paths and links                                                             #
# --------------------------------------------------------------------------- #


def test_safe_name_strips_characters_the_filesystem_rejects():
    assert em.safe_name("windsurf:plans") == "windsurf-plans"
    assert em.safe_name("a/b") == "a-b"
    assert em.safe_name("   ") == "unknown"
    assert em.safe_name(None) == "unknown"


def test_relative_tool_paths_resolve_against_the_session_cwd(tmp_path):
    project = tmp_path / "demo"
    absolute = tmp_path / "outside" / "x.py"
    assert em.resolve_path(str(Path("src") / "app.py"), str(project)) == str(project / "src" / "app.py")
    assert em.resolve_path(str(absolute), str(project)) == str(absolute)


def test_link_label_keeps_the_path_below_the_project_root(tmp_path):
    # A bare basename would render two different index.html files identically.
    project = tmp_path / "demo"
    relative = Path("web") / "index.html"
    rendered = em.link(str(relative), str(project))
    expected_url = "file://" + quote(str(project / relative), safe="/:")
    assert f"[{relative}]" in rendered
    assert expected_url in rendered


def test_link_to_a_file_outside_the_project_falls_back_to_the_basename(tmp_path):
    rendered = em.link(str(tmp_path / "outside" / "scratch.py"), str(tmp_path / "demo"))
    assert rendered.startswith("[scratch.py](")


def test_file_url_escapes_spaces_but_keeps_separators_readable():
    assert em.file_url("/Users/dev/My Notes/a.md") == "file:///Users/dev/My%20Notes/a.md"


# --------------------------------------------------------------------------- #
# Message text                                                                #
# --------------------------------------------------------------------------- #


def test_flattened_tool_stanzas_are_not_treated_as_prose():
    # Ingestion writes each tool_use into content as "[Tool: Edit]\n{json}".
    # Rendering that as prose would print every command twice.
    row = _msg("assistant", content='[Tool: Edit]\n{"file_path": "/a.py"}')
    assert em.block_text(row) == ""


def test_text_blocks_are_used_when_content_is_empty():
    row = _msg("assistant", blocks=[{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "Fertig."}])
    assert em.block_text(row) == "Fertig."


def test_thinking_blocks_never_reach_the_export():
    row = _msg("assistant", blocks=[{"type": "thinking", "thinking": "geheim"}])
    assert "geheim" not in em.block_text(row)


@pytest.mark.parametrize(
    "text",
    [
        "<system-reminder>noise</system-reminder>",
        "Base directory for this skill: /Users/dev/.claude/skills/x",
        "<command-name>/compact</command-name>",
        "This session is being continued from a previous conversation…",
    ],
)
def test_harness_injections_are_recognised(text):
    assert em.is_injected(text)


def test_a_real_prompt_is_not_mistaken_for_an_injection():
    assert not em.is_injected("Bitte exportiere den Speicher nach Obsidian.")


# --------------------------------------------------------------------------- #
# Tool calls                                                                  #
# --------------------------------------------------------------------------- #


def test_a_shell_command_renders_as_a_fenced_block_and_changes_no_file():
    call = {"tool_name": "Bash", "input": {"command": "git status", "description": "Show status"}}
    rendered, changed = em.render_tool_call(call, "/Users/dev/demo")
    assert "```bash\ngit status\n```" in rendered
    assert "Show status" in rendered
    assert changed is None


def test_a_write_reports_the_file_it_changed(tmp_path):
    call = {"tool_name": "Edit", "input": {"file_path": "src/app.py"}}
    project = tmp_path / "demo"
    rendered, changed = em.render_tool_call(call, str(project))
    assert changed == str(project / "src" / "app.py")
    assert "writes" in rendered


def test_a_read_links_the_file_without_claiming_it_changed():
    call = {"tool_name": "Read", "input": {"file_path": "src/app.py"}}
    rendered, changed = em.render_tool_call(call, "/Users/dev/demo")
    assert changed is None
    assert "reads" in rendered


def test_an_unknown_tool_still_renders_something_useful():
    call = {"tool_name": "WebSearch", "input": {"query": "pgvector hnsw"}}
    rendered, _ = em.render_tool_call(call, None)
    assert "WebSearch" in rendered and "pgvector hnsw" in rendered


def test_a_scalar_input_does_not_crash_the_renderer():
    rendered, changed = em.render_tool_call({"tool_name": "Odd", "input": "raw"}, None)
    assert rendered == "**Odd**"
    assert changed is None


# --------------------------------------------------------------------------- #
# Sessions                                                                    #
# --------------------------------------------------------------------------- #


def test_a_session_renders_prompt_answer_command_and_changed_files(tmp_path):
    messages = [
        _msg("user", content="Mach das Deployment.", minute=0),
        _msg(
            "assistant",
            content="Klar.",
            calls=[
                {"tool_name": "Bash", "input": {"command": "make deploy"}},
                {"tool_name": "Write", "input": {"file_path": "deploy.sh"}},
            ],
            minute=1,
        ),
    ]
    project = tmp_path / "demo"
    out = em.render_session(_conv(project_path=str(project)), messages, tool_output=0)

    assert out.startswith("## 2026-08-13 09:00 — Ein Titel")
    assert "`claude_code`" in out and "Model `claude-opus-5`" in out
    assert "### 09:00 · Prompt · You\n\nMach das Deployment." in out
    assert "### 09:01 · Answer · claude_code · claude-opus-5\n\nKlar." in out
    assert "make deploy" in out
    expected_url = "file://" + quote(str(project / "deploy.sh"), safe="/:")
    assert f"**Files changed:** [deploy.sh]({expected_url})" in out


def test_tool_results_stay_out_unless_asked_for():
    messages = [
        _msg("user", content="Los.", minute=0),
        _msg("tool_result", content="EIN SEHR LANGES ERGEBNIS", minute=1),
    ]
    assert "ERGEBNIS" not in em.render_session(_conv(), messages, tool_output=0)
    assert "ERGEBNIS" in em.render_session(_conv(), messages, tool_output=500)


def test_a_session_with_only_injected_user_text_is_dropped_entirely():
    messages = [_msg("user", content="<system-reminder>x</system-reminder>", minute=0)]
    assert em.render_session(_conv(), messages, tool_output=0) == ""


def test_a_session_without_a_summary_still_gets_a_heading():
    messages = [_msg("user", content="Hallo", minute=0)]
    out = em.render_session(_conv(summary=None), messages, tool_output=0)
    assert out.startswith("## 2026-08-13 09:00 — Session")


# --------------------------------------------------------------------------- #
# Files on disk                                                               #
# --------------------------------------------------------------------------- #


def test_a_small_project_stays_in_one_file(tmp_path):
    sections = [(datetime(2026, 8, i, 9, 0), f"## Sitzung {i}\n") for i in (1, 2, 3)]
    written = em.write_parts(tmp_path, "demo", sections, split_bytes=1_000_000)
    assert [p.name for p in written] == ["demo.md"]
    body = written[0].read_text(encoding="utf-8")
    assert body.index("Sitzung 1") < body.index("Sitzung 3")  # oldest first


def test_a_large_project_splits_into_indexed_parts_that_sort_chronologically(tmp_path):
    sections = [(datetime(2026, 8, i, 9, 0), "x" * 400 + "\n") for i in range(1, 11)]
    written = em.write_parts(tmp_path, "demo", sections, split_bytes=1000)

    assert len(written) > 1
    names = [p.name for p in written]
    assert names == sorted(names)  # filenames sort in the order they were written
    assert names[0].startswith("demo 01 2026-08-")


def test_splitting_never_drops_or_reorders_a_session(tmp_path):
    sections = [(datetime(2026, 8, i, 9, 0), f"## Sitzung {i:02d}\n") for i in range(1, 21)]
    written = em.write_parts(tmp_path, "demo", sections, split_bytes=100)
    combined = "".join(p.read_text(encoding="utf-8") for p in written)
    positions = [combined.index(f"Sitzung {i:02d}") for i in range(1, 21)]
    assert positions == sorted(positions)
    assert len(positions) == 20


def test_the_index_links_the_first_part_of_a_split_project(tmp_path):
    stats = [
        {
            "project": "demo",
            "entry": "demo 2026-06-13",
            "sessions": 4,
            "first": datetime(2026, 6, 13),
            "last": datetime(2026, 8, 13),
        }
    ]
    body = em.write_index(tmp_path, stats).read_text(encoding="utf-8")
    assert "[[demo/demo 2026-06-13\\|demo]]" in body


def test_memory_file_is_skipped_when_the_project_has_none(tmp_path):
    assert em.write_memory(tmp_path, "demo", []) is None
    assert not (tmp_path / "Memory.md").exists()


def test_memory_file_lists_chunks_oldest_first(tmp_path):
    chunks = [
        {"content": "Erste Erkenntnis", "category": "decision", "tags": ["db"], "created_at": datetime(2026, 1, 1)},
        {"content": "Zweite Erkenntnis", "category": "pattern", "tags": [], "created_at": datetime(2026, 2, 1)},
    ]
    body = em.write_memory(tmp_path, "demo", chunks).read_text(encoding="utf-8")
    assert body.index("Erste Erkenntnis") < body.index("Zweite Erkenntnis")
    assert "#db" in body


def test_truncation_says_how_much_it_left_out():
    out = em.truncate("y" * 100, 10)
    assert out.startswith("y" * 10)
    assert "90 more characters" in out


def test_an_unresolvable_relative_path_is_not_dressed_up_as_a_link():
    # Some adapters record a bare project name as project_path, which leaves
    # a relative tool argument unresolvable. A file:// URL built from one of
    # those points nowhere.
    relative = Path("src") / "app.swift"
    assert em.link(str(relative), "mackeyboardcleaner") == f"`{Path('mackeyboardcleaner') / relative}`"
    assert em.link(str(relative), None) == f"`{relative}`"


def test_two_parts_opening_on_the_same_day_do_not_overwrite_each_other(tmp_path):
    # A single day busy enough to fill a part twice would otherwise hand both
    # parts the same filename and silently lose the first.
    same_day = datetime(2026, 8, 9, 9, 0)
    sections = [(same_day, f"## Sitzung {i:02d}\n" + "x" * 900) for i in range(1, 6)]
    written = em.write_parts(tmp_path, "demo", sections, split_bytes=1000)

    assert len(written) == 5
    assert len({p.name for p in written}) == 5
    assert [p.name for p in written] == sorted(p.name for p in written)
    combined = "".join(p.read_text(encoding="utf-8") for p in written)
    for i in range(1, 6):
        assert f"Sitzung {i:02d}" in combined


# --------------------------------------------------------------------------- #
# Redaction                                                                   #
# --------------------------------------------------------------------------- #


def test_redaction_scrubs_secrets_out_of_prompts_and_commands():
    messages = [
        _msg("user", content="Mein Key ist sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA und mail@example.com", minute=0),
        _msg(
            "assistant",
            content="Ok.",
            calls=[{"tool_name": "Bash", "input": {"command": "export TOKEN=hunter2 && curl -s x"}}],
            minute=1,
        ),
    ]
    plain = em.render_session(_conv(), messages, tool_output=0)
    safe = em.render_session(_conv(), messages, tool_output=0, redact=True)

    assert "mail@example.com" in plain
    assert "mail@example.com" not in safe
    assert "<REDACTED" in safe
    assert "hunter2" not in safe


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the source-path redactor recognises POSIX home paths; this integration is covered by Linux CI",
)
def test_redaction_hides_the_home_directory_and_drops_the_dead_link():
    call = {"tool_name": "Write", "input": {"file_path": "src/app.py"}}
    rendered, changed = em.render_tool_call(call, "/Users/alice/demo", redact=True)

    assert "alice" not in rendered
    assert "file://" not in rendered  # a redacted path cannot resolve, so no link is offered
    # The touched-file list still carries the real path; the header redacts it on render.
    assert changed == "/Users/alice/demo/src/app.py"


def test_redaction_reaches_the_session_header_and_memory_file(tmp_path):
    messages = [_msg("user", content="Hallo", minute=0)]
    out = em.render_session(_conv(project_path="/Users/alice/demo"), messages, tool_output=0, redact=True)
    assert "alice" not in out

    chunks = [
        {
            "content": "Key sk-ant-api03-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            "category": "decision",
            "tags": [],
            "created_at": datetime(2026, 1, 1),
        }
    ]
    body = em.write_memory(tmp_path, "demo", chunks, redact=True).read_text(encoding="utf-8")
    assert "sk-ant-api03-BBBB" not in body


def test_without_the_flag_nothing_is_altered():
    messages = [_msg("user", content="/Users/alice/demo/x.py", minute=0)]
    assert "alice" in em.render_session(_conv(), messages, tool_output=0)


# --------------------------------------------------------------------------- #
# Document structure                                                          #
# --------------------------------------------------------------------------- #


def test_a_message_cannot_forge_a_session_heading():
    # An assistant report that quotes its own "## 2026-05-24 …" heading would
    # otherwise appear in the outline as a session that never happened.
    messages = [
        _msg("user", content="Bericht?", minute=0),
        _msg("assistant", content="## 2026-05-24 17:47 — Phase A", minute=1),
    ]
    out = em.render_session(_conv(), messages, tool_output=0)

    assert out.count("\n## ") == 0  # the only "## " is the section heading itself
    assert out.startswith("## 2026-08-13 09:00")
    assert "##### 2026-05-24 17:47 — Phase A" in out  # demoted, still readable


def test_headings_are_demoted_below_the_levels_the_export_owns():
    assert em.demote_headings("# Titel") == "#### Titel"
    assert em.demote_headings("## Titel") == "##### Titel"
    assert em.demote_headings("### Titel") == "###### Titel"
    assert em.demote_headings("###### Titel") == "###### Titel"  # already at the floor


def test_a_shell_comment_inside_a_fence_is_left_alone():
    # "# install deps" is a comment, not a heading. Demoting it corrupts the command.
    text = "So geht es:\n\n```bash\n# install deps\nnpm ci\n```\n"
    assert "# install deps\n" in em.demote_headings(text)


def test_a_hash_that_is_not_a_heading_is_left_alone():
    assert em.demote_headings("#nichteinetitel") == "#nichteinetitel"


def test_truncation_never_leaves_a_code_fence_open():
    # An unbalanced fence swallows the rest of the file into a code block.
    text = "Hier:\n\n```python\n" + "x = 1\n" * 500
    body = em.prose(text, False, limit=60)
    assert body.rstrip().endswith("```")
    assert len([ln for ln in body.split("\n") if ln.startswith("```")]) % 2 == 0


# --------------------------------------------------------------------------- #
# Grouping into folders                                                       #
# --------------------------------------------------------------------------- #


def test_project_names_that_share_a_folder_are_merged_not_overwritten():
    # project_name is a generated column: it is 'unknown' when project_path is
    # NULL and '' when the path ends in a separator. Both land in the folder
    # "unknown", so keying the export by the raw name lets the second group
    # overwrite the first — losing every session in it.
    convs = [
        _conv(id=1, project_name="", started_at=datetime(2026, 3, 20, 11, 37)),
        _conv(id=2, project_name="unknown", started_at=datetime(2026, 7, 22, 0, 29)),
    ]
    grouped = em.group_by_folder(convs)

    assert list(grouped) == ["unknown"]
    assert [c["id"] for c in grouped["unknown"]] == [1, 2]


def test_a_merged_folder_stays_in_chronological_order():
    convs = [
        _conv(id=1, project_name="", started_at=datetime(2026, 7, 22, 0, 29)),
        _conv(id=2, project_name="unknown", started_at=datetime(2026, 3, 20, 11, 37)),
    ]
    stamps = [c["started_at"] for c in em.group_by_folder(convs)["unknown"]]
    assert stamps == sorted(stamps)


def test_distinct_projects_keep_their_own_folders():
    convs = [_conv(id=1, project_name="alpha"), _conv(id=2, project_name="beta")]
    assert sorted(em.group_by_folder(convs)) == ["alpha", "beta"]


# --------------------------------------------------------------------------- #
# The export as a whole                                                       #
# --------------------------------------------------------------------------- #


def _loaders(messages_by_id, memory=None):
    """Stub loaders so the export can be driven without a database."""
    return (lambda cid: messages_by_id.get(cid, []), lambda name: (memory or {}).get(name, []))


def test_export_writes_one_folder_per_project_and_reports_what_it_wrote(tmp_path):
    convs = [_conv(id=1, project_name="alpha"), _conv(id=2, project_name="beta")]
    load_messages, load_memory = _loaders({1: [_msg("user", content="A")], 2: [_msg("user", content="B")]})

    summary = em.export_corpus(tmp_path, convs, load_messages, load_memory)

    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == ["alpha", "beta"]
    assert (tmp_path / "README.md").exists()
    assert summary["projects"] == 2
    assert summary["sessions"] == 2
    assert summary["files"] == 3  # two project files plus the index
    assert [p["project"] for p in summary["per_project"]] == ["alpha", "beta"]


def test_export_merges_projects_that_share_a_folder_instead_of_losing_one(tmp_path):
    convs = [
        _conv(id=1, project_name="", started_at=datetime(2026, 3, 20, 11, 37)),
        _conv(id=2, project_name="unknown", started_at=datetime(2026, 7, 22, 0, 29)),
    ]
    load_messages, load_memory = _loaders({1: [_msg("user", content="ERSTE")], 2: [_msg("user", content="ZWEITE")]})

    summary = em.export_corpus(tmp_path, convs, load_messages, load_memory)

    body = (tmp_path / "unknown" / "unknown.md").read_text(encoding="utf-8")
    assert "ERSTE" in body and "ZWEITE" in body
    assert body.index("ERSTE") < body.index("ZWEITE")
    assert summary["sessions"] == 2


def test_export_skips_sessions_that_render_to_nothing(tmp_path):
    convs = [_conv(id=1, project_name="alpha"), _conv(id=2, project_name="alpha")]
    load_messages, load_memory = _loaders(
        {1: [_msg("user", content="Echt")], 2: [_msg("user", content="<system-reminder>x</system-reminder>")]}
    )
    summary = em.export_corpus(tmp_path, convs, load_messages, load_memory)
    assert summary["sessions"] == 1


def test_export_collects_memory_from_every_name_that_shares_the_folder(tmp_path):
    convs = [_conv(id=1, project_name=""), _conv(id=2, project_name="unknown")]
    memory = {
        "": [{"content": "Aus der leeren", "category": "note", "tags": [], "created_at": datetime(2026, 1, 1)}],
        "unknown": [{"content": "Aus unknown", "category": "note", "tags": [], "created_at": datetime(2026, 2, 1)}],
    }
    load_messages, load_memory = _loaders({1: [_msg("user", content="A")], 2: [_msg("user", content="B")]}, memory)

    em.export_corpus(tmp_path, convs, load_messages, load_memory)

    body = (tmp_path / "unknown" / "Memory.md").read_text(encoding="utf-8")
    assert "Aus der leeren" in body and "Aus unknown" in body


def test_export_refuses_to_write_outside_a_directory_it_can_create(tmp_path):
    blocker = tmp_path / "wall"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        em.export_corpus(blocker / "out", [_conv()], *_loaders({1: [_msg("user", content="A")]}))


# --------------------------------------------------------------------------- #
# Who said what                                                               #
# --------------------------------------------------------------------------- #


def test_the_answering_model_is_named_per_turn_not_per_session():
    # A long session switches models; the session header cannot say which one
    # produced a given answer.
    conv = _conv(model="claude-opus-5")
    fast = _msg("assistant", content="Kurz.", minute=1)
    fast["model"] = "claude-haiku-4-5-20251001"
    assert em.speaker(conv, fast) == "claude_code · claude-haiku-4-5-20251001"


def test_a_turn_without_its_own_model_falls_back_to_the_session():
    assert em.speaker(_conv(model="claude-opus-5"), _msg("assistant")) == "claude_code · claude-opus-5"


def test_a_placeholder_model_is_not_presented_as_one():
    conv = _conv(model=None)
    msg = _msg("assistant")
    msg["model"] = "<synthetic>"
    assert em.speaker(conv, msg) == "claude_code"


def test_an_unknown_tool_and_model_still_yield_a_readable_speaker():
    assert em.speaker(_conv(model=None, source_tool=None), _msg("assistant")) == "KI"


def test_prompt_answer_and_execution_are_separately_labelled():
    messages = [
        _msg("user", content="Bau das.", minute=0),
        _msg(
            "assistant",
            content="Mache ich.",
            calls=[{"tool_name": "Bash", "input": {"command": "make"}}],
            minute=1,
        ),
    ]
    out = em.render_session(_conv(), messages, tool_output=0)

    assert "### 09:00 · Prompt · You" in out
    assert "### 09:01 · Answer · claude_code · claude-opus-5" in out
    assert "### 09:01 · Execution · claude_code · claude-opus-5" in out
    # and in that order
    assert out.index("· Prompt ·") < out.index("· Answer ·") < out.index("· Execution ·")


def test_a_turn_that_only_acts_gets_an_execution_heading_and_no_empty_answer():
    messages = [
        _msg("user", content="Los.", minute=0),
        _msg("assistant", calls=[{"tool_name": "Bash", "input": {"command": "ls"}}], minute=1),
    ]
    out = em.render_session(_conv(), messages, tool_output=0)
    assert "· Execution ·" in out
    assert "· Answer ·" not in out


# --------------------------------------------------------------------------- #
# Where the export is allowed to write                                        #
# --------------------------------------------------------------------------- #


def test_the_destination_must_be_absolute(tmp_path):
    with pytest.raises(ValueError, match="absolute path"):
        em.resolve_destination("relativ/pfad", root=tmp_path)


def test_an_empty_destination_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="No destination"):
        em.resolve_destination("   ", root=tmp_path)


def test_a_destination_inside_the_allowed_root_is_accepted(tmp_path):
    target = tmp_path / "Obsidian" / "Throughline"
    assert em.resolve_destination(str(target), root=tmp_path) == target


def test_a_destination_outside_the_allowed_root_is_refused(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        em.resolve_destination(str(tmp_path.parent / "outside-throughline-export"), root=tmp_path)


def test_a_traversal_back_out_of_the_root_is_refused(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        em.resolve_destination(str(tmp_path / ".." / "anderswo"), root=tmp_path)


def test_a_destination_that_is_an_existing_file_is_refused(tmp_path):
    victim = tmp_path / "notizen.md"
    victim.write_text("wichtig", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        em.resolve_destination(str(victim), root=tmp_path)


def test_a_tilde_is_expanded_against_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert em.resolve_destination("~/Vault", root=tmp_path) == tmp_path / "Vault"


def test_the_author_label_is_configurable_and_not_a_hard_coded_name(monkeypatch):
    # A published tool must not carry one person's name in its output.
    messages = [_msg("user", content="Hallo", minute=0)]
    assert "· Prompt · You" in em.render_session(_conv(), messages, tool_output=0)

    monkeypatch.setenv("THROUGHLINE_AUTHOR", "Alex")
    assert "· Prompt · Alex" in em.render_session(_conv(), messages, tool_output=0)


def test_an_export_that_fails_halfway_says_so_instead_of_looking_complete(tmp_path):
    # A partial export leaves real files behind. What it must not leave behind
    # is an index that reads like a finished one — the previous run's index,
    # or a fresh one listing only what happened to get written.
    convs = [_conv(id=1, project_name="alpha"), _conv(id=2, project_name="beta")]

    def exploding_loader(cid):
        if cid == 2:
            raise OSError("No space left on device")
        return [_msg("user", content="A")]

    with pytest.raises(OSError):
        em.export_corpus(tmp_path, convs, exploding_loader, lambda name: [])

    index = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "incomplete" in index.lower()
    assert "No space left on device" in index


def test_a_completed_export_is_not_labelled_incomplete(tmp_path):
    convs = [_conv(id=1, project_name="alpha")]
    em.export_corpus(tmp_path, convs, lambda cid: [_msg("user", content="A")], lambda name: [])
    assert "incomplete" not in (tmp_path / "README.md").read_text(encoding="utf-8").lower()


def test_a_failed_export_keeps_what_it_managed_to_write(tmp_path):
    convs = [_conv(id=1, project_name="alpha"), _conv(id=2, project_name="beta")]

    def exploding_loader(cid):
        if cid == 2:
            raise OSError("boom")
        return [_msg("user", content="GERETTET")]

    with pytest.raises(OSError):
        em.export_corpus(tmp_path, convs, exploding_loader, lambda name: [])

    assert "GERETTET" in (tmp_path / "alpha" / "alpha.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Re-running the export                                                       #
# --------------------------------------------------------------------------- #


def test_a_second_run_updates_in_place_and_creates_no_duplicates(tmp_path):
    convs = [_conv(id=1, project_name="alpha")]
    loaders = _loaders({1: [_msg("user", content="A")]})

    em.export_corpus(tmp_path, convs, *loaders)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    em.export_corpus(tmp_path, convs, *loaders)
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())

    assert before == after


def test_an_unchanged_file_is_not_rewritten(tmp_path):
    # The destination is often a synced folder. Rewriting seventeen megabytes
    # of identical bytes makes the sync client upload all of it again.
    convs = [_conv(id=1, project_name="alpha")]
    loaders = _loaders({1: [_msg("user", content="A")]})

    em.export_corpus(tmp_path, convs, *loaders)
    target = tmp_path / "alpha" / "alpha.md"
    stamp = target.stat().st_mtime_ns
    target.chmod(0o444)  # a rewrite would fail loudly rather than pass quietly

    summary = em.export_corpus(tmp_path, convs, *loaders)

    assert target.stat().st_mtime_ns == stamp
    assert summary["unchanged"] >= 1
    target.chmod(0o644)


def test_a_stale_part_from_an_earlier_run_is_removed(tmp_path):
    # A project that split into three parts and now needs two must not leave
    # the third behind: it is not in the index, and it reads as current.
    big = [_conv(id=i, project_name="alpha", started_at=datetime(2026, 8, i, 9, 0)) for i in range(1, 6)]
    messages = {i: [_msg("user", content="x" * 900)] for i in range(1, 6)}
    em.export_corpus(tmp_path, big, *_loaders(messages), split_bytes=1000)
    parts_before = sorted(p.name for p in (tmp_path / "alpha").glob("alpha *.md"))
    assert len(parts_before) > 2

    summary = em.export_corpus(tmp_path, big[:1], *_loaders(messages), split_bytes=1000)

    assert sorted(p.name for p in (tmp_path / "alpha").glob("*.md")) == ["alpha.md"]
    assert summary["removed"] == len(parts_before)


def test_a_project_that_left_the_corpus_takes_its_files_with_it(tmp_path):
    convs = [_conv(id=1, project_name="alpha"), _conv(id=2, project_name="beta")]
    loaders = _loaders({1: [_msg("user", content="A")], 2: [_msg("user", content="B")]})
    em.export_corpus(tmp_path, convs, *loaders)
    assert (tmp_path / "beta" / "beta.md").exists()

    em.export_corpus(tmp_path, convs[:1], *loaders)

    assert not (tmp_path / "beta" / "beta.md").exists()


def test_a_file_the_export_did_not_write_is_never_touched(tmp_path):
    # The destination can be a vault the person also keeps their own notes in.
    convs = [_conv(id=1, project_name="alpha")]
    loaders = _loaders({1: [_msg("user", content="A")]})
    em.export_corpus(tmp_path, convs, *loaders)

    mine = tmp_path / "alpha" / "Meine Notizen.md"
    mine.write_text("von Hand geschrieben", encoding="utf-8")
    stray = tmp_path / "Inbox.md"
    stray.write_text("auch meins", encoding="utf-8")

    em.export_corpus(tmp_path, [], *loaders)

    assert mine.read_text(encoding="utf-8") == "von Hand geschrieben"
    assert stray.read_text(encoding="utf-8") == "auch meins"


def test_the_manifest_is_not_reported_as_a_project(tmp_path):
    convs = [_conv(id=1, project_name="alpha")]
    summary = em.export_corpus(tmp_path, convs, *_loaders({1: [_msg("user", content="A")]}))
    assert summary["projects"] == 1
    assert (tmp_path / em.MANIFEST_NAME).is_file()


def test_memory_chunks_with_the_same_timestamp_keep_a_stable_order(tmp_path):
    # Ordering by created_at alone leaves ties to the database's whim, so a
    # re-run rewrites Memory.md with the same content in a different order —
    # churn that looks like a change and is not one.
    same = datetime(2026, 1, 1, 12, 0)
    chunks = [
        {"id": 7, "content": "sieben", "category": "note", "tags": [], "created_at": same},
        {"id": 3, "content": "drei", "category": "note", "tags": [], "created_at": same},
        {"id": 5, "content": "fünf", "category": "note", "tags": [], "created_at": same},
    ]
    convs = [_conv(id=1, project_name="alpha")]
    loaders = (lambda cid: [_msg("user", content="A")], lambda name: list(chunks))

    em.export_corpus(tmp_path, convs, *loaders)
    first = (tmp_path / "alpha" / "Memory.md").read_text(encoding="utf-8")

    chunks.reverse()  # the database hands them back in another order
    summary = em.export_corpus(tmp_path, convs, *loaders)

    assert (tmp_path / "alpha" / "Memory.md").read_text(encoding="utf-8") == first
    assert summary["written"] == 0
