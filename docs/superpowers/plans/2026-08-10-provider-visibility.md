# Provider Visibility and a Working Timeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all nine AI-CLI providers visible and filterable everywhere in Throughline, and replace the paginated pseudo-Timeline with a real date-range aggregate.

**Architecture:** A new `conversations.source_tool` column carries provider identity (distinct from `entrypoint`, which keeps meaning *how* a tool was invoked). One registry module (`throughline/providers.py`) defines the nine providers once. A coverage endpoint (`/api/providers`) answers "what exists on disk vs. what is imported" for three UI consumers. The Timeline gets its own bucketed aggregate endpoint so it no longer inherits search pagination. Provider is an app-scope URL parameter (`?provider=`) rendered by two controls — a global bar and a Find facet — that read and write the same state.

**Tech Stack:** Python 3.13 · PostgreSQL 16 (pgvector 0.8.0) · psycopg2 · FastAPI · pytest · React 19 + TypeScript + Vite + Tailwind v4 · Vitest

## Global Constraints

- **The nine provider names are exactly:** `claude_code`, `windsurf`, `hermes`, `codex`, `continue`, `cline`, `vibe`, `cursor`, `zed`. Copy this list verbatim; do not invent variants.
- **`entrypoint` semantics and existing values must not change** (spec §8). It stays the home of `cli` vs `sdk-cli`.
- **Never auto-ingest** (spec decision 2). Every ingest path stays user-initiated.
- **The 8 unattributed conversations stay NULL** (spec §3.3). Guessing `vibe` for them is a fabrication that hardens into fact.
- **Task 5 is atomic.** The recursive glob and the subagent exclusion ship in one commit, never two (spec §9.1). `ingest --all` is safe today *because* the glob is non-recursive; shipping `rglob` alone introduces silent data loss.
- **Migrations are idempotent.** Each `UPDATE` is guarded by `source_tool IS NULL` so a re-run changes zero rows.
- **Provider is app-scope; category/tag/confidence are Find-local** (spec §4.2). The provider bar is hidden on Console.
- Run Python tests with `python3 -m pytest`. Integration tests need a live database and are marked `@pytest.mark.integration`.
- Frontend tests run with `npm test` from `web/`.

---

### Task 1: Provider registry

Provider identity defined once, so the API, the UI and `conflicts.py` stop re-deriving it separately (spec §3.5).

**Files:**
- Create: `throughline/providers.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: nothing (foundation task)
- Produces: `PROVIDERS: tuple[Provider, ...]`, `Provider` dataclass with fields `name: str`, `label: str`, `chart_slot: int`; `NAMES: frozenset[str]`; `by_name(name: str) -> Provider | None`; `label_for(name: str | None) -> str`

- [ ] **Step 1: Write the failing test**

```python
"""The provider registry is the single definition of provider identity."""

from __future__ import annotations

from throughline import providers as P
from throughline.adapters.registry import all_adapters


def test_registry_lists_exactly_the_nine_providers():
    assert P.NAMES == {
        "claude_code", "windsurf", "hermes", "codex", "continue",
        "cline", "vibe", "cursor", "zed",
    }


def test_registry_matches_the_installed_adapters():
    """A new adapter must not be able to exist without a provider entry.

    This is the assertion that keeps the registry honest as adapters are
    added — the failure mode it prevents is a provider that ingests data
    and then renders as 'unknown' forever.
    """
    assert {a.name for a in all_adapters()} == P.NAMES


def test_every_provider_has_a_distinct_label():
    labels = [p.label for p in P.PROVIDERS]
    assert len(set(labels)) == len(labels)
    assert P.by_name("claude_code").label == "Claude Code"


def test_unknown_and_none_render_as_unattributed():
    assert P.label_for(None) == "(unattributed)"
    assert P.label_for("no_such_tool") == "no_such_tool"


def test_by_name_returns_none_for_unknown():
    assert P.by_name("no_such_tool") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'throughline.providers'`

- [ ] **Step 3: Write minimal implementation**

```python
"""The nine providers, defined once.

Provider identity was previously re-derived in three places — the API, the
UI, and ``conflicts.py`` — from ``conversations.entrypoint``, a column that
means different things depending on which adapter wrote it. That divergence
is the root cause in the design spec §1.1. This module is the single answer
to "which tools does Throughline unify?".

``chart_slot`` indexes the validated six-slot chart palette. Nine providers
against six hues is a real constraint, so slots repeat; provider chips carry
a text label as well, and the Timeline deliberately uses intensity rather
than hue (spec §5.2) so nothing depends on nine distinct colours existing.
"""

from __future__ import annotations

from dataclasses import dataclass

UNATTRIBUTED_LABEL = "(unattributed)"


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    chart_slot: int


PROVIDERS: tuple[Provider, ...] = (
    Provider("claude_code", "Claude Code", 1),
    Provider("windsurf", "Windsurf", 2),
    Provider("hermes", "Hermes", 3),
    Provider("codex", "Codex", 4),
    Provider("continue", "Continue", 5),
    Provider("cline", "Cline", 6),
    Provider("vibe", "Vibe", 1),
    Provider("cursor", "Cursor", 2),
    Provider("zed", "Zed", 3),
)

NAMES: frozenset[str] = frozenset(p.name for p in PROVIDERS)

_BY_NAME: dict[str, Provider] = {p.name: p for p in PROVIDERS}


def by_name(name: str) -> Provider | None:
    return _BY_NAME.get(name)


def label_for(name: str | None) -> str:
    """Display label. NULL is a state we render, not one we hide."""
    if name is None or name == "":
        return UNATTRIBUTED_LABEL
    p = _BY_NAME.get(name)
    return p.label if p else name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_providers.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add throughline/providers.py tests/test_providers.py
git commit -m "feat(providers): add the provider registry as one source of identity"
```

---

### Task 2: `source_tool` column and backfill migration

Spec §3.1 and §3.3. Satisfies acceptance bars 1 and 2.

**Files:**
- Create: `sql/migrations/002_source_tool.sql`
- Create: `tests/integration/test_migration_source_tool.py`

**Interfaces:**
- Consumes: `throughline.providers.NAMES` (Task 1) — the migration hardcodes the same nine names in SQL; the test asserts the two agree
- Produces: `conversations.source_tool text` (nullable, btree-indexed)

- [ ] **Step 1: Write the failing test**

```python
"""The source_tool backfill: correct per rule, idempotent, honest about NULL."""

from __future__ import annotations

from pathlib import Path

import pytest

from throughline import providers as P

pytestmark = pytest.mark.integration

MIGRATION = Path(__file__).resolve().parents[2] / "sql" / "migrations" / "002_source_tool.sql"


