#!/usr/bin/env python3
"""Run the throughline evaluation harness.

For each question in --questions, ask Claude twice:

  1. **with-memory**: pass top-k results from `memory.search` as context.
  2. **cold**: ask the question with no retrieved context.

Score each answer by checking whether *any* of the question's
``expected_substrings`` appear (case-insensitive) in the answer body. Write
a Markdown report to --report with per-question detail and headline numbers.

This is intentionally NOT an LLM-judge eval — the substring ground truth
is human-authored, so the scores are coarse but objective.

Usage
-----

    python evals/run_eval.py \
        --questions evals/questions.jsonl \
        --report evals/last_run.md \
        --top-k 5

    # Print what would be asked, do not call any LLM:
    python evals/run_eval.py --questions evals/questions.jsonl --dry-run

Auth
----

Either set ``ANTHROPIC_API_KEY`` (uses the SDK if installed) or have the
``claude`` CLI on ``$PATH`` (used in headless ``claude -p`` mode). The
runner auto-detects.

"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Reuse the live MCP search implementation so this harness measures the same
# retrieval the agent gets at runtime (not a parallel implementation that
# could drift).
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for sub in ("scripts", "gui", str(_REPO)):
    p = str(_REPO / sub) if sub != str(_REPO) else sub
    if p not in sys.path:
        sys.path.insert(0, p)

# memory_search is imported lazily inside retrieve(): the harness must be
# usable in --dry-run / --offline-stub modes on a CI runner that has no DB,
# no Postgres client, and no `mcp` SDK installed. Importing at module level
# fights every one of those.
memory_search = None  # type: ignore[assignment]


def _load_memory_search():
    """Import memory_mcp.server.search only when really needed."""
    global memory_search
    if memory_search is not None:
        return memory_search
    from memory_mcp.server import search as _search  # type: ignore
    memory_search = _search
    return memory_search


@dataclass
class Question:
    id: str
    category: str
    question: str
    expected_substrings: list[str]
    scope_project: str | None = None
    notes: str = ""


@dataclass
class Result:
    qid: str
    condition: str  # "with-memory" | "cold"
    answer: str
    retrieved_ids: list[int] = field(default_factory=list)
    hit: bool = False
    matched_substring: str | None = None


# ── LLM dispatch ─────────────────────────────────────────────────────────────
def call_claude(prompt: str, *, model: str = "sonnet") -> str:
    """Call Claude. Prefer the SDK if available, fall back to the CLI."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # type: ignore
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model="claude-sonnet-4-6" if model == "sonnet" else model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        except Exception as e:
            print(f"[eval] anthropic SDK call failed: {e}; falling back to CLI", file=sys.stderr)

    cli = shutil.which("claude")
    if not cli:
        raise RuntimeError(
            "Neither ANTHROPIC_API_KEY (with anthropic SDK) nor the `claude` CLI is available. "
            "Set one or the other before running."
        )
    proc = subprocess.run(
        [cli, "-p", prompt, "--model", model],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr[-500:]}")
    return proc.stdout.strip()


# ── Retrieval ────────────────────────────────────────────────────────────────
def retrieve(query: str, *, project: str | None, top_k: int) -> list[dict]:
    """Call memory.search the same way the MCP server does at runtime.

    Best-effort: returns ``[]`` if memory_mcp / Postgres / pgvector are
    unavailable, so ``--offline-stub`` and ``--dry-run`` callers can keep
    going. The retrieval *failure* is interesting in CI smoke tests
    (proves the runner doesn't crash), and a real eval run would notice
    immediately because every question would miss.
    """
    try:
        search_fn = _load_memory_search()
    except Exception as e:
        print(f"[eval] retrieval skipped — memory_search unavailable: {e}",
              file=sys.stderr)
        return []
    try:
        return search_fn(
            query=query,
            scope=["memory", "messages"],
            project=project if project else "",
            limit=top_k,
        )
    except Exception as e:
        print(f"[eval] retrieval skipped — backend error: {e}",
              file=sys.stderr)
        return []


def format_context(rows: list[dict]) -> str:
    if not rows:
        return "(no memory chunks retrieved)"
    parts = []
    for r in rows:
        cat = r.get("category", "?")
        proj = r.get("project_name") or r.get("project") or "—"
        content = (r.get("content") or "").replace("\n", " ").strip()
        if len(content) > 400:
            content = content[:400] + "…"
        parts.append(f"- [{cat} · {proj}] {content}")
    return "\n".join(parts)


# ── Scoring ──────────────────────────────────────────────────────────────────
def grade(answer: str, expected: Iterable[str]) -> tuple[bool, str | None]:
    body = (answer or "").lower()
    for needle in expected:
        if needle.lower() in body:
            return True, needle
    return False, None


# ── Main loop ────────────────────────────────────────────────────────────────
def load_questions(path: Path) -> list[Question]:
    out: list[Question] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
                out.append(Question(
                    id=d["id"],
                    category=d.get("category", "?"),
                    question=d["question"],
                    expected_substrings=list(d.get("expected_substrings") or []),
                    scope_project=d.get("scope_project"),
                    notes=d.get("notes", ""),
                ))
            except Exception as e:
                print(f"[eval] {path}:{line_no}: skipped — {e}", file=sys.stderr)
    return out


def _stub_answer(q: Question, *, with_memory: bool) -> str:
    """Deterministic offline pretend-LLM.

    The with-memory condition emits the first expected_substring (so
    grade() hits) — pretending memory let it answer. The cold condition
    emits a fixed "I do not know" so it misses. Lets CI prove that the
    grader, retrieval glue, and reporting all work without network.
    """
    if with_memory and q.expected_substrings:
        return f"[offline-stub] {q.expected_substrings[0]}"
    return "[offline-stub] I do not know."


