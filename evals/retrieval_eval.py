#!/usr/bin/env python3
"""Measure retrieval honestly: questions the author did not write.

The standing criticism of every local-RAG demo, and it was true of this one:
the person building it tests with terms they already know are in the corpus.
One team reported ~90% recall in development and ~30% once real users asked,
because users arrive with different vocabulary. Hand-written eval questions
measure the author's memory of the data, not the retriever.

So the questions here are generated FROM the corpus, one per sampled record,
by a model that sees only that record. Each question therefore has exactly one
known-correct answer — the record it was written from — and the measurement is
whether `ask.retrieve` puts that record in the top k. The author never picks
the vocabulary, which is the whole point.

Two things this deliberately does NOT claim:

- It is not a measure of answer quality, only of retrieval. If the right
  record never arrives, no model can answer; if it does, that is the part
  this file is about.
- Questions written from a record are still easier than questions asked from
  memory weeks later — a generated question tends to reuse the record's own
  nouns. So treat the number as an UPPER BOUND. A poor score here is
  conclusive; a good one is necessary, not sufficient.

    python3 evals/retrieval_eval.py --sample 40 --top-k 12

Writes a JSON report and prints recall@k, plus the misses, so a bad question
can be inspected rather than averaged away.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from throughline import ask as _ask  # noqa: E402
from throughline import llm as _llm  # noqa: E402
from throughline.self_referential import agent_call_cwd  # noqa: E402
from throughline.status import _connect  # noqa: E402

#: Records shorter than this rarely support a question with one clear answer.
_MIN_CHARS = 200

_QUESTION_PROMPT = (
    "Below is one record from someone's working history.\n\n"
    "Write the single question this record answers — the question that person "
    "might type weeks later, having forgotten the details.\n\n"
    "Rules:\n"
    "- Ask it the way someone recalling it vaguely would, not the way the "
    "record words it. Avoid reusing its distinctive nouns where a normal "
    "person would use a general word.\n"
    "- One question. No preamble, no quotes, no explanation.\n"
    "- Same language as the record.\n\n"
    "RECORD:\n{record}\n\nQUESTION:"
)


@dataclass
class Trial:
    chunk_id: int
    question: str
    rank: int | None = None  # 1-based position of the source record, or None

    @property
    def hit(self) -> bool:
        return self.rank is not None


@dataclass
class Report:
    top_k: int
    trials: list[Trial] = field(default_factory=list)
    skipped: int = 0

    @property
    def recall(self) -> float:
        return (sum(1 for t in self.trials if t.hit) / len(self.trials)) if self.trials else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        """95% Wilson interval for the recall estimate.

        Twenty questions do not measure a percentage, they measure a range —
        at n=20 the interval around 75% spans roughly 53%–89%. Reporting the
        point estimate alone is how a sample of twenty becomes a claim, and
        the whole reason this file exists is that the previous number was a
        claim rather than a measurement.
        """
        n = len(self.trials)
        if not n:
            return (0.0, 0.0)
        z = 1.96
        p = self.recall
        denom = 1 + z * z / n
        centre = (p + z * z / (2 * n)) / denom
        spread = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
        return (max(0.0, centre - spread), min(1.0, centre + spread))

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank — rewards putting the record near the top, not
        merely inside the window. A record at rank 11 of 12 reaches the model
        with eleven distractions ahead of it."""
        if not self.trials:
            return 0.0
        return sum((1.0 / t.rank) if t.rank else 0.0 for t in self.trials) / len(self.trials)

    def to_dict(self) -> dict:
        return {
            "top_k": self.top_k,
            "n": len(self.trials),
            "skipped": self.skipped,
            "recall_at_k": round(self.recall, 3),
            "recall_ci95": [round(self.interval[0], 3), round(self.interval[1], 3)],
            "mrr": round(self.mrr, 3),
            "trials": [
                {"chunk_id": t.chunk_id, "question": t.question, "rank": t.rank}
                for t in self.trials
            ],
        }


def sample_chunks(conn, n: int, seed: int) -> list[tuple[int, str]]:
    """Random active memory chunks, long enough to support a question.

    Random rather than newest-first: the newest records are the ones the author
    remembers, and evaluating on those is the bias this file exists to avoid.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, content FROM memory_chunks
            WHERE COALESCE(status, 'active') = 'active'
              AND length(content) >= %s
            ORDER BY md5(id::text || %s)
            LIMIT %s
            """,
            (_MIN_CHARS, str(seed), n),
        )
        return [(int(r[0]), str(r[1])) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=25, help="How many records to test.")
    ap.add_argument("--top-k", type=int, default=_ask.DEFAULT_TOP_K)
    ap.add_argument("--seed", type=int, default=7, help="Makes the sample reproducible.")
    ap.add_argument("--out", default="evals/retrieval_report.json")
    args = ap.parse_args()

    info = _llm.backend_info()
    if not info.available:
        print(f"No model available to write questions: {info.detail}", file=sys.stderr)
        return 2
    print(f"Generating questions with {info}", file=sys.stderr)

    conn = _connect()
    if conn is None:
        print("Cannot reach PostgreSQL.", file=sys.stderr)
        return 2

    random.seed(args.seed)
    report = Report(top_k=args.top_k)
    try:
        chunks = sample_chunks(conn, args.sample, args.seed)
        for i, (chunk_id, content) in enumerate(chunks, start=1):
            question, err = _llm.complete(
                _QUESTION_PROMPT.format(record=content[:1200]),
                timeout=90,
                # Run from Throughline's own directory. Without this every
                # generated question became a Claude Code session filed under
                # the user's project and ingested as their work: one run of
                # `--sample 20` added twenty two-message conversations to the
                # corpus this file exists to measure. An eval that pollutes
                # what it measures is worse than no eval.
                cwd=str(agent_call_cwd()),
            )
            if not question or err:
                report.skipped += 1
                print(f"  [{i}/{len(chunks)}] skipped ({err or 'no question'})", file=sys.stderr)
                continue
            question = question.strip().strip('"').splitlines()[0]

            sources = _ask.retrieve(conn, question, top_k=args.top_k)
            rank = next(
                (
                    s.n
                    for s in sources
                    if s.kind == "memory_chunk" and s.id == chunk_id
                ),
                None,
            )
            report.trials.append(Trial(chunk_id=chunk_id, question=question, rank=rank))
            mark = f"rank {rank}" if rank else "MISS"
            print(f"  [{i}/{len(chunks)}] {mark}  {question[:70]}", file=sys.stderr)
    finally:
        conn.close()

    Path(args.out).write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))

    print()
    lo, hi = report.interval
    print(
        f"recall@{args.top_k}: {report.recall:.1%}  (95% CI {lo:.0%}–{hi:.0%}, n={len(report.trials)})"
        f"   MRR: {report.mrr:.3f}   skipped {report.skipped}"
    )
    if len(report.trials) < 50:
        print("  ↑ the interval is wide at this sample size; --sample 100 for a usable number")
    misses = [t for t in report.trials if not t.hit]
    if misses:
        print(f"\n{len(misses)} misses — the questions retrieval could not place:")
        for t in misses[:10]:
            print(f"  chunk {t.chunk_id}: {t.question[:90]}")
    print(f"\nReport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