def _apply(conn):
    with conn.cursor() as cur:
        cur.execute(MIGRATION.read_text())
    conn.commit()


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS source_tool")
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, entrypoint, started_at, message_count, metadata)
            VALUES
                (gen_random_uuid(), '/a', 'sdk-cli',      now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/b', 'cli',          now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/c', 'windsurf',     now(), 1, '{"source":"windsurf"}'::jsonb),
                (gen_random_uuid(), '/d', 'continue.dev', now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/e', 'zed',          now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/f', '',             now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/g', NULL,           now(), 1, '{}'::jsonb)
            """
        )
    db_connection.commit()
    return db_connection


def _tool(conn, project_path: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_tool FROM conversations WHERE project_path=%s", (project_path,)
        )
        return cur.fetchone()[0]


def test_claude_code_entrypoints_become_claude_code(corpus):
    """Bar 1: the 3,016 rows report claude_code, not cli/sdk-cli."""
    _apply(corpus)
    assert _tool(corpus, "/a") == "claude_code"
    assert _tool(corpus, "/b") == "claude_code"


def test_metadata_source_wins_when_it_names_a_known_adapter(corpus):
    _apply(corpus)
    assert _tool(corpus, "/c") == "windsurf"


def test_continue_dev_maps_to_the_adapter_name(corpus):
    _apply(corpus)
    assert _tool(corpus, "/d") == "continue"


def test_entrypoint_matching_an_adapter_is_taken_literally(corpus):
    _apply(corpus)
    assert _tool(corpus, "/e") == "zed"


def test_genuinely_unknown_rows_stay_null(corpus):
    """Spec §3.3: labelling these would be a fabrication that hardens into fact."""
    _apply(corpus)
    assert _tool(corpus, "/f") is None
    assert _tool(corpus, "/g") is None


def test_every_backfilled_value_is_a_registered_provider(corpus):
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute("SELECT DISTINCT source_tool FROM conversations WHERE source_tool IS NOT NULL")
        found = {r[0] for r in cur.fetchall()}
    assert found <= P.NAMES


def test_rerunning_the_migration_changes_zero_rows(corpus):
    """Bar 2. Guarded by `source_tool IS NULL` on every UPDATE."""
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute("SELECT id, source_tool FROM conversations ORDER BY id")
        before = cur.fetchall()
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute("SELECT id, source_tool FROM conversations ORDER BY id")
        after = cur.fetchall()
    assert before == after


def test_the_column_is_indexed(corpus):
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_indexes "
            "WHERE tablename='conversations' AND indexname='idx_conversations_source_tool'"
        )
        assert cur.fetchone() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/integration/test_migration_source_tool.py -v`
Expected: FAIL — the migration file does not exist (`FileNotFoundError`)

- [ ] **Step 3: Write the migration**

```sql
-- 002_source_tool.sql
--
-- Adds conversations.source_tool: WHICH tool produced this conversation.
--
-- This is deliberately a new column rather than a cleanup of `entrypoint`,
-- because `entrypoint` already has a correct and different meaning — HOW the
-- tool was invoked (`cli` vs `sdk-cli`). Conflating the two is the root cause
-- documented in the design spec §1.1: claude_code passed Claude's own
-- entrypoint through, so 98% of the corpus was unrecognisable as Claude Code,
-- and conflicts.py has been reporting false cross-tool conflicts between
-- Claude Code and itself.
--
-- Nullable on purpose. NULL means "genuinely unknown" and the UI renders it as
-- "(unattributed)" rather than hiding it. The rows that end up NULL here
-- predate any Vibe files on disk; labelling them `vibe` would be a fabrication
-- that hardens into fact.
--
-- Idempotent: every UPDATE is guarded by `source_tool IS NULL`, so re-running
-- changes zero rows.

ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS source_tool text;

CREATE INDEX IF NOT EXISTS idx_conversations_source_tool
    ON public.conversations USING btree (source_tool);

-- Rule 1 — an explicit metadata.source that names a known adapter.
UPDATE public.conversations
SET source_tool = metadata->>'source'
WHERE source_tool IS NULL
  AND metadata->>'source' IN (
      'claude_code','windsurf','hermes','codex','continue','cline','vibe','cursor','zed'
  );

-- Rule 2 — Claude Code's own entrypoint values.
UPDATE public.conversations
SET source_tool = 'claude_code'
WHERE source_tool IS NULL
  AND entrypoint IN ('cli','sdk-cli');

-- Rule 3 — entrypoint already naming an adapter.
UPDATE public.conversations
SET source_tool = entrypoint
WHERE source_tool IS NULL
  AND entrypoint IN (
      'claude_code','windsurf','hermes','codex','continue','cline','vibe','cursor','zed'
  );

-- Rule 3b — the one adapter whose entrypoint does not match its name.
UPDATE public.conversations
SET source_tool = 'continue'
WHERE source_tool IS NULL
  AND entrypoint = 'continue.dev';

-- Everything else stays NULL, deliberately.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/integration/test_migration_source_tool.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Apply the migration to the real database and verify bar 1**

```bash
python3 scripts/migrate.py --status
python3 scripts/migrate.py
psql -d claude_memory -c \
  "SELECT COALESCE(source_tool,'(null)') AS tool, count(*) FROM conversations GROUP BY 1 ORDER BY 2 DESC"
```

Expected: `claude_code` ≈ 3016, `windsurf` 34, `(null)` 8. No row reports `cli` or `sdk-cli`.

- [ ] **Step 6: Commit**

```bash
git add sql/migrations/002_source_tool.sql tests/integration/test_migration_source_tool.py
git commit -m "feat(db): add conversations.source_tool with an idempotent backfill"
```

---

### Task 3: Every adapter writes its own name

Spec §3.2. Satisfies acceptance bar 3.

**Files:**
- Modify: `throughline/adapters/base.py` — add `source_tool` to `NormalisedConversation`
- Modify: `throughline/adapters/writer.py:55-80` — persist it in `_upsert_conversation`
- Modify: `throughline/adapters/claude_code.py:159`, `vibe.py:310`, `hermes.py:209,316`, `windsurf.py:57`, `codex.py:202`, `continue_dev.py:136,203`, `cline.py:244`, `cursor.py:210`, `zed.py:184`
- Test: `tests/test_adapters_write_source_tool.py`

**Interfaces:**
- Consumes: `throughline.providers.NAMES` (Task 1); `conversations.source_tool` (Task 2)
- Produces: `NormalisedConversation.source_tool: str | None` — set by every adapter to its own `name`

- [ ] **Step 1: Write the failing test**

```python
"""Table-driven over all nine adapters: each writes its own name.

Written table-driven rather than as nine separate tests so that a tenth
adapter cannot be added without this failing — the omission this guards
against is exactly how `vibe` shipped writing an empty string and `hermes`
shipped trusting a payload field.
"""

from __future__ import annotations

import inspect

import pytest

from throughline import providers as P
from throughline.adapters.base import NormalisedConversation
from throughline.adapters.registry import all_adapters


def test_normalised_conversation_carries_source_tool():
    assert "source_tool" in inspect.signature(NormalisedConversation).parameters


@pytest.mark.parametrize("adapter", all_adapters(), ids=lambda a: a.name)
def test_adapter_sets_source_tool_to_its_own_name(adapter):
    """Bar 3: no adapter may leave provider identity to chance."""
    src = inspect.getsource(type(adapter))
    assert "source_tool=" in src, (
        f"{adapter.name} never sets source_tool; provider identity would be "
        f"NULL for everything it ingests"
    )
    assert f'source_tool="{adapter.name}"' in src or "source_tool=self.name" in src, (
        f"{adapter.name} must write its own registered name"
    )


@pytest.mark.parametrize("adapter", all_adapters(), ids=lambda a: a.name)
def test_adapter_name_is_a_registered_provider(adapter):
    assert adapter.name in P.NAMES


def test_entrypoint_is_left_alone():
    """Spec §8: entrypoint semantics do not change."""
    from throughline.adapters import vibe, claude_code

    assert 'entrypoint=""' in inspect.getsource(vibe.VibeAdapter)
    assert "entrypoint=entrypoint" in inspect.getsource(claude_code.ClaudeCodeAdapter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_adapters_write_source_tool.py -v`
Expected: FAIL — `source_tool` is not a parameter of `NormalisedConversation`, and no adapter sets it

- [ ] **Step 3: Add the field to the dataclass**

In `throughline/adapters/base.py`, inside `NormalisedConversation`, add after `entrypoint`:

```python
    #: WHICH tool produced this conversation — the adapter's own ``name``.
    #: Distinct from ``entrypoint``, which is HOW that tool was invoked.
    #: Adapters must set this; leaving it None means "unattributed" and the
    #: UI will render it as such rather than guessing.
    source_tool: str | None = None
```

Place it among the keyword fields with defaults (after `metadata` is fine; it must not precede a field without a default).

- [ ] **Step 4: Persist it in the writer**

In `throughline/adapters/writer.py`, `_upsert_conversation`, change the INSERT to include the column and keep it fresh on conflict:

```python
def _upsert_conversation(cur: Any, conv: NormalisedConversation) -> int:
    cur.execute(
        """
        INSERT INTO conversations
            (session_id, project_path, model, entrypoint, git_branch,
             started_at, ended_at, message_count,
             token_count_in, token_count_out, summary, metadata, source_tool)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_id) DO UPDATE
        SET ended_at      = EXCLUDED.ended_at,
            message_count = EXCLUDED.message_count,
            model         = COALESCE(EXCLUDED.model, conversations.model),
            metadata      = conversations.metadata || EXCLUDED.metadata,
            source_tool   = COALESCE(EXCLUDED.source_tool, conversations.source_tool),
            updated_at    = NOW()
        RETURNING id
        """,
        (
            conv.session_id,
            conv.project_path,
            conv.model,
            conv.entrypoint,
            conv.git_branch,
            conv.started_at,
            conv.ended_at,
            len(conv.messages),
            conv.token_count_in,
            conv.token_count_out,
            conv.summary,
            Json(conv.metadata or {}),
            conv.source_tool,
        ),
    )
    return cur.fetchone()[0]
```

Keep the existing `Json(...)`/parameter style already present in the file for `metadata` — only the column list, the placeholder count, the `SET` clause and the final tuple entry change.

- [ ] **Step 5: Set it in all nine adapters**

Add `source_tool="<name>"` beside the existing `entrypoint=` argument in each `NormalisedConversation(...)` construction. Leave every `entrypoint=` value exactly as it is.

| file:line | add |
|---|---|
| `claude_code.py:159` | `source_tool="claude_code",` |
| `windsurf.py:57` | `source_tool="windsurf",` |
| `hermes.py:209` and `hermes.py:316` | `source_tool="hermes",` |
| `codex.py:202` | `source_tool="codex",` |
| `continue_dev.py:136` and `continue_dev.py:203` | `source_tool="continue",` |
| `cline.py:244` | `source_tool="cline",` |
| `vibe.py:310` | `source_tool="vibe",` |
| `cursor.py:210` | `source_tool="cursor",` |
| `zed.py:184` | `source_tool="zed",` |

Note `continue_dev.py` writes `source_tool="continue"` — the adapter's registered `name`, not its `entrypoint` value `continue.dev`.

- [ ] **Step 6: Run the adapter suite**

Run: `python3 -m pytest tests/test_adapters_write_source_tool.py tests/test_adapter_*.py -v`
Expected: PASS — the new file plus every existing per-adapter test still green

- [ ] **Step 7: Verify end-to-end against the database**

Run: `python3 -m pytest tests/integration/test_adapter_e2e.py tests/integration/test_ingestion_e2e.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add throughline/adapters/ tests/test_adapters_write_source_tool.py
git commit -m "feat(adapters): every adapter writes its own name to source_tool"
```

---

### Task 4: `conflicts.py` groups by provider, not entrypoint

Spec §3.6. Satisfies acceptance bar 7.

**Files:**
- Modify: `throughline/conflicts.py:61,214,251,315,405`
- Test: `tests/integration/test_conflicts_cross_tool.py`

**Interfaces:**
- Consumes: `conversations.source_tool` (Tasks 2, 3)
- Produces: no new symbols; `Conflict.tool` now holds a provider name

- [ ] **Step 1: Write the failing test**

```python
"""Cross-tool conflict detection must not count Claude Code against itself.

Before source_tool, conflicts.py grouped by `entrypoint`, where Claude Code
writes both `cli` and `sdk-cli`. Two Claude Code sessions with different
invocation styles therefore looked like two different tools disagreeing —
the signature analysis of the product, reporting noise.
"""

from __future__ import annotations

import pytest

from throughline import conflicts

pytestmark = pytest.mark.integration


@pytest.fixture()
def two_claude_sessions(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, entrypoint, source_tool, started_at, message_count)
            VALUES (gen_random_uuid(), '/repo/x', 'cli',     'claude_code', now(), 1),
                   (gen_random_uuid(), '/repo/x', 'sdk-cli', 'claude_code', now(), 1)
            RETURNING id
            """
        )
    db_connection.commit()
    return db_connection


def test_same_provider_different_entrypoints_is_not_cross_tool(two_claude_sessions):
    """Bar 7."""
    tools = conflicts.tools_in_use(two_claude_sessions)
    assert "cli" not in tools and "sdk-cli" not in tools
    assert tools == ["claude_code"] or tools == []


def test_entrypoint_values_are_untouched(two_claude_sessions):
    """Spec §8: the correction is in grouping, not in the data."""
    with two_claude_sessions.cursor() as cur:
        cur.execute("SELECT DISTINCT entrypoint FROM conversations WHERE project_path='/repo/x'")
        assert {r[0] for r in cur.fetchall()} == {"cli", "sdk-cli"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/integration/test_conflicts_cross_tool.py -v`
Expected: FAIL — `conflicts` has no `tools_in_use`, and grouping still uses `entrypoint`

- [ ] **Step 3: Switch the grouping**

In `throughline/conflicts.py`, replace `COALESCE(c.entrypoint, 'unknown')` with `COALESCE(c.source_tool, 'unknown')` at lines 214, 315 and 405, and `COALESCE(ca.entrypoint, 'unknown')` at line 251. Update the comment at line 61:

```python
    tool: str                   # the conversations.source_tool value (claude_code, codex, ...)
```

Add the small helper the test uses, near the other module-level query functions:

```python
def tools_in_use(conn) -> list[str]:
    """Distinct providers that have contributed conversations.

    Grouping moved from ``entrypoint`` to ``source_tool`` because the former
    holds `cli` and `sdk-cli` for one and the same tool. Conflict counts will
    move — probably down — and that is a correction, not a regression.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT source_tool FROM conversations "
            "WHERE source_tool IS NOT NULL ORDER BY 1"
        )
        return [r[0] for r in cur.fetchall()]
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/integration/test_conflicts_cross_tool.py tests/test_conflicts.py tests/integration/test_conflicts_marker_pushdown.py -v`
Expected: PASS

- [ ] **Step 5: Record the count change in the changelog**

Spec §3.6 requires this be stated, not discovered. Capture before/after on the real database:

```bash
python3 -c "
from throughline.conflicts import find_contradictions
import psycopg2, os
conn = psycopg2.connect(dbname='claude_memory', user=os.environ.get('USER'), host='localhost')
print('cross-tool conflicts now:', len(find_contradictions(conn)))
"
```

Add a `## Unreleased` entry to `CHANGELOG.md` (create the file if absent) reading:

```markdown
### Changed
- Cross-tool conflict detection now groups by `source_tool` rather than
  `entrypoint`. Conflict counts drop, because Claude Code was previously
  counted in conflict with itself (`cli` vs `sdk-cli`). This is a correction.
```

- [ ] **Step 6: Commit**

```bash
git add throughline/conflicts.py tests/integration/test_conflicts_cross_tool.py CHANGELOG.md
git commit -m "fix(conflicts): group by source_tool so Claude Code stops conflicting with itself"
```

---

### Task 5: Recursive discovery **and** subagent exclusion — one atomic change

Spec §1.4, §4.4, §4.4a, §9, §9.1. Satisfies acceptance bar 4a.

> **DO NOT SPLIT THIS TASK.** `ingest --all` is safe today only because the glob is non-recursive. 98 subagent transcripts collapse to 7 `session_id`s shared with their parents; `_upsert_conversation` keys on `ON CONFLICT (session_id)` and `_replace_messages` does `DELETE FROM messages WHERE conversation_id = …` first. Making the glob recursive without the exclusion means 33 subagent files and the parent all resolve to one row, each deleting the previous one's messages, and the ingest reports success. That is silent data loss. The design below makes the coupling structural: `discover()` is *derived* from `discover_all()` minus exclusions, so the recursive walk cannot reach the writer unfiltered.

**Files:**
- Modify: `throughline/adapters/base.py` — add `discover_all()`, `excluded_reason()`, derive `discover()`; change `is_present()`
- Modify: `throughline/adapters/claude_code.py:71-78`
- Test: `tests/test_adapter_claude_code_subagents.py`
- Test: `tests/integration/test_subagent_exclusion.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `Adapter.discover_all() -> Iterable[Path]` (every candidate file, including excluded; defaults to `discover()`); `Adapter.excluded_reason(path: Path) -> str | None` (defaults to None); `Adapter.discover()` stays abstract and unchanged in meaning — files ingestion **will** process; `Adapter.is_present() -> bool` now means "at least one file discovered"

- [ ] **Step 1: Write the failing unit test**

```python
"""Subagent transcripts are counted but never ingested.

They are not noise — 12-16 messages each, averaging 730 KB, containing work
that exists nowhere else. They are excluded for a correctness reason: a
subagent inherits its parent's `sessionId`, and the writer keys on it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from throughline.adapters.claude_code import ClaudeCodeAdapter


def _write_session(path: Path, session_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": str(uuid.uuid4()),
                "parentUuid": None,
                "isSidechain": False,
                "sessionId": session_id,
                "message": {"role": "user", "content": "hello"},
                "timestamp": "2026-01-15T10:00:00Z",
            }
        )
        + "\n"
    )