def run_one(q: Question, *, top_k: int, dry_run: bool, offline_stub: bool) -> tuple[Result, Result]:
    rows: list[dict] = []
    if not dry_run:
        rows = retrieve(q.question, project=q.scope_project, top_k=top_k)
    retrieved_ids = [int(r["source_id"]) for r in rows if r.get("source_id") is not None]
    ctx = format_context(rows)

    with_prompt = (
        "You are answering a factual question about the throughline / claude-memory-db project.\n"
        f"Relevant memory chunks (top-{top_k}):\n{ctx}\n\n"
        f"Question: {q.question}\nAnswer concisely (1-3 sentences)."
    )
    cold_prompt = (
        "You are answering a factual question about the throughline / claude-memory-db project.\n"
        f"Question: {q.question}\nAnswer concisely (1-3 sentences). "
        "If you do not know, say so."
    )

    if dry_run:
        print(f"[{q.id}] would retrieve top-{top_k}; would prompt twice (dry-run).")
        return (
            Result(qid=q.id, condition="with-memory", answer="(dry-run)", retrieved_ids=retrieved_ids),
            Result(qid=q.id, condition="cold", answer="(dry-run)"),
        )

    if offline_stub:
        with_ans = _stub_answer(q, with_memory=True)
        cold_ans = _stub_answer(q, with_memory=False)
    else:
        with_ans = call_claude(with_prompt)
        cold_ans = call_claude(cold_prompt)

    with_hit, with_match = grade(with_ans, q.expected_substrings)
    cold_hit, cold_match = grade(cold_ans, q.expected_substrings)

    return (
        Result(qid=q.id, condition="with-memory", answer=with_ans, retrieved_ids=retrieved_ids,
               hit=with_hit, matched_substring=with_match),
        Result(qid=q.id, condition="cold", answer=cold_ans,
               hit=cold_hit, matched_substring=cold_match),
    )


def write_report(path: Path, qs: list[Question], pairs: list[tuple[Result, Result]]) -> None:
    n = len(qs)
    with_hits = sum(1 for w, _ in pairs if w.hit)
    cold_hits = sum(1 for _, c in pairs if c.hit)
    delta = with_hits - cold_hits

    lines: list[str] = []
    lines.append("# Throughline eval — last run\n")
    lines.append(f"- with-memory recall: **{with_hits}/{n}**")
    lines.append(f"- cold recall: **{cold_hits}/{n}**")
    lines.append(f"- delta: **{delta:+d}**\n")

    lines.append("## Per-question table\n")
    lines.append("| ID | Category | with-memory | cold | retrieved |")
    lines.append("|---|---|---|---|---|")
    for q, (w, c) in zip(qs, pairs):
        lines.append(
            f"| {q.id} | {q.category} | "
            f"{'✓ ' + (w.matched_substring or '') if w.hit else '✗'} | "
            f"{'✓ ' + (c.matched_substring or '') if c.hit else '✗'} | "
            f"{','.join(str(i) for i in w.retrieved_ids[:5]) or '—'} |"
        )

    misses = [(q, w) for q, (w, _) in zip(qs, pairs) if not w.hit and q.category != "control"]
    if misses:
        lines.append("\n## Misses worth investigating\n")
        for q, w in misses:
            lines.append(f"### {q.id} — {q.question}")
            lines.append(f"- Expected any of: `{', '.join(q.expected_substrings)}`")
            lines.append(f"- Retrieved IDs: `{w.retrieved_ids}`")
            lines.append(f"- Answer:\n  > {w.answer}\n")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, default=_HERE / "questions.jsonl")
    ap.add_argument("--report", type=Path, default=_HERE / "last_run.md")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true",
                    help=("Parse questions, do not retrieve, do not call any "
                          "LLM. Exits 0 if the file parses. Safe in CI "
                          "without DB or API keys."))
    ap.add_argument("--offline-stub", action="store_true",
                    help=("Use a deterministic pretend-LLM that always emits "
                          "the first expected substring in the with-memory "
                          "condition and 'I do not know.' in the cold "
                          "condition. Smoke-tests the harness end-to-end "
                          "without spending tokens or needing API keys."))
    args = ap.parse_args()

    if args.dry_run and args.offline_stub:
        print("[eval] --dry-run and --offline-stub are mutually exclusive.",
              file=sys.stderr)
        return 2

    qs = load_questions(args.questions)
    if not qs:
        print(f"[eval] no questions loaded from {args.questions}", file=sys.stderr)
        return 2

    print(
        f"[eval] {len(qs)} question(s); top-k={args.top_k}; "
        f"dry_run={args.dry_run}; offline_stub={args.offline_stub}"
    )
    pairs: list[tuple[Result, Result]] = []
    for q in qs:
        try:
            pair = run_one(
                q,
                top_k=args.top_k,
                dry_run=args.dry_run,
                offline_stub=args.offline_stub,
            )
        except Exception as e:
            print(f"[eval] {q.id}: failed — {e}", file=sys.stderr)
            pair = (
                Result(qid=q.id, condition="with-memory", answer=f"ERROR: {e}"),
                Result(qid=q.id, condition="cold", answer=f"ERROR: {e}"),
            )
        pairs.append(pair)
        w, c = pair
        if not args.dry_run:
            print(f"  {q.id}: with={'✓' if w.hit else '✗'}  cold={'✓' if c.hit else '✗'}")

    if args.dry_run:
        return 0

    write_report(args.report, qs, pairs)
    print(f"[eval] wrote {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
