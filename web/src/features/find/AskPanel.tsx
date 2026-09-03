import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Copy, CornerDownLeft, Info } from "lucide-react";

import { askApi, type AskResponse, type AskSource } from "@/lib/api";
import { contextForAnswer } from "./copyContext";

/**
 * A question, answered from the stored history, with citations.
 *
 * Search hands back rows and leaves the reading to you — right for "find the
 * thing I half-remember", wrong for "what did we decide about X, and why",
 * whose answer is spread across several sessions in different tools. This is
 * that second question.
 *
 * The citations are the point, not a flourish. Every [n] in the answer becomes
 * a link to the record it came from, so a claim can be checked in one click.
 * An answer with no citations is labelled as unverified rather than shown as
 * if it were grounded: for a store that holds the only surviving copy of most
 * of this history, a confident invention is the one unacceptable output.
 */

/** Splits an answer on [n] markers and links each one to its record. */
function WithCitations({ text, sources }: { text: string; sources: AskSource[] }) {
  const byN = new Map(sources.map((s) => [s.n, s]));
  // Captures the number so it survives the split and can be looked up.
  const parts = text.split(/\[(\d+)\]/g);
  return (
    <>
      {parts.map((part, i) => {
        // Odd indices are the captured digits.
        if (i % 2 === 0) return <span key={i}>{part}</span>;
        const s = byN.get(Number(part));
        if (!s) {
          // A marker with no matching record. Rendered as plain text rather
          // than as a link, because a citation that goes nowhere is worse than
          // one that admits it is not a link.
          return <span key={i}>[{part}]</span>;
        }
        return (
          <Link key={i} to={routeForSource(s)} className="cite" title={s.excerpt}>
            [{part}]
          </Link>
        );
      })}
    </>
  );
}

/** Same routes the result list uses, derived from the API's own kinds. */
export function routeForSource(s: AskSource): string {
  if (s.kind === "message") {
    return s.conversation_id ? `/c/${s.conversation_id}#m${s.id}` : `/m/${s.id}`;
  }
  if (s.kind === "memory_chunk") return `/m/${s.id}`;
  if (s.kind === "conversation") return `/c/${s.id}`;
  return `/m/${s.id}`;
}

export function AskPanel({
  question,
  onAsked,
}: {
  question: string;
  onAsked?: (question: string) => void;
}) {
  const [copyStatus, setCopyStatus] = useState<{ id: number; message: string } | null>(null);
  const ask = useMutation({
    mutationFn: (q: string) => askApi.ask({ question: q }),
  });

  // Asked once per question, not once per pause in typing.
  //
  // The query is debounced into the URL, so reacting to every change meant a
  // model call every 220ms of hesitation: typing "why did we move off 8787"
  // spent several calls and several seconds each to answer fragments nobody
  // asked. Searching can afford that because it is cheap and the partial
  // results are useful; an answer is neither.
  //
  // So: fire when the question stops changing for long enough to be finished,
  // and never twice for the same text.
  const { mutate } = ask;
  const asked = useRef<string | null>(null);
  useEffect(() => {
    const q = question.trim();
    if (q.length < 3 || q === asked.current) return;
    const t = window.setTimeout(() => {
      asked.current = q;
      onAsked?.(q);
      mutate(q);
    }, 900);
    return () => window.clearTimeout(t);
  }, [question, mutate, onAsked]);

  useEffect(() => {
    setCopyStatus(null);
  }, [question]);

  if (!question.trim()) {
    return (
      <div className="ask-empty">
        <h2>Ask your history a question</h2>
        <p>
          Not keywords — a question. “Why did we move the web UI off 8787?”,
          “What did I decide about embeddings?”. The answer is assembled from
          your own records, and every claim links back to the one it came from.
        </p>
      </div>
    );
  }

  const currentQuestion = question.trim();
  const currentRequest = ask.variables?.trim() === currentQuestion;

  if (ask.isPending && currentRequest) {
    return <p className="ask-status" role="status">Reading your history…</p>;
  }

  if (ask.isError && currentRequest) {
    return <p className="ask-status" role="alert">{(ask.error as Error).message}</p>;
  }

  const response = ask.data as AskResponse | undefined;
  const data = response?.question.trim() === currentQuestion ? response : undefined;
  if (!data) return null;
  const answer = data;

  const cited = data.sources.filter((s) => data.cited.includes(s.n));
  // With citations, the cited records are the evidence. Without them, the
  // retrieved records ARE the answer as far as the reader is concerned, so
  // show more of them rather than a token three.
  const shown = cited.length > 0 ? cited : data.sources.slice(0, 8);

  async function copyAnswer() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(contextForAnswer(answer));
      setCopyStatus((previous) => ({
        id: (previous?.id ?? 0) + 1,
        message: "Answer and sources copied.",
      }));
    } catch {
      setCopyStatus((previous) => ({
        id: (previous?.id ?? 0) + 1,
        message: "Could not copy the answer. Check browser permissions and try again.",
      }));
    }
  }

  return (
    <div className="ask">
      {data.degraded && (
        <div className="disclosure">
          <Info size={15} aria-hidden />
          <div>{data.degraded}</div>
        </div>
      )}

      {data.answer && (
        <>
          <p className="ask-answer">
            <WithCitations text={data.answer} sources={data.sources} />
          </p>
          <div className="ask-actions">
            <button
              type="button"
              className="button ask-copy"
              aria-label="Copy answer with sources"
              onClick={() => void copyAnswer()}
            >
              <Copy size={14} aria-hidden />
              Copy answer with sources
            </button>
          </div>
        </>
      )}
      <p className="sr-only" role="status" aria-live="polite">
        {copyStatus && <span key={copyStatus.id}>{copyStatus.message}</span>}
      </p>

      {/* An uncited answer must say so — but saying so is not the useful part.
        * The first version put a warning box between the reader and the
        * records, which is the wrong way round: what they want when an answer
        * cannot be trusted is the evidence, not a lecture about it. One quiet
        * line here; the records get the space. */}
      {data.answer && cited.length === 0 && (
        <p className="ask-caveat">
          Nothing in this answer is cited, so it cannot be checked against your
          history — the records it was built from are below.
        </p>
      )}

      {/* Where the answer came from, and whether the excerpts left this
          machine. Stated rather than left to be inferred: it is the one fact
          about this feature a privacy-minded reader actually wants, and
          burying it in a config file would be a way of not saying it. */}
      {data.model && (
        <p className="ask-provenance">
          Answered by <strong>{data.model}</strong>{" "}
          {data.local ? "on this machine" : `via ${data.backend} — excerpts left this machine`}
        </p>
      )}

      {shown.length > 0 && (
        <>
          <h2 className="section-label ask-sources-title">
            {cited.length > 0 ? "Cited records" : "What the answer was built from"}
          </h2>
          <ul className="ask-sources">
            {shown.map((s) => (
              <li key={`${s.kind}-${s.id}`}>
                <Link to={routeForSource(s)} className="ask-source">
                  <span className="ask-source-n">[{s.n}]</span>
                  <span className="ask-source-body">
                    <span className="ask-source-ref">{s.ref}</span>
                    <span className="ask-source-excerpt">{s.excerpt}</span>
                  </span>
                  <CornerDownLeft size={13} aria-hidden className="ask-source-go" />
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