@pytest.fixture()
def projects(tmp_path, monkeypatch):
    """A parent session at depth 2 and three subagents beneath it."""
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    _write_session(home / "-Users-x" / f"{sid}.jsonl", sid)
    for i in range(3):
        _write_session(home / "-Users-x" / sid / "subagents" / f"agent-{i}.jsonl", sid)
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)
    return adapter, sid


def test_discover_all_reaches_the_deeper_files(projects):
    """The old `proj.glob('*.jsonl')` could not see 124 of 250 files."""
    adapter, _ = projects
    assert len(list(adapter.discover_all())) == 4


def test_discover_excludes_subagents(projects):
    """Bar 4a: only the parent is offered to the writer."""
    adapter, _ = projects
    found = list(adapter.discover())
    assert len(found) == 1
    assert "subagents" not in str(found[0])


def test_excluded_reason_explains_itself(projects):
    adapter, sid = projects
    sub = adapter.home / "-Users-x" / sid / "subagents" / "agent-0.jsonl"
    parent = adapter.home / "-Users-x" / f"{sid}.jsonl"
    assert adapter.excluded_reason(sub) == "subagent transcript"
    assert adapter.excluded_reason(parent) is None


def test_is_present_is_false_for_an_empty_directory(tmp_path, monkeypatch):
    """Spec §4.4 — this is what makes cline report no_data instead of present."""
    empty = tmp_path / "projects"
    empty.mkdir()
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", empty)
    assert adapter.is_present() is False


def test_is_present_is_true_when_a_file_exists(projects):
    adapter, _ = projects
    assert adapter.is_present() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_adapter_claude_code_subagents.py -v`
Expected: FAIL with `AttributeError: 'ClaudeCodeAdapter' object has no attribute 'discover_all'`

- [ ] **Step 3: Extend the adapter base**

In `throughline/adapters/base.py`, replace the abstract `discover` with this trio. Note `discover` stays concrete and derived — that is what makes the exclusion impossible to bypass:

```python
    @abstractmethod
    def discover(self) -> Iterable[Path]:
        """Yield the files ingestion will process. May be empty.

        This must ALREADY exclude anything unsafe to ingest. An adapter that
        widens its search must widen ``discover_all`` and narrow here — see
        ``excluded_reason`` and the design spec §9.1.
        """

    def discover_all(self) -> Iterable[Path]:
        """Every candidate file, including ones excluded from ingestion.

        The *coverage* view: what exists on disk, for counting and reporting.
        Defaults to ``discover()``, so an adapter with no exclusions needs no
        extra code — and a third-party adapter published through the
        ``throughline.adapters`` entry point keeps working untouched.
        """
        return self.discover()

    def excluded_reason(self, path: Path) -> str | None:
        """Why *path* is discovered but must not be ingested, or None.

        Default: nothing is excluded.
        """
        return None

    def is_present(self) -> bool:
        """Does this tool have any data on this box?

        Was "the directory exists", which reported `cline` as present while
        it contributed nothing. Now: at least one candidate file was found.
        """
        home = self.home.expanduser()
        if not home.exists():
            return False
        return any(True for _ in self.discover_all())
```

`discover` stays abstract deliberately. Making `discover_all` the abstract method instead would break every third-party adapter that implements only `discover()`: the class would fail to instantiate, and `registry.py:_iter_entrypoint_adapters` swallows load failures with a bare `except Exception: continue`, so the adapter would **disappear from the registry with no error message**. The eight adapters that need no exclusion are therefore left completely untouched by this task.

Update the module docstring's contract list to read: "``discover`` yields the files that will be ingested; ``discover_all`` yields every candidate including excluded ones; ``excluded_reason`` explains an exclusion."

- [ ] **Step 4: Make Claude Code recursive with the exclusion in the same edit**

In `throughline/adapters/claude_code.py`, replace lines 71-78:

```python
    #: Subagent transcripts live at
    #: ``~/.claude/projects/<proj>/<session>/subagents/agent-*.jsonl`` and
    #: inherit their parent's ``sessionId``. See ``excluded_reason``.
    SUBAGENT_DIR = "subagents"

    def discover_all(self) -> Iterable[Path]:
        """Every transcript, at any depth.

        Was ``proj.glob("*.jsonl")`` — non-recursive, which could not reach
        124 of the 250 files present. The deeper ones are subagent
        transcripts; nobody decided to exclude them, the glob simply did not
        reach them.
        """
        if not self.home.exists():
            return []
        out: list[Path] = []
        for proj in self.home.iterdir():
            if proj.is_dir():
                out.extend(proj.rglob("*.jsonl"))
        return sorted(out)

    def excluded_reason(self, path: Path) -> str | None:
        """Subagent transcripts are counted, not ingested.

        A subagent's transcript carries its *parent's* ``sessionId``. The
        writer upserts ``ON CONFLICT (session_id)`` and replaces messages with
        a DELETE, so ingesting 33 subagent files plus the parent would resolve
        them all to one row, each deleting the previous one's messages — and
        report success. Ingesting them properly needs its own identity
        (uuid5 of parent + filename) and a `parent_session_id` column; that is
        specified as follow-up work in the design spec §9.3.
        """
        if path.parent.name == self.SUBAGENT_DIR:
            return "subagent transcript"
        return None
```

- [ ] **Step 5: Run the unit tests**

Run: `python3 -m pytest tests/test_adapter_claude_code_subagents.py tests/test_adapter_*.py tests/test_adapter_registry.py -v`
Expected: PASS

- [ ] **Step 6: Write the regression test that proves no data loss**

```python
"""Bar 4a: ingesting a parent plus its subagents keeps the parent whole.

This is the test that would have caught the hazard in design spec §9 before
it reached the database. Without the exclusion, the parent's messages are
deleted and replaced by the last subagent file processed, and the ingest
reports success.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from throughline.adapters.claude_code import ClaudeCodeAdapter
from throughline.adapters.writer import run_adapter

pytestmark = pytest.mark.integration


def _write(path: Path, session_id: str, texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, t in enumerate(texts):
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "parentUuid": None,
                    "isSidechain": False,
                    "sessionId": session_id,
                    "message": {"role": "user", "content": t},
                    "timestamp": f"2026-01-15T10:0{i}:00Z",
                }
            )
        )
    path.write_text("\n".join(lines) + "\n")


def test_parent_session_keeps_all_its_messages(tmp_path, monkeypatch, db_connection):
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    _write(home / "-Users-x" / f"{sid}.jsonl", sid, ["p1", "p2", "p3", "p4", "p5"])
    for i in range(3):
        _write(home / "-Users-x" / sid / "subagents" / f"agent-{i}.jsonl", sid, ["sub"])

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)

    assert len(list(adapter.discover_all())) == 4, "all four files must be countable"
    assert len(list(adapter.discover())) == 1, "only the parent may be ingested"

    run_adapter(adapter, conn=db_connection, verbose=False)

    with db_connection.cursor() as cur:
        cur.execute("SELECT id, message_count FROM conversations WHERE session_id=%s", (sid,))
        row = cur.fetchone()
        assert row is not None, "the parent session must exist"
        conv_id, count = row
        cur.execute("SELECT count(*) FROM messages WHERE conversation_id=%s", (conv_id,))
        actual = cur.fetchone()[0]

    assert actual == 5, f"parent lost messages: {actual} of 5 survived"
    assert count == 5


def test_exactly_one_conversation_per_parent_session(tmp_path, monkeypatch, db_connection):
    """Bar 4a stated directly."""
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    _write(home / "-Users-y" / f"{sid}.jsonl", sid, ["a"])
    for i in range(4):
        _write(home / "-Users-y" / sid / "subagents" / f"agent-{i}.jsonl", sid, ["b"])

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)
    run_adapter(adapter, conn=db_connection, verbose=False)

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] == 1
```

- [ ] **Step 7: Run it and verify it passes**

Run: `python3 -m pytest tests/integration/test_subagent_exclusion.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Verify the counts on the real machine**

```bash
python3 -c "
from throughline.adapters.claude_code import ClaudeCodeAdapter
a = ClaudeCodeAdapter()
allf = list(a.discover_all()); ing = list(a.discover())
print(f'discover_all: {len(allf)}   ingestable: {len(ing)}   excluded: {len(allf)-len(ing)}')
"
```

Expected: roughly `discover_all: 250  ingestable: 152  excluded: 98` (exact numbers drift as sessions accumulate; `excluded` should be ~98 and non-zero).

- [ ] **Step 9: Commit — both edits together**

```bash
git add throughline/adapters/ tests/test_adapter_claude_code_subagents.py \
        tests/integration/test_subagent_exclusion.py
git commit -m "fix(claude_code): discover transcripts recursively, exclude subagents from ingestion

The non-recursive glob could not reach 124 of 250 files. Making it recursive
alone would be a data-loss bug: subagent transcripts inherit their parent's
sessionId, and the writer upserts ON CONFLICT (session_id) after DELETEing
messages, so parent and subagents collapse to one row. discover() is now
derived from discover_all() minus excluded_reason(), so the recursive walk
cannot reach the writer unfiltered."
```

---

### Task 6: Coverage query and `GET /api/providers`

Spec §4.3, §4.5. Satisfies acceptance bar 4.

**Files:**
- Create: `throughline/queries/providers.py`
- Create: `throughline/api/routers/providers.py`
- Modify: `throughline/api/app.py:70` — register the router
- Test: `tests/integration/test_api_providers.py`

**Interfaces:**
- Consumes: `throughline.providers.PROVIDERS` (Task 1); `conversations.source_tool` (Tasks 2, 3); `Adapter.discover_all/excluded_reason/is_present` (Task 5)
- Produces: `coverage(conn) -> list[Row]` with keys `name, label, on_disk, pending, excluded, ingested, last_run, status`; `GET /api/providers -> {"providers": [...]}`

- [ ] **Step 1: Write the failing test**

```python
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

    monkeypatch.setattr(
        Q, "_disk_scan",
        lambda: {"hermes": Q.DiskCounts(on_disk=33, pending=33, excluded=0, present=True)},
    )
    rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    assert rows["hermes"]["status"] == "not_ingested"
    assert rows["hermes"]["pending"] == 33


def test_an_installed_source_with_no_files_reports_no_data(client, monkeypatch):
    """§4.4: cline has a directory but contributes nothing."""
    from throughline.queries import providers as Q

    monkeypatch.setattr(
        Q, "_disk_scan",
        lambda: {"cline": Q.DiskCounts(on_disk=0, pending=0, excluded=0, present=False)},
    )
    rows = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/integration/test_api_providers.py -v`
Expected: FAIL — `/api/providers` returns 404 and `throughline.queries.providers` does not exist

- [ ] **Step 3: Write the coverage query**

```python
"""Provider coverage: what exists on disk against what is imported.

