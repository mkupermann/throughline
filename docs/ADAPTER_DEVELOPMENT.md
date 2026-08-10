# Adapter Development Guide

Throughline supports new AI CLI tools through adapters. An adapter is a small
Python class that knows one tool's on-disk session format and converts it into
Throughline's normalised model. Everything downstream — database writes,
idempotency, project bucketing — is shared infrastructure.

## The contract

An adapter subclasses `throughline.adapters.base.Adapter` and provides:

| Member | Purpose |
|---|---|
| `name` | Machine identifier, used as `--source <name>` and as `conversations.source` |
| `label` | Human-readable name shown in listings and the GUI |
| `home` | The directory the adapter scans; `is_present` checks its existence cheaply |
| `discover()` | Yield candidate conversation files (no parsing) |
| `parse(path)` | Convert one file into a `NormalisedConversation`, or `None` to skip |

```python
from pathlib import Path
from typing import Iterable

from throughline.adapters.base import (
    Adapter,
    NormalisedConversation,
    NormalisedMessage,
)


class MyToolAdapter(Adapter):
    name = "my_tool"
    label = "My Tool"
    home = Path("~/.my_tool/sessions").expanduser()

    def discover(self) -> Iterable[Path]:
        if not self.home.exists():
            return
        yield from sorted(self.home.glob("*.json"))

    def parse(self, path: Path) -> NormalisedConversation | None:
        ...
```

## Normalisation rules

- **`session_id` must be a UUID string.** If the source tool's identifiers are
  not UUIDs, derive one deterministically with `uuid.uuid5` from a stable key
  (for example the file path or the tool's own session id). Determinism is what
  keeps re-ingestion idempotent.
- **`role`** must be one of `user`, `assistant`, `system`, `tool_result` —
  these map to the database enum. Map tool-specific roles onto these four.
- **`content`** is the rendered plain text shown in the GUI. Preserve the
  original structured payload in `content_blocks` so nothing is lost.
- **Timestamps** should be timezone-aware `datetime` objects when the source
  provides them; leave `None` when it does not.
- **Malformed input must not raise.** A corrupt file should log and return
  `None` from `parse()`. One bad session file must never abort a full ingest.

## Idempotency

The shared writer records every ingested file in `ingestion_log` with a content
hash. On re-ingestion:

- unchanged files are skipped without parsing,
- changed files are re-parsed and their conversation is refreshed in place
  (messages replaced, not duplicated).

Adapters get this behaviour for free — but only if `session_id` derivation is
deterministic (see above).

## Registration

Built-in adapters are listed explicitly in
`throughline/adapters/registry.py` (`_BUILTIN_PATHS`) so import errors surface
immediately and load order is stable.

Third-party adapters ship as ordinary pip packages using the entry point —
no changes to Throughline required:

```toml
[project.entry-points."throughline.adapters"]
my_tool = "my_pkg.adapter:MyToolAdapter"
```

## Testing

Every adapter has a matching test module `tests/test_adapter_<name>.py`.
Follow the existing patterns:

1. Build a minimal fixture of the tool's real on-disk format in `tmp_path`.
2. Point the adapter at it (override `home`).
3. Assert `discover()` finds the fixture and `parse()` produces the expected
   `NormalisedConversation` (roles, content, session id, timestamps).
4. Add at least one malformed-input case asserting `parse()` returns `None`
   instead of raising.

End-to-end coverage (adapter → writer → PostgreSQL) lives in
`tests/integration/test_adapter_e2e.py` and requires a reachable PostgreSQL
(`docker compose up -d postgres`).

## Checklist for a new adapter PR

- [ ] Adapter module in `throughline/adapters/<name>.py`
- [ ] Deterministic `session_id` derivation
- [ ] Graceful handling of malformed files
- [ ] Registered (built-in list or entry point)
- [ ] Unit tests including a malformed-input case
- [ ] Row added to the support table in `README.md`
