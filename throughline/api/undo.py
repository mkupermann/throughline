"""Short-lived undo tokens.

Scope, stated plainly: this is an in-process registry with a TTL. Tokens do
not survive a server restart and are not an audit trail — ``memory_reflections``
is the durable record of what happened.

That is an acceptable trade only because every mutation behind a token is
*already* reversible in the database: forgetting sets a status rather than
deleting a row, so losing a token costs a trip to the Forgotten queue, not
data. If an irreversible action is ever added, it must not be given an undo
token — it needs a confirmation dialog instead.

Idempotency keys live here too, so a double-submitted mutation (an impatient
second click, a retried request) applies once and returns the original result
rather than forgetting the same chunks twice and stacking two inverses.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

#: How long an undo token stays valid. The UI toast shows 5s; the token lives
#: far longer so a user who reaches for the button late still succeeds rather
#: than meeting an error they can do nothing about.
TTL_SECONDS = 120

#: Bound on retained tokens, so a long-running server cannot grow unboundedly.
MAX_TOKENS = 500


@dataclass
class UndoEntry:
    token: str
    op: str
    payload: dict[str, Any]
    label: str
    created_at: float = field(default_factory=time.monotonic)
    used: bool = False


class UndoRegistry:
    def __init__(self, ttl: float = TTL_SECONDS, max_tokens: int = MAX_TOKENS) -> None:
        self._ttl = ttl
        self._max = max_tokens
        self._lock = threading.Lock()
        self._entries: dict[str, UndoEntry] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}

    # ── undo tokens ────────────────────────────────────────────────────────

    def register(self, inverse: dict[str, Any] | None, label: str) -> str | None:
        """Store an inverse operation and return its token, or None."""
        if not inverse:
            return None
        op = inverse.get("op")
        if not op:
            return None
        payload = {k: v for k, v in inverse.items() if k != "op"}
        token = uuid.uuid4().hex
        with self._lock:
            self._prune_locked()
            self._entries[token] = UndoEntry(token=token, op=op, payload=payload, label=label)
        return token

    def take(self, token: str) -> UndoEntry | None:
        """Claim a token exactly once. Returns None if unknown, used or expired."""
        with self._lock:
            self._prune_locked()
            entry = self._entries.get(token)
            if entry is None or entry.used:
                return None
            if time.monotonic() - entry.created_at > self._ttl:
                self._entries.pop(token, None)
                return None
            entry.used = True
            return entry

    # ── idempotency ────────────────────────────────────────────────────────

    def seen(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        with self._lock:
            return self._idempotency.get(key)

    def remember(self, key: str | None, result: dict[str, Any]) -> None:
        if not key:
            return
        with self._lock:
            if len(self._idempotency) > self._max:
                self._idempotency.clear()
            self._idempotency[key] = result

    # ── internals ──────────────────────────────────────────────────────────

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [t for t, e in self._entries.items() if now - e.created_at > self._ttl or e.used]
        for t in expired:
            self._entries.pop(t, None)
        if len(self._entries) > self._max:
            for t in sorted(self._entries, key=lambda k: self._entries[k].created_at)[
                : len(self._entries) - self._max
            ]:
                self._entries.pop(t, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._idempotency.clear()


registry = UndoRegistry()