`pending` — discovered files with no `ingestion_log` entry that ingestion
*would* process — is the column that matters. `on_disk` alone is misleading:
Claude Code rotates its transcripts, so conversations persist after their
files are gone and `ingested` can legitimately exceed `on_disk`. `status`
therefore derives from `pending`, never from `ingested == 0`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from throughline import providers as P
from throughline.adapters.registry import all_adapters
from throughline.queries._exec import rows

#: The scan walks ~300 paths and hashes nothing, so it is cheap — but it
#: changes when you ingest, not per request, and Overview polls while a job
#: runs. Sixty seconds is long enough to absorb the polling and short enough
#: that a finished ingest shows up without a restart.
CACHE_TTL_SECONDS = 60

_cache: tuple[float, dict[str, "DiskCounts"]] | None = None


@dataclass(frozen=True)
class DiskCounts:
    on_disk: int
    pending: int
    excluded: int
    present: bool


def invalidate_scan_cache() -> None:
    """Call after an ingest so coverage reflects it immediately."""
    global _cache
    _cache = None


def _scan_uncached() -> dict[str, DiskCounts]:
    from throughline.adapters.writer import _connect

    ingested_paths: set[str] = set()
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT file_path FROM ingestion_log")
                ingested_paths = {r[0] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        # A scan that cannot reach the database still reports disk truth.
        ingested_paths = set()

    out: dict[str, DiskCounts] = {}
    for adapter in all_adapters():
        try:
            every = [str(p) for p in adapter.discover_all()]
            ingestable = {str(p) for p in adapter.discover()}
        except Exception:
            out[adapter.name] = DiskCounts(0, 0, 0, False)
            continue
        pending = sum(1 for p in every if p in ingestable and p not in ingested_paths)
        out[adapter.name] = DiskCounts(
            on_disk=len(every),
            pending=pending,
            excluded=len(every) - len(ingestable),
            present=bool(every),
        )
    return out


def _disk_scan() -> dict[str, DiskCounts]:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    scanned = _scan_uncached()
    _cache = (now, scanned)
    return scanned


def _status(disk: DiskCounts, ingested: int) -> str:
    if not disk.present and ingested == 0:
        return "no_data"
    if disk.pending > 0 and ingested == 0:
        return "not_ingested"
    if disk.pending > 0:
        return "pending"
    return "ok"


def coverage(conn) -> list[dict]:
    counts = {
        r["source_tool"]: r
        for r in rows(
            conn,
            """
            SELECT source_tool, count(*) AS ingested, max(started_at) AS last_run
            FROM conversations
            GROUP BY source_tool
            """,
        )
    }
    disk = _disk_scan()

    out: list[dict] = []
    for prov in P.PROVIDERS:
        c = counts.get(prov.name) or {}
        d = disk.get(prov.name) or DiskCounts(0, 0, 0, False)
        ingested = int(c.get("ingested") or 0)
        out.append(
            {
                "name": prov.name,
                "label": prov.label,
                "chart_slot": prov.chart_slot,
                "on_disk": d.on_disk,
                "pending": d.pending,
                "excluded": d.excluded,
                "ingested": ingested,
                "last_run": c.get("last_run"),
                "status": _status(d, ingested),
            }
        )

    unattributed = counts.get(None)
    if unattributed and int(unattributed.get("ingested") or 0) > 0:
        out.append(
            {
                "name": "(unattributed)",
                "label": P.UNATTRIBUTED_LABEL,
                "chart_slot": 0,
                "on_disk": 0,
                "pending": 0,
                "excluded": 0,
                "ingested": int(unattributed["ingested"]),
                "last_run": unattributed.get("last_run"),
                "status": "unknown",
            }
        )
    return out
```

- [ ] **Step 4: Write the router**

```python
"""Provider coverage — one endpoint, three consumers.

The provider bar, the Overview attention item and the Operate table all read
this. One source, so they cannot disagree about whether Hermes is imported.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from throughline.api.deps import connection
from throughline.api.settings import Settings, get_settings
from throughline.queries import providers as Q

router = APIRouter(tags=["providers"])


@router.get("/providers")
def list_providers(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"providers": Q.coverage(conn)}
```

This is the connection pattern the other routers use (`throughline/api/routers/overview.py:220`) — a `get_settings` dependency plus the `connection(settings)` context manager. There is no `get_conn` dependency in `deps.py`; import `get_settings` from wherever `overview.py` imports it.

- [ ] **Step 5: Register the router**

In `throughline/api/app.py`, beside the other includes:

```python
    app.include_router(providers.router, prefix="/api")
```

and add `providers` to the routers import at the top of the file.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/integration/test_api_providers.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Verify bar 4 against the real database**

```bash
curl -s localhost:8787/api/providers | python3 -m json.tool
```

Expected: `hermes` and `vibe` report `not_ingested` with non-zero `pending`; `cline` reports `no_data`; `claude_code` reports non-zero `pending` and `excluded` ≈ 98.

- [ ] **Step 8: Commit**

```bash
git add throughline/queries/providers.py throughline/api/routers/providers.py \
        throughline/api/app.py tests/integration/test_api_providers.py
git commit -m "feat(api): add /api/providers coverage endpoint"
```

---

### Task 7: Provider as a Find filter

The backend half of spec §4.1 — the facet and the bar both narrow queries through this one filter.

**Files:**
- Modify: `throughline/queries/find.py:105-120` — add `providers` to `FindFilters`, apply it in each retriever
- Modify: `throughline/api/routers/find.py:45-65` — accept `provider` query params
- Test: `tests/integration/test_find_provider_filter.py`

**Interfaces:**
- Consumes: `conversations.source_tool` (Tasks 2, 3); `throughline.providers.NAMES` (Task 1)
- Produces: `FindFilters.providers: list[str]`; `GET /api/find?provider=…` (repeatable)

- [ ] **Step 1: Write the failing test**

```python
"""Provider narrows Find, and inherits through the conversation join."""

from __future__ import annotations

import pytest

from throughline.queries import find as F

pytestmark = pytest.mark.integration


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, source_tool, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/p', 'claude_code', now(), 1, 'zebrafish study'),
                   (gen_random_uuid(), '/p', 'hermes',      now(), 1, 'zebrafish study'),
                   (gen_random_uuid(), '/p', NULL,          now(), 1, 'zebrafish study')
            """
        )
    db_connection.commit()
    return db_connection


def test_unfiltered_finds_all_three(corpus):
    res = F.find(corpus, "zebrafish", filters=F.FindFilters(kinds=["conversation"]), limit=50)
    assert len(res.items) >= 3


def test_one_provider_narrows(corpus):
    res = F.find(
        corpus, "zebrafish",
        filters=F.FindFilters(kinds=["conversation"], providers=["hermes"]),
        limit=50,
    )
    assert len(res.items) == 1


def test_several_providers_union(corpus):
    res = F.find(
        corpus, "zebrafish",
        filters=F.FindFilters(kinds=["conversation"], providers=["hermes", "claude_code"]),
        limit=50,
    )
    assert len(res.items) == 2


def test_browse_honours_the_filter(corpus):
    res = F.browse(corpus, F.FindFilters(kinds=["conversation"], providers=["hermes"]), limit=50)
    assert len(res.items) == 1


def test_messages_inherit_provider_through_their_conversation(corpus):
    """Spec §3.4: no denormalisation; the join already exists."""
    with corpus.cursor() as cur:
        cur.execute("SELECT id FROM conversations WHERE source_tool='hermes' LIMIT 1")
        conv_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (%s, 'user', 'zebrafish in the message', now())",
            (conv_id,),
        )
    corpus.commit()
    res = F.find(
        corpus, "zebrafish",
        filters=F.FindFilters(kinds=["message"], providers=["hermes"]),
        limit=50,
    )
    assert len(res.items) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/integration/test_find_provider_filter.py -v`
Expected: FAIL with `TypeError: FindFilters.__init__() got an unexpected keyword argument 'providers'`

- [ ] **Step 3: Add the filter field**

In `throughline/queries/find.py`, inside `FindFilters` (after `projects`):

```python
    #: Provider names (``conversations.source_tool``). App-scope, unlike the
    #: other facets — see design spec §4.2.
    providers: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Apply it in the retrievers**

Each per-kind query gains a provider predicate. Conversations filter directly; messages and memory inherit through their conversation:

```python
def _provider_clause(alias: str, filters: FindFilters, params: dict) -> str:
    """SQL fragment restricting *alias* (a conversations alias) by provider."""
    if not filters.providers:
        return ""
    params["providers"] = list(filters.providers)
    return f" AND {alias}.source_tool = ANY(%(providers)s)"


def _provider_clause_via_conversation(source_col: str, filters: FindFilters, params: dict) -> str:
    """For tables that reach conversations by id, e.g. memory_chunks.source_id."""
    if not filters.providers:
        return ""
    params["providers"] = list(filters.providers)
    return (
        f" AND EXISTS (SELECT 1 FROM conversations pc "
        f"WHERE pc.id = {source_col} AND pc.source_tool = ANY(%(providers)s))"
    )
```

Apply `_provider_clause("c", ...)` in the conversation and message retrievers (both already join `conversations c`), and `_provider_clause_via_conversation("mc.source_id", ...)` in the memory retriever.

Kinds with no provider dimension — `skill`, `project`, `prompt` — are **excluded entirely** when a provider filter is active, because "show me Hermes" should not return every skill. Add at the top of each of those retrievers:

```python
    if filters.providers:
        return []
```

- [ ] **Step 5: Accept the parameter in the router**

In `throughline/api/routers/find.py`, add to the signature beside `project`:

```python
    provider: list[str] = Query(default=[]),
```

and to the `FindFilters(...)` construction:

```python
        providers=provider,
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/integration/test_find_provider_filter.py tests/integration/test_find.py tests/integration/test_api_find.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add throughline/queries/find.py throughline/api/routers/find.py \
        tests/integration/test_find_provider_filter.py
git commit -m "feat(find): filter by provider, inherited through the conversation join"
```

---

### Task 8: Timeline aggregate endpoint

Spec §5.1, §5.3. Satisfies acceptance bar 6 — the bar the Timeline has now failed twice.

