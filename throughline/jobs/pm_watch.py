"""Pure parsers over a pipeline.sh run directory.

Kept dependency-free (no DB, no psycopg2) on purpose: these are exactly the
functions the watcher loop (Task 8) calls every ~10s, and the fastest way to
verify parsing logic against the real log format captured from
razor1911-demo-tribute on 2026-08-25 is a plain pytest file with tmp_path
fixtures — no database needed to test text parsing.
"""

from __future__ import annotations

import re
from pathlib import Path

_VERDICT_RE = re.compile(r"^VERDICT:\s*(PASS|FAIL)(?::\s*(.*))?", re.MULTILINE)
# Aider prints e.g. "Tokens: 3.4k sent, 130 received." — the "k" suffix
# needs its own branch since int() can't parse it directly.
_TOKENS_RE = re.compile(r"Tokens:\s*([\d.]+)(k)?\s*sent,\s*([\d.]+)(k)?\s*received")


def parse_spec(log_dir: Path) -> str | None:
    spec = log_dir / "SPEC.md"
    if not spec.is_file():
        return None
    return spec.read_text(encoding="utf-8")


def latest_iteration(log_dir: Path) -> int:
    highest = 0
    for f in log_dir.glob("executor-*.log"):
        try:
            n = int(f.stem.split("-")[1])
        except (IndexError, ValueError):
            continue
        highest = max(highest, n)
    return highest


def parse_verdict(log_dir: Path, iteration: int) -> tuple[str, str] | None:
    verdict_file = log_dir / f"verdict-{iteration}.txt"
    if not verdict_file.is_file():
        return None
    text = verdict_file.read_text(encoding="utf-8")
    match = _VERDICT_RE.search(text)
    if not match:
        return None
    status = "pass" if match.group(1) == "PASS" else "fail"
    return status, text.strip()


def _to_int(value: str, suffix: str | None) -> int:
    n = float(value)
    if suffix == "k":
        n *= 1000
    return int(n)


def extract_aider_tokens(log_text: str) -> int:
    total = 0
    for sent_val, sent_k, recv_val, recv_k in _TOKENS_RE.findall(log_text):
        total += _to_int(sent_val, sent_k) + _to_int(recv_val, recv_k)
    return total
