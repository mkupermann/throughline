# Throughline Evals

This directory holds the evaluation harness for the question:

> Does throughline's memory layer measurably improve the agent's answers
> compared to the same agent without it?

The harness is **scaffolded but not yet run**. The 30 starter questions in
`questions.jsonl` are seed material — extend and tailor them to your own
session history before drawing conclusions.

## Method

For each question we run two conditions:

| Condition | What runs |
|---|---|
| **with-memory** | Issue an MCP-style `memory.search(query)` against the live `claude_memory` DB; pass the top-k chunks to Claude as context; ask the question. |
| **without-memory** | Ask Claude the same question with **no** retrieved context (cold). |

Each condition's answer is graded against the question's `expected_substrings`
(any-of match, case-insensitive) — a coarse but objective recall metric.
Aggregate score: `recall@k = matched / total`.

The harness is deliberately **not** an end-to-end LLM-judge eval. The point
is to be honest about whether retrieval helps — `expected_substrings` is the
ground truth a human authored, not a second LLM's opinion.

## Running

```bash
python evals/run_eval.py --questions evals/questions.jsonl --report evals/last_run.md
```

Required:
- A populated `claude_memory` DB.
- An OpenAI or Ollama embedding backend (for `memory.search`).
- Either `ANTHROPIC_API_KEY` (uses the SDK) or the `claude` CLI on `$PATH`
  (used in headless `claude -p` mode). The runner auto-detects.

`--dry-run` prints what would be asked without calling any LLM. Use it once
to confirm your question set parses cleanly.

## Question format

`questions.jsonl` is one JSON object per line:

```json
{
  "id": "Q01",
  "category": "decision",
  "question": "What did we decide about pgvector vs Milvus?",
  "expected_substrings": ["pgvector", "HNSW"],
  "scope_project": null,
  "notes": "Should retrieve the migration discussion from project X."
}
```

Fields:

- `id` — short identifier, used in the report.
- `category` — one of `decision`, `pattern`, `insight`, `preference`,
  `contact`, `error_solution`, `project_context`, `workflow`. Helps slice
  the score by memory type.
- `question` — the natural-language prompt fed to Claude in both conditions.
- `expected_substrings` — a list of strings; any one of them appearing
  (case-insensitive) in the answer counts as a hit.
- `scope_project` — optional. If set, the with-memory condition restricts
  `memory.search` to this project name. Useful for "would the project-scoped
  agent answer correctly without leaking to other projects?".
- `notes` — free-form, never sent to the LLM.

## What "good" looks like

A useful memory layer should beat cold by at least **+15 recall points** on
this set, **without regressing** on questions where memory shouldn't help
(controls — questions that don't reference any session content). If recall
is the same or worse with memory, the retrieval ranking needs work, not the
LLM.

## Known limitations

- `expected_substrings` is a brittle proxy. A genuinely correct paraphrase
  that doesn't include the substring counts as a miss.
- One DB snapshot per run — if your DB changes between runs, scores aren't
  directly comparable. Pin a snapshot via `--db-snapshot evals/snapshots/...`
  for repeatable comparisons (snapshot tooling is on the roadmap).
- 30 questions is a starter set; statistical significance over noise needs
  100+. The number is intentionally small so you can hand-author and audit
  every question.

## Reporting

`run_eval.py` writes a Markdown report to `--report` with:

- Headline: `with-memory recall: X/30 · cold recall: Y/30 · delta: ±Z`.
- Per-question table with both answers, retrieved chunk IDs, hit/miss.
- A `## Misses worth investigating` section listing questions where memory
  was expected to help but didn't.

**Commit your run reports.** A trail of `last_run.md` snapshots in PRs is
how the project demonstrates that retrieval still works after schema
changes, embedding-backend swaps, and reflection-engine tweaks.