**Files:**
- Create: `throughline/queries/timeline.py`
- Create: `throughline/api/routers/timeline.py`
- Modify: `throughline/api/app.py` — register the router
- Test: `tests/integration/test_timeline_aggregate.py`

**Interfaces:**
- Consumes: `conversations.source_tool` (Tasks 2, 3); `FindFilters` (Task 7)
- Produces: `pick_bucket(since, until) -> str`; `aggregate(conn, since, until, bucket, kinds, providers) -> list[Row]` with keys `bucket, provider, kind, n`; `day_detail(conn, day, kinds, providers, limit, offset) -> list[Row]`; `GET /api/timeline`; `GET /api/timeline/day/{date}`

- [ ] **Step 1: Write the failing test**

```python
"""The Timeline covers a range, not a page.

Bar 6 is stated at length in the spec because Timeline has failed twice by
verifying the wrong property: sources reachable rather than range complete,
page rendered rather than data whole. These tests reconcile lane totals
against raw counts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from throughline.queries import timeline as T

pytestmark = pytest.mark.integration


@pytest.fixture()
def spread(db_connection):
    """300 conversations over 300 days — more than any page limit."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db_connection.cursor() as cur:
        for i in range(300):
            cur.execute(
                "INSERT INTO conversations "
                "(session_id, project_path, source_tool, started_at, message_count) "
                "VALUES (gen_random_uuid(), '/t', %s, %s, 1)",
                ("claude_code" if i % 2 else "hermes", base + timedelta(days=i)),
            )
    db_connection.commit()
    return db_connection


def test_bucket_auto_selection():
    d = date(2026, 1, 1)
    assert T.pick_bucket(d, d + timedelta(days=30)) == "day"
    assert T.pick_bucket(d, d + timedelta(days=200)) == "week"
    assert T.pick_bucket(d, d + timedelta(days=1000)) == "month"


def test_range_is_complete_not_paginated(spread):
    """Bar 6: a date range with no query shows every conversation in it."""
    since, until = date(2026, 1, 1), date(2026, 12, 31)
    agg = T.aggregate(spread, since, until, "day", kinds=["conversation"], providers=[])
    total = sum(r["n"] for r in agg)

    with spread.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM conversations "
            "WHERE started_at >= %s AND started_at < %s + interval '1 day'",
            (since, until),
        )
        raw = cur.fetchone()[0]

    assert total == raw, f"lane totals {total} != raw count {raw}"
    assert total >= 300


def test_lanes_are_per_provider(spread):
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 12, 31), "month",
        kinds=["conversation"], providers=[],
    )
    assert {r["provider"] for r in agg} >= {"claude_code", "hermes"}


def test_provider_filter_narrows_and_still_reconciles(spread):
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 12, 31), "day",
        kinds=["conversation"], providers=["hermes"],
    )
    total = sum(r["n"] for r in agg)
    with spread.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations WHERE source_tool='hermes'")
        assert total == cur.fetchone()[0]


def test_row_count_stays_bounded_regardless_of_corpus(spread):
    """90 days x 9 providers is ~810 rows whatever the corpus size."""
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 3, 31), "day",
        kinds=["conversation"], providers=[],
    )
    assert len(agg) < 1000


def test_non_provider_sources_get_their_own_lane(db_connection):
    """§5.3: skills etc. are not per-tool and must not be dropped or forced."""
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO skills (name, description, file_path, created_at) "
            "VALUES ('t-skill', 'd', '/tmp/x/SKILL.md', %s) ON CONFLICT DO NOTHING",
            (datetime(2026, 5, 5, tzinfo=timezone.utc),),
        )
    db_connection.commit()
    agg = T.aggregate(
        db_connection, date(2026, 5, 1), date(2026, 5, 31), "day",
        kinds=["skill"], providers=[],
    )
    assert any(r["provider"] == T.NOT_TOOL_SPECIFIC for r in agg)


def test_month_boundary_buckets_do_not_leak(spread):
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 1, 31), "month",
        kinds=["conversation"], providers=[],
    )
    assert {str(r["bucket"])[:7] for r in agg} == {"2026-01"}


def test_day_detail_returns_that_days_events(spread):
    detail = T.day_detail(
        spread, date(2026, 2, 1), kinds=["conversation"], providers=[], limit=50, offset=0
    )
    assert len(detail) >= 1
    assert all(str(r["ts"])[:10] == "2026-02-01" for r in detail)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/integration/test_timeline_aggregate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'throughline.queries.timeline'`

- [ ] **Step 3: Write the aggregate query**

```python
"""Timeline: a bucketed aggregate over a date range.

The previous Timeline rendered `data.items` — the current page of search
results, 30 by default. Sources were verified reachable; range never was.
This returns counts per (bucket, provider, kind), so the row count depends on
the range and the provider count, never on the corpus size: 90 days x 9
providers is ~810 rows whether the database holds 3,000 conversations or
3,000,000. Detail arrives only when a cell is clicked.
"""

from __future__ import annotations

from datetime import date

from throughline.queries._exec import rows

#: Skills, projects, prompts, entities and reflections are not per-tool. They
#: get their own lane rather than being forced into a provider or dropped, so
#: all eight of the old Calendar's sources stay reachable.
NOT_TOOL_SPECIFIC = "not_tool_specific"

BUCKETS = ("day", "week", "month")

#: Every kind the Timeline can show, with the table and timestamp it reads and
#: how it reaches a provider (None = the not-tool-specific lane).
_SOURCES: dict[str, tuple[str, str, str | None]] = {
    "conversation": ("conversations c", "c.started_at", "c.source_tool"),
    "message": (
        "messages m JOIN conversations c ON c.id = m.conversation_id",
        "m.created_at",
        "c.source_tool",
    ),
    "memory": (
        "memory_chunks mc LEFT JOIN conversations c ON c.id = mc.source_id",
        "mc.created_at",
        "c.source_tool",
    ),
    # Column names verified against throughline/queries/activity.py, which
    # already reads all six tables. `skills` has no single event timestamp —
    # activity.py coalesces the same three columns, and so must this.
    "skill": ("skills s", "COALESCE(s.file_modified, s.last_used, s.created_at)", None),
    "project": ("projects p", "p.created_at", None),
    "prompt": ("prompts pr", "pr.created_at", None),
}


def pick_bucket(since: date, until: date) -> str:
    """<=90 days by day, <=2 years by week, beyond by month.

    Keeps "all time" cheap without the caller having to think about it.
    """
    span = (until - since).days
    if span <= 90:
        return "day"
    if span <= 730:
        return "week"
    return "month"


def aggregate(
    conn,
    since: date,
    until: date,
    bucket: str,
    kinds: list[str],
    providers: list[str],
) -> list[dict]:
    if bucket not in BUCKETS:
        raise ValueError(f"bucket must be one of {BUCKETS}, got {bucket!r}")
    wanted = [k for k in (kinds or list(_SOURCES)) if k in _SOURCES]
    if not wanted:
        return []

    params: dict = {"since": since, "until": until}
    if providers:
        params["providers"] = list(providers)

    parts: list[str] = []
    for kind in wanted:
        frm, ts, provider_col = _SOURCES[kind]
        if provider_col is None:
            if providers:
                # A provider scope is active and this kind has no provider.
                continue
            provider_expr = f"'{NOT_TOOL_SPECIFIC}'"
            provider_filter = ""
        else:
            provider_expr = f"COALESCE({provider_col}, 'unattributed')"
            provider_filter = f" AND {provider_col} = ANY(%(providers)s)" if providers else ""

        parts.append(
            f"""
            SELECT date_trunc(%(bucket)s, {ts})::date AS bucket,
                   {provider_expr} AS provider,
                   '{kind}' AS kind,
                   count(*) AS n
            FROM {frm}
            WHERE {ts} >= %(since)s
              AND {ts} < (%(until)s::date + interval '1 day')
              {provider_filter}
            GROUP BY 1, 2
            """
        )

    if not parts:
        return []
    params["bucket"] = bucket
    sql = " UNION ALL ".join(parts) + " ORDER BY bucket, provider, kind"
    return rows(conn, sql, params)


def day_detail(
    conn,
    day: date,
    kinds: list[str],
    providers: list[str],
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """One day's events. Clicking a cell is what loads rows."""
    wanted = [k for k in (kinds or ["conversation"]) if k in _SOURCES]
    if not wanted:
        return []

    params: dict = {"day": day, "limit": limit, "offset": offset}
    if providers:
        params["providers"] = list(providers)

    parts: list[str] = []
    for kind in wanted:
        frm, ts, provider_col = _SOURCES[kind]
        if provider_col is None:
            if providers:
                continue
            provider_expr = f"'{NOT_TOOL_SPECIFIC}'"
            provider_filter = ""
            id_expr, title_expr = _detail_columns(kind)
        else:
            provider_expr = f"COALESCE({provider_col}, 'unattributed')"
            provider_filter = f" AND {provider_col} = ANY(%(providers)s)" if providers else ""
            id_expr, title_expr = _detail_columns(kind)

        parts.append(
            f"""
            SELECT {id_expr} AS id,
                   '{kind}' AS kind,
                   {provider_expr} AS provider,
                   {ts} AS ts,
                   {title_expr} AS title
            FROM {frm}
            WHERE {ts} >= %(day)s
              AND {ts} < (%(day)s::date + interval '1 day')
              {provider_filter}
            """
        )

    if not parts:
        return []
    sql = (
        " UNION ALL ".join(parts)
        + " ORDER BY ts DESC, kind, id DESC LIMIT %(limit)s OFFSET %(offset)s"
    )
    return rows(conn, sql, params)


def _detail_columns(kind: str) -> tuple[str, str]:
    """(id expression, title expression) per kind for the day view."""
    return {
        "conversation": ("c.id", "COALESCE(c.summary, c.project_name, '(conversation)')"),
        "message": ("m.id", "left(m.content, 200)"),
        "memory": ("mc.id", "left(mc.content, 200)"),
        "skill": ("s.id", "s.name"),
        "project": ("p.id", "p.name"),
        "prompt": ("pr.id", "COALESCE(pr.name, '(prompt)')"),
    }[kind]
```

`skills.name`, `projects.name` and `prompts.name` are confirmed against `activity.py` (`events_skills`, `events_projects`, `events_prompts`). If any UNION branch fails on a type mismatch, cast the title expressions to `text` — Postgres requires every branch of a UNION to agree on column types.

- [ ] **Step 4: Write the router**

```python
"""Timeline endpoints.

The Timeline is its own surface, not a view mode of search results — which is
what made it inherit pagination and show one page of a range.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from throughline.api.deps import connection
from throughline.api.settings import Settings, get_settings
from throughline.queries import timeline as T

router = APIRouter(tags=["timeline"])

MAX_DETAIL = 500


@router.get("/timeline")
def get_timeline(
    since: date | None = Query(None),
    until: date | None = Query(None),
    bucket: str | None = Query(None, pattern="^(day|week|month)$"),
    kind: list[str] = Query(default=[]),
    provider: list[str] = Query(default=[]),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    until = until or date.today()
    since = since or (until - timedelta(days=89))
    chosen = bucket or T.pick_bucket(since, until)
    with connection(settings) as conn:
        cells = T.aggregate(conn, since, until, chosen, kinds=kind, providers=provider)
    return {"since": since, "until": until, "bucket": chosen, "cells": cells}


@router.get("/timeline/day/{day}")
def get_timeline_day(
    day: date,
    kind: list[str] = Query(default=[]),
    provider: list[str] = Query(default=[]),
    limit: int = Query(100, ge=1, le=MAX_DETAIL),
    offset: int = Query(0, ge=0),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    with connection(settings) as conn:
        items = T.day_detail(
            conn, day, kinds=kind, providers=provider, limit=limit, offset=offset
        )
    return {"day": day, "items": items}
```

Register it in `throughline/api/app.py` beside the others.

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest tests/integration/test_timeline_aggregate.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Verify bar 6 against the real database, not the Docker one**

The spec is explicit about this. With the API running against `claude_memory`:

```bash
curl -s "localhost:8787/api/timeline?since=2026-01-01&until=2026-12-31&kind=conversation" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('lane total:', sum(c['n'] for c in d['cells']))"
psql -d claude_memory -tAc \
  "SELECT count(*) FROM conversations WHERE started_at >= '2026-01-01' AND started_at < '2027-01-01'"
```

Expected: the two numbers are identical. If they differ, bar 6 has failed — do not re-scope it.

- [ ] **Step 7: Commit**

```bash
git add throughline/queries/timeline.py throughline/api/routers/timeline.py \
        throughline/api/app.py tests/integration/test_timeline_aggregate.py
git commit -m "feat(timeline): date-range aggregate endpoint with per-provider lanes"
```

---

### Task 9: Provider as app-scope URL state

Spec §4.1, §4.2. The frontend half of "one state, two controls". Satisfies acceptance bar 5.

**Files:**
- Create: `web/src/lib/providerScope.ts`
- Modify: `web/src/features/find/useFindState.ts:15-45` — add `providers` to `FindState`
- Modify: `web/src/lib/api.ts` — add `providersApi` and `timelineApi`
- Test: `web/src/lib/providerScope.test.ts`
- Test: `web/src/features/find/useFindState.test.ts` (extend)

**Interfaces:**
- Consumes: `GET /api/providers` (Task 6); `GET /api/timeline` (Task 8); `FindFilters.providers` via `?provider=` (Task 7)
- Produces: `PROVIDER_PARAM = "provider"`; `readProviders(sp: URLSearchParams): string[]`; `withProviders(sp: URLSearchParams, next: string[]): URLSearchParams`; `carryProviders(to: string, sp: URLSearchParams): string`; `FindState.providers: string[]`; `ProviderCoverage` interface; `providersApi.list()`; `timelineApi.range()`, `timelineApi.day()`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";

import {
  PROVIDER_PARAM,
  carryProviders,
  readProviders,
  withProviders,
} from "./providerScope";

describe("provider scope", () => {
  it("reads repeated params", () => {
    const sp = new URLSearchParams("provider=hermes&provider=vibe");
    expect(readProviders(sp)).toEqual(["hermes", "vibe"]);
  });

  it("is empty when absent", () => {
    expect(readProviders(new URLSearchParams(""))).toEqual([]);
  });

  it("replaces rather than appends", () => {
    const sp = new URLSearchParams("provider=hermes&q=x");
    const next = withProviders(sp, ["vibe"]);
    expect(next.getAll(PROVIDER_PARAM)).toEqual(["vibe"]);
    expect(next.get("q")).toBe("x");
  });

  it("clears the param entirely when the selection is empty", () => {
    const sp = new URLSearchParams("provider=hermes");
    expect(withProviders(sp, []).toString()).toBe("");
  });

  it("carries provider across navigation but nothing else", () => {
    // Spec §4.2: provider is app-scope; category/tag/confidence stay Find-local.
    const sp = new URLSearchParams("provider=hermes&category=insight&q=zebra");
    const to = carryProviders("/curate", sp);
    expect(to).toBe("/curate?provider=hermes");
  });

  it("returns a bare path when no provider is active", () => {
    expect(carryProviders("/curate", new URLSearchParams("q=x"))).toBe("/curate");
  });

  it("carries several providers", () => {
    const sp = new URLSearchParams("provider=hermes&provider=vibe");
    expect(carryProviders("/operate", sp)).toBe("/operate?provider=hermes&provider=vibe");
  });
});
```

Extend `web/src/features/find/useFindState.test.ts` with:

```ts
  it("parses providers from the URL", () => {
    const s = parseFindState(new URLSearchParams("provider=hermes&provider=vibe"));
    expect(s.providers).toEqual(["hermes", "vibe"]);
  });

  it("defaults providers to empty", () => {
    expect(parseFindState(new URLSearchParams("")).providers).toEqual([]);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npm test -- providerScope useFindState`
Expected: FAIL — cannot resolve `./providerScope`, and `providers` is missing from `FindState`

- [ ] **Step 3: Write the scope module**

```ts
/**
 * Provider is app-scope; the other facets are Find-local.
 *
 * The bar and the Find facet are two renderings of ONE piece of state — this
 * URL parameter — not two states to keep in sync. That is what removes the
 * risk of the two controls disagreeing, and it is why "I am looking at
 * Hermes" survives navigating from Find to Curate while `category` does not.
 */
export const PROVIDER_PARAM = "provider";

export function readProviders(sp: URLSearchParams): string[] {
  return sp.getAll(PROVIDER_PARAM);
}

export function withProviders(sp: URLSearchParams, next: string[]): URLSearchParams {
  const out = new URLSearchParams(sp);
  out.delete(PROVIDER_PARAM);
  for (const name of next) out.append(PROVIDER_PARAM, name);
  return out;
}

/** Build a link to `to` that preserves only the provider scope. */
export function carryProviders(to: string, sp: URLSearchParams): string {
  const active = readProviders(sp);
  if (active.length === 0) return to;
  const carried = new URLSearchParams();
  for (const name of active) carried.append(PROVIDER_PARAM, name);
  return `${to}?${carried.toString()}`;
}
```

- [ ] **Step 4: Add `providers` to Find state**

In `web/src/features/find/useFindState.ts`: add `providers: string[];` to `FindState`, `providers: []` to `DEFAULTS`, `"provider"` to the `MULTI` tuple, and `providers: sp.getAll("provider"),` to `parseFindState`.

- [ ] **Step 5: Add the API clients**

In `web/src/lib/api.ts`:

```ts
export interface ProviderCoverage {
  name: string;
  label: string;
  chart_slot: number;
  on_disk: number;
  pending: number;
  excluded: number;
  ingested: number;
  last_run: string | null;
  status: "ok" | "pending" | "not_ingested" | "no_data" | "unknown";
}

export const providersApi = {
  list: () => request<{ providers: ProviderCoverage[] }>("/api/providers"),
};

export interface TimelineCell {
  bucket: string;
  provider: string;
  kind: Kind;
  n: number;
}

export interface TimelineRange {
  since: string;
  until: string;
  bucket: "day" | "week" | "month";
  cells: TimelineCell[];
}

export const timelineApi = {
  range: (qs: URLSearchParams) => request<TimelineRange>(`/api/timeline?${qs}`),
  day: (day: string, qs: URLSearchParams) =>
    request<{ day: string; items: FindItem[] }>(`/api/timeline/day/${day}?${qs}`),
};
```

Use whatever the file's existing fetch helper is called — match the pattern already used by `findApi` and `operateApi` rather than introducing a second one.

- [ ] **Step 6: Run tests and typecheck**

Run: `cd web && npm test -- providerScope useFindState && npm run typecheck`
Expected: PASS, no type errors

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/providerScope.ts web/src/lib/providerScope.test.ts \
        web/src/lib/api.ts web/src/features/find/useFindState.ts \
        web/src/features/find/useFindState.test.ts
git commit -m "feat(web): provider as app-scope URL state"
```

---

### Task 10: The provider bar

Spec §4.3 (bar consumer), §4.2 (hidden on Console). Satisfies acceptance bar 5.

**Files:**
- Create: `web/src/components/ProviderBar.tsx`
- Modify: `web/src/components/Shell.tsx:105-121` — render the bar, carry provider on nav links
- Modify: `web/src/components/CommandPalette.tsx` — carry provider on palette navigation
- Test: `web/src/components/ProviderBar.test.tsx`

**Interfaces:**
- Consumes: `providersApi.list()`, `readProviders`, `withProviders`, `carryProviders` (Task 9)
- Produces: `<ProviderBar />` — reads and writes `?provider=`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ProviderBar } from "./ProviderBar";

vi.mock("@/lib/api", () => ({
  providersApi: {
    list: async () => ({
      providers: [
        { name: "claude_code", label: "Claude Code", chart_slot: 1, on_disk: 224,
          pending: 126, excluded: 98, ingested: 3016, last_run: null, status: "pending" },
        { name: "hermes", label: "Hermes", chart_slot: 3, on_disk: 33,
          pending: 33, excluded: 0, ingested: 0, last_run: null, status: "not_ingested" },
      ],
    }),
  },
}));

function LocationProbe() {
  const loc = useLocation();
  return <output data-testid="loc">{loc.search}</output>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ProviderBar />
      <LocationProbe />
      <Routes>
        <Route path="*" element={null} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProviderBar", () => {
  it("shows a chip per provider with its ingested count", async () => {
    renderAt("/find");
    const chip = await screen.findByRole("button", { name: /Claude Code/ });
    expect(within(chip).getByText(/3,016|3016/)).toBeTruthy();
  });

  it("marks an un-ingested provider so it cannot be mistaken for empty", async () => {
    renderAt("/find");
    const hermes = await screen.findByRole("button", { name: /Hermes/ });
    expect(hermes.getAttribute("data-status")).toBe("not_ingested");
  });

  it("writes the provider param when a chip is selected", async () => {
    renderAt("/find");
    await userEvent.click(await screen.findByRole("button", { name: /Hermes/ }));
    expect(screen.getByTestId("loc").textContent).toContain("provider=hermes");
  });

  it("toggles a selected chip back off", async () => {
    renderAt("/find?provider=hermes");
    await userEvent.click(await screen.findByRole("button", { name: /Hermes/ }));
    expect(screen.getByTestId("loc").textContent).not.toContain("provider=hermes");
  });

  it("reflects the active scope from the URL, so it is never invisible", async () => {
    renderAt("/find?provider=hermes");
    const hermes = await screen.findByRole("button", { name: /Hermes/ });
    expect(hermes.getAttribute("aria-pressed")).toBe("true");
  });

  it("renders nothing on Console", async () => {
    // Spec §4.2: raw SQL ignores the scope, and a control that does not affect
    // what you see is worse than none.
    const { container } = renderAt("/console");
    expect(container.querySelector("[data-testid='provider-bar']")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- ProviderBar`
Expected: FAIL — cannot resolve `./ProviderBar`

- [ ] **Step 3: Write the component**

```tsx
import { useQuery } from "@tanstack/react-query";
import { useLocation, useSearchParams } from "react-router-dom";

import { providersApi } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { PROVIDER_PARAM, readProviders, withProviders } from "@/lib/providerScope";

/**
 * The provider scope, permanently visible.
 *
 * Throughline exists to unify memory across nine AI CLIs, and the previous
 * interface exposed the originating tool in exactly one place — the
 * conversation detail record. This bar is the fix: the scope is always on
 * screen, so it can never silently filter something the user has forgotten
 * about.
 *
 * Hidden on Console, where raw SQL ignores it.
 */
const HIDDEN_ON = ["/console"];

export function ProviderBar() {
  const { pathname } = useLocation();
  const [sp, setSp] = useSearchParams();
  const { data } = useQuery({
    queryKey: ["providers"],
    queryFn: () => providersApi.list(),
    staleTime: 60_000,
  });

  if (HIDDEN_ON.some((p) => pathname.startsWith(p))) return null;

  const active = new Set(readProviders(sp));
  const providers = data?.providers ?? [];

  function toggle(name: string) {
    const next = new Set(active);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSp(withProviders(sp, [...next]), { replace: false });
  }

  return (
    <div className="provider-bar" data-testid="provider-bar" role="group" aria-label="Provider scope">
      {providers.map((p) => {
        const on = active.has(p.name);
        return (
          <button
            key={p.name}
            type="button"
            className={`provider-chip${on ? " is-active" : ""}`}
            data-status={p.status}
            data-slot={p.chart_slot}
            aria-pressed={on}
            onClick={() => toggle(p.name)}
            title={
              p.pending > 0
                ? `${p.pending} file(s) on disk not imported`
                : `${formatCount(p.ingested)} conversation(s)`
            }
          >
            <span className="provider-chip-label">{p.label}</span>
            <span className="provider-chip-count">{formatCount(p.ingested)}</span>
            {p.pending > 0 && (
              <span className="provider-chip-dot" aria-label="not fully imported" />
            )}
          </button>
        );
      })}
      {active.size > 0 && (
        <button
          type="button"
          className="provider-chip provider-chip-clear"
          onClick={() => setSp(withProviders(sp, []))}
        >
          Clear scope
        </button>
      )}
    </div>
  );
}
```

Add matching styles to `web/src/styles/index.css` using existing tokens — `--surface-raised` for the chip, `--accent` for `.is-active`, `--status-warning` for the dot. Do not introduce raw hex values; the palette is validated and tokenised.

- [ ] **Step 4: Mount it and carry provider across navigation**

In `web/src/components/Shell.tsx`, render `<ProviderBar />` directly above the page outlet, and change the nav links so they preserve scope:

```tsx
import { carryProviders } from "@/lib/providerScope";
// inside the component:
const [sp] = useSearchParams();
// and in the NavLink:
  to={carryProviders(item.to, sp)}
```

Apply the same `carryProviders` call to the `g`-chord `navigate(item.to)` handler and to every navigation action in `CommandPalette.tsx`.

- [ ] **Step 5: Run tests, typecheck, and drive the real UI**

```bash
cd web && npm test -- ProviderBar && npm run typecheck && npm run build
```

Then, with the server running, verify bar 5 by hand: select Hermes on Find, navigate to Curate and Operate via both the sidebar and `g c` / `g p`, and confirm the chip stays selected and the URL keeps `?provider=hermes`. Open Console and confirm the bar disappears.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ProviderBar.tsx web/src/components/ProviderBar.test.tsx \
        web/src/components/Shell.tsx web/src/components/CommandPalette.tsx \
        web/src/styles/index.css
git commit -m "feat(web): provider bar, carried across navigation"
```

---

### Task 11: Timeline becomes its own surface

Spec §5.2, §5.4. Satisfies acceptance bar 6 in the interface.

**Files:**
- Create: `web/src/features/timeline/TimelinePage.tsx`
- Create: `web/src/features/timeline/RangeControl.tsx`
- Delete: `web/src/features/find/ResultTimeline.tsx`
- Modify: `web/src/features/find/useFindState.ts` — drop `"timeline"` from `ViewMode`
- Modify: `web/src/lib/nav.ts` — add the Timeline surface
- Modify: the router (wherever `/find`, `/curate` etc. are declared) — add `/timeline`
- Test: `web/src/features/timeline/TimelinePage.test.tsx`

**Interfaces:**
- Consumes: `timelineApi.range()`, `timelineApi.day()` (Task 9); `readProviders` (Task 9); `NOT_TOOL_SPECIFIC` lane name `"not_tool_specific"` (Task 8)
- Produces: `<TimelinePage />` at route `/timeline`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { TimelinePage } from "./TimelinePage";

const range = vi.fn(async () => ({
  since: "2026-01-01",
  until: "2026-03-31",
  bucket: "day" as const,
  cells: [
    { bucket: "2026-01-05", provider: "claude_code", kind: "conversation" as const, n: 12 },
    { bucket: "2026-01-05", provider: "hermes", kind: "conversation" as const, n: 3 },
    { bucket: "2026-02-01", provider: "not_tool_specific", kind: "skill" as const, n: 2 },
  ],
}));

vi.mock("@/lib/api", () => ({
  timelineApi: {
    range: (...a: unknown[]) => range(...(a as [])),
    day: async () => ({ day: "2026-01-05", items: [] }),
  },
}));

function renderAt(path = "/timeline") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TimelinePage />
    </MemoryRouter>,
  );
}

describe("TimelinePage", () => {
  it("renders one lane per provider present in the range", async () => {
    renderAt();
    expect(await screen.findByText("claude_code")).toBeTruthy();
    expect(screen.getByText("hermes")).toBeTruthy();
  });

  it("gives non-provider sources their own lane rather than dropping them", async () => {
    // Spec §5.3: all eight of the old Calendar's sources stay reachable.
    renderAt();
    expect(await screen.findByText(/not tool-specific/i)).toBeTruthy();
  });

  it("requests a date range, never a page", async () => {
    renderAt();
    await screen.findByText("claude_code");
    const qs = String(range.mock.calls.at(-1)?.[0] ?? "");
    expect(qs).toContain("since=");
    expect(qs).toContain("until=");
    expect(qs).not.toContain("page=");
    expect(qs).not.toContain("offset=");
  });

  it("carries the provider scope into the query", async () => {
    renderAt("/timeline?provider=hermes");
    await screen.findByText("hermes");
    expect(String(range.mock.calls.at(-1)?.[0] ?? "")).toContain("provider=hermes");
  });

  it("changing the range refetches", async () => {
    renderAt();
    await screen.findByText("claude_code");
    const before = range.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: /last year|1y/i }));
    expect(range.mock.calls.length).toBeGreaterThan(before);
  });

  it("shows an empty state rather than a blank grid", async () => {
    range.mockResolvedValueOnce({
      since: "2026-01-01", until: "2026-03-31", bucket: "day", cells: [],
    });
    renderAt();
    expect(await screen.findByText(/no activity in this range/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- TimelinePage`
Expected: FAIL — cannot resolve `./TimelinePage`

- [ ] **Step 3: Write the range control**

```tsx
/** Preset ranges plus explicit dates. The Timeline's own control — it no
 * longer borrows the search page's pagination, which was the actual defect. */
export interface Range {
  since: string;
  until: string;
}

const DAY = 86_400_000;

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function presetRange(days: number): Range {
  const until = new Date();
  return { since: iso(new Date(until.getTime() - days * DAY)), until: iso(until) };
}

export function RangeControl({
  value,
  onChange,
}: {
  value: Range;
  onChange: (r: Range) => void;
}) {
  const presets: Array<[string, number]> = [
    ["30d", 30],
    ["90d", 90],
    ["1y", 365],
    ["All", 3650],
  ];
  return (
    <div className="range-control" role="group" aria-label="Date range">
      {presets.map(([label, days]) => (
        <button
          key={label}
          type="button"
          className="range-preset"
          onClick={() => onChange(presetRange(days))}
        >
          {label}
        </button>
      ))}
      <label className="range-date">
        From
        <input
          type="date"
          value={value.since}
          onChange={(e) => onChange({ ...value, since: e.target.value })}
        />
      </label>
      <label className="range-date">
        To
        <input
          type="date"
          value={value.until}
          onChange={(e) => onChange({ ...value, until: e.target.value })}
        />
      </label>
    </div>
  );
}
```

- [ ] **Step 4: Write the page**

```tsx
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { timelineApi } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { readProviders } from "@/lib/providerScope";

import { RangeControl, presetRange, type Range } from "./RangeControl";

/**
 * Activity over a date range, in per-provider lanes.
 *
 * The previous Timeline rendered the current page of search results — 30 rows
 * by default — and called it a timeline. This asks the server for an
 * aggregate over an explicit range, so what you see is the range, whole.
 *
 * Lanes use INTENSITY, not categorical hue (design spec §5.2). Six validated
 * chart hues exist against nine providers; each lane already carries its
 * provider name, so hue would be redundant, and intensity is what a cell
 * actually means. Categorical colour stays on the provider chips, where a
 * label alone is not enough.
 */
const NOT_TOOL_SPECIFIC = "not_tool_specific";

function laneLabel(provider: string): string {
  if (provider === NOT_TOOL_SPECIFIC) return "not tool-specific";
  if (provider === "unattributed") return "(unattributed)";
  return provider;
}

export function TimelinePage() {
  const [sp] = useSearchParams();
  const [range, setRange] = useState<Range>(() => presetRange(90));
  const providers = readProviders(sp);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    p.set("since", range.since);
    p.set("until", range.until);
    for (const name of providers) p.append("provider", name);
    return p;
  }, [range.since, range.until, providers.join(",")]);

  const { data, isLoading } = useQuery({
    queryKey: ["timeline", qs.toString()],
    queryFn: () => timelineApi.range(qs),
  });

  const { lanes, buckets, max } = useMemo(() => {
    const cells = data?.cells ?? [];
    const laneSet = new Set<string>();
    const bucketSet = new Set<string>();
    const totals = new Map<string, number>();
    for (const c of cells) {
      laneSet.add(c.provider);
      bucketSet.add(c.bucket);
      const key = `${c.provider}|${c.bucket}`;
      totals.set(key, (totals.get(key) ?? 0) + c.n);
    }
    const ordered = [...laneSet].sort((a, b) =>
      a === NOT_TOOL_SPECIFIC ? 1 : b === NOT_TOOL_SPECIFIC ? -1 : a.localeCompare(b),
    );
    return {
      lanes: ordered,
      buckets: [...bucketSet].sort(),
      max: Math.max(1, ...totals.values()),
      totals,
    };
  }, [data]);

  const totals = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of data?.cells ?? []) {
      const key = `${c.provider}|${c.bucket}`;
      m.set(key, (m.get(key) ?? 0) + c.n);
    }
    return m;
  }, [data]);

  const grandTotal = (data?.cells ?? []).reduce((s, c) => s + c.n, 0);

  return (
    <section className="timeline-page">
      <header className="page-header">
        <h1>Timeline</h1>
        <p className="page-hint">
          {formatCount(grandTotal)} event(s) between {range.since} and {range.until}
          {data ? `, bucketed by ${data.bucket}` : ""}
        </p>
      </header>

      <RangeControl value={range} onChange={setRange} />

      {isLoading && <p className="muted">Loading…</p>}

      {!isLoading && lanes.length === 0 && (
        <p className="empty-state">No activity in this range.</p>
      )}

      {lanes.length > 0 && (
        <div className="timeline-grid" role="table" aria-label="Activity by provider over time">
          {lanes.map((lane) => (
            <div className="timeline-lane" role="row" key={lane}>
              <span className="timeline-lane-label" role="rowheader">
                {laneLabel(lane)}
              </span>
              <div className="timeline-cells">
                {buckets.map((b) => {
                  const n = totals.get(`${lane}|${b}`) ?? 0;
                  return (
                    <span
                      key={b}
                      role="cell"
                      className="timeline-cell"
                      style={{ opacity: n === 0 ? 0.08 : 0.25 + 0.75 * (n / max) }}
                      title={`${laneLabel(lane)} · ${b} · ${n}`}
                      aria-label={`${laneLabel(lane)}, ${b}, ${n} events`}
                    />
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
```

Add `.timeline-*` and `.range-*` styles to `web/src/styles/index.css` using the sequential ramp tokens. The cell background must come from one token with opacity varying — that is the intensity encoding.

- [ ] **Step 5: Wire the route and nav, remove the old component**

- Add `{ to: "/timeline", label: "Timeline", icon: CalendarRange, chord: "t", hint: "Activity over time" }` to `NAV` in `web/src/lib/nav.ts` (import `CalendarRange` from `lucide-react`).
- Add the `/timeline` route beside the others.
- Delete `web/src/features/find/ResultTimeline.tsx` and every import of it.
- In `useFindState.ts`, drop `"timeline"` from `ViewMode` and from the mode whitelist in `parseFindState`, leaving `list | table | graph`.
- Remove the Timeline entry from Find's view-mode switcher.

- [ ] **Step 6: Run tests, typecheck, build**

```bash
cd web && npm test && npm run typecheck && npm run build
```

Expected: PASS, no type errors, no dangling import of `ResultTimeline`

- [ ] **Step 7: Verify bar 6 in the browser**

Open `/timeline`, pick "All", and confirm the header event count matches:

```bash
psql -d claude_memory -tAc "SELECT count(*) FROM conversations"
```

for a conversations-only view. A mismatch means bar 6 has failed.

- [ ] **Step 8: Commit**

```bash
git add web/src/features/timeline/ web/src/lib/nav.ts web/src/features/find/ \
        web/src/styles/index.css
git rm web/src/features/find/ResultTimeline.tsx
git commit -m "feat(web): Timeline becomes its own surface over a date range"
```

---

### Task 12: Coverage in Overview and Operate

Spec §4.3 — the remaining two consumers. Closes the gap that let 8,453 parseable messages sit on disk unmentioned.

**Files:**
- Modify: `throughline/api/routers/overview.py:66-190` — add the un-ingested attention item
- Modify: `web/src/features/operate/OperatePage.tsx` — add the provider coverage table
- Modify: `throughline/api/jobs.py:101-146` — add per-adapter ingest jobs
- Modify: `throughline/queries/providers.py` — invalidate the cache after an ingest
- Test: `tests/integration/test_api_overview_providers.py`

**Interfaces:**
- Consumes: `Q.coverage()`, `Q.invalidate_scan_cache()` (Task 6); `ProviderCoverage` (Task 9)
- Produces: an `AttentionItem` per un-ingested provider; job names `ingest_<provider>`

- [ ] **Step 1: Write the failing test**

```python
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

    monkeypatch.setattr(
        Q, "_disk_scan",
        lambda: {
            "hermes": Q.DiskCounts(on_disk=33, pending=33, excluded=0, present=True),
            "vibe": Q.DiskCounts(on_disk=15, pending=15, excluded=0, present=True),
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/integration/test_api_overview_providers.py -v`
Expected: FAIL — no Hermes attention item, no `ingest_hermes` job

- [ ] **Step 3: Add the attention item**

In `throughline/api/routers/overview.py`, inside `_build_overview`, after the existing checks:

```python
    from throughline.queries import providers as PQ

    for row in PQ.coverage(conn):
        if row["status"] != "not_ingested":
            continue
        attention.append(
            AttentionItem(
                id=f"provider-not-ingested-{row['name']}",
                severity="warning",
                title=f"{row['label']}: {row['pending']} file(s) on disk, 0 ingested",
                detail=(
                    f"{row['label']} data is present and parseable but has never been "
                    f"imported. Run the {row['label']} ingest to bring it in."
                ),
                count=row["pending"],
                action="/operate",
                action_label=f"Ingest {row['label']}",
            )
        )
```

`AttentionItem` (defined at `throughline/api/routers/overview.py:30`) has exactly these fields: `id`, `severity`, `title`, `detail`, `count`, `action`, `action_label`. `id` is required and `action` is *a UI route, not prose* — its own docstring says so. There is no `href` field; do not add one.

- [ ] **Step 4: Add per-provider ingest jobs**

In `throughline/api/jobs.py`, after the static `JOBS` dict, generate one job per provider so the Operate table can offer a targeted ingest:

```python
def _per_provider_jobs() -> dict[str, JobSpec]:
    """One ingest job per adapter.

    Targeted rather than `--all` because the Overview item that surfaces an
    un-ingested source should lead to importing exactly that source.
    """
    from throughline import providers as P

    return {
        f"ingest_{p.name}": JobSpec(
            f"ingest_{p.name}",
            f"Ingest {p.label}",
            f"Import new {p.label} sessions.",
            _cli("ingest", "--source", p.name),
        )
        for p in P.PROVIDERS
    }


JOBS.update(_per_provider_jobs())
```

Confirm `throughline ingest --source <name>` is the real CLI flag before writing this; check `throughline/cli.py` and use whatever the actual flag is.

- [ ] **Step 5: Invalidate the coverage cache when an ingest finishes**

In `throughline/api/jobs.py`, in `JobRunner._pump`, inside the `finally` block:

```python
            if job.name.startswith("ingest"):
                from throughline.queries import providers as PQ

                PQ.invalidate_scan_cache()
```

Without this the coverage table keeps reporting the pre-ingest counts for up to 60 seconds after a successful import, which reads as "the ingest did nothing".

- [ ] **Step 6: Add the Operate table**

In `web/src/features/operate/OperatePage.tsx`, add a section above the jobs list rendering `providersApi.list()` as a table with columns Provider · On disk · Pending · Excluded · Ingested · Last run · Status, and a Run button per row wired to `operateApi.run(\`ingest_${p.name}\`)`. Give `excluded` a tooltip reading "discovered but not ingested (subagent transcripts)". Use `<th scope="col">` and a caption so the table is readable by screen reader.

- [ ] **Step 7: Run everything**

```bash
python3 -m pytest tests/integration/test_api_overview_providers.py \
                  tests/integration/test_api_overview.py \
                  tests/integration/test_api_operate.py -v
cd web && npm test && npm run typecheck && npm run build
```

Expected: PASS throughout

- [ ] **Step 8: Commit**

```bash
git add throughline/api/routers/overview.py throughline/api/jobs.py \
        throughline/queries/providers.py web/src/features/operate/OperatePage.tsx \
        tests/integration/test_api_overview_providers.py
git commit -m "feat: surface un-ingested providers in Overview and Operate"
```

---

## Final verification — the pre-registered acceptance bars

Run these after Task 12. Spec §6: each is binary, and a miss is not re-scoped afterwards.

- [ ] **Bar 1** — every conversation has `source_tool` set or explicitly NULL; the Claude Code rows report `claude_code`:
  ```bash
  psql -d claude_memory -c \
    "SELECT COALESCE(source_tool,'(null)'), count(*) FROM conversations GROUP BY 1 ORDER BY 2 DESC"
  ```
  Expect no `cli` or `sdk-cli` value.

- [ ] **Bar 2** — re-running the migration changes zero rows:
  ```bash
  psql -d claude_memory -f sql/migrations/002_source_tool.sql
  ```
  Every `UPDATE` reports `UPDATE 0`.

- [ ] **Bar 3** — `python3 -m pytest tests/test_adapters_write_source_tool.py -v`

- [ ] **Bar 4** — `curl -s localhost:8787/api/providers | python3 -m json.tool` shows Hermes and Vibe `not_ingested` with non-zero `pending`, `cline` `no_data`, `claude_code` with `pending` and `excluded` ≈ 98.

- [ ] **Bar 4a** — `python3 -m pytest tests/integration/test_subagent_exclusion.py -v`, then a real `throughline ingest --all` followed by:
  ```bash
  psql -d claude_memory -tAc \
    "SELECT count(*) FROM (SELECT session_id FROM conversations GROUP BY session_id HAVING count(*)>1) x"
  ```
  Expect `0`.

- [ ] **Bar 5** — by hand: select a provider on Find, navigate to Overview, Curate and Operate via sidebar and `g` chords, confirm the scope persists and bar and facet agree. Console shows no bar.

- [ ] **Bar 6** — lane totals equal the raw count **on the real database**:
  ```bash
  curl -s "localhost:8787/api/timeline?since=2020-01-01&until=2030-01-01&kind=conversation" \
    | python3 -c "import json,sys; print(sum(c['n'] for c in json.load(sys.stdin)['cells']))"
  psql -d claude_memory -tAc "SELECT count(*) FROM conversations"
  ```
  The two numbers must be identical.

- [ ] **Bar 7** — `python3 -m pytest tests/integration/test_conflicts_cross_tool.py -v`, and the changelog entry from Task 4 records the count change.

- [ ] **Full suite** — `python3 -m pytest -v` and `cd web && npm test && npm run build`

---

## Self-review notes

**Spec coverage.** §3.1/§3.3 → Task 2. §3.2 → Task 3. §3.5 → Task 1. §3.6 → Task 4. §3.4 (inheritance through the join, no denormalisation) → Task 7 step 4 and Task 8's `_SOURCES`. §4.1/§4.2 → Tasks 9, 10. §4.3 → Tasks 6, 10, 12. §4.4/§4.4a → Task 5. §4.5 → Task 6 (`CACHE_TTL_SECONDS`) and Task 12 step 5 (invalidation). §5.1 → Task 8. §5.2/§5.3/§5.4 → Task 11. §6 bars 1–7 → the final checklist. §7 testing → each task's tests, including the subagent regression test. §9.1's sequencing constraint is enforced structurally, not just by instruction: `discover()` is derived from `discover_all()` minus `excluded_reason()`, so a recursive walk cannot reach the writer unfiltered. §9.3 is out of scope by the spec's own statement and appears only as a docstring pointer in Task 5.

**Assumptions checked against the code while reviewing, and four corrections made.** The first draft of this plan had real errors, fixed above:

1. Both new routers used a `Depends(get_conn)` dependency that does not exist. `deps.py` exposes `connection(settings)` as a context manager; every router takes `settings: Settings = Depends(get_settings)` and opens `with connection(settings) as conn:`. Corrected in Tasks 6 and 8.
2. The Overview attention item passed `href=`, which is not a field. `AttentionItem` (`overview.py:30`) is `id, severity, title, detail, count, action, action_label`, `id` is required, and `action` is a route. Corrected in Task 12, including the test that asserted the non-existent field.
3. `skills` has no single event timestamp — `activity.py` coalesces `file_modified, last_used, created_at`, and the Timeline must do the same or it silently drops skills that were never used. Corrected in Task 8's `_SOURCES`.
4. `throughline ingest --source NAME` is confirmed real (`cli.py:105-147`), so Task 12's per-provider jobs stand as written.

**Deliberate scope choice, worth a second opinion before Task 7 lands.** When a provider filter is active, kinds with no provider dimension (`skill`, `project`, `prompt`) return nothing from Find, on the reasoning that "show me Hermes" should not return every skill. The Timeline takes the opposite approach and keeps them in a "not tool-specific" lane, because a timeline is about coverage over time. The asymmetry is intentional but it is the one decision here a reviewer might reasonably overturn.

**Deliberate scope choice, worth a second opinion before Task 7 lands.** When a provider filter is active, kinds with no provider dimension (`skill`, `project`, `prompt`) return nothing from Find, on the reasoning that "show me Hermes" should not return every skill. The Timeline takes the opposite approach and keeps them in a "not tool-specific" lane, because a timeline is about coverage over time. The asymmetry is intentional but it is the one decision here a reviewer might reasonably overturn.
