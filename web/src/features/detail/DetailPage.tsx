import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowDownWideNarrow, ArrowLeft, ArrowUpWideNarrow, OctagonAlert } from "lucide-react";

import { findApi, type ApiError } from "@/lib/api";
import { formatDateTime, looksLikeIsoDate } from "@/lib/format";
import { Transcript, type TranscriptMessage } from "./Transcript";

/** URL prefix -> API kind. Short prefixes keep deep links pasteable.
 *
 * "project" is deliberately absent: it used to be reachable at `/p/:id`
 * through this generic renderer -- a bare field grid with raw snake_case
 * labels and unformatted ISO timestamps -- while `/project/:name` already
 * routed to the purpose-built ProjectPage for the same entity. Every caller
 * (Find's routeFor, the command palette, Ask citations) now points at
 * ProjectPage directly; see ResultList.tsx's routeFor (UI audit
 * full-app H1). */
export const DETAIL_KINDS = {
  c: "conversation",
  m: "memory",
  e: "entity",
  s: "skill",
  pr: "prompt",
} as const;

const TITLE: Record<string, string> = {
  conversation: "Conversation",
  memory: "Memory chunk",
  entity: "Entity",
  skill: "Skill",
  prompt: "Prompt",
};

/** Fields rendered as their own block rather than in the key/value grid. */
const LONG_FIELDS = new Set(["content", "description", "summary", "reasoning"]);
const HIDDEN_FIELDS = new Set(["id"]);

/** Any field name ending `_at` is a timestamp by this codebase's own
 *  convention (created_at, occurred_at, last_activity...); `looksLikeIsoDate`
 *  catches the rest by shape, so a field this generic renderer has never
 *  seen still gets formatted correctly. */
function renderValue(key: string, v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  if (typeof v === "string" && (key.endsWith("_at") || looksLikeIsoDate(v))) {
    return formatDateTime(v);
  }
  return String(v);
}

/** "created_at" -> "Created at" — matches the app's sentence-case
 *  convention everywhere else instead of the raw snake_case field name
 *  (UI audit full-app H1). `.detail-field dt` still renders it in small
 *  caps via CSS, but the underlying text (and anything reading it, like a
 *  screen reader) sees words, not an identifier. */
function humanizeLabel(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function DetailPage({ kind }: { kind: (typeof DETAIL_KINDS)[keyof typeof DETAIL_KINDS] }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();

  // In the URL like Find's and the project page's own sort, so a link to a
  // conversation read newest-first stays newest-first for whoever opens it.
  const order = (sp.get("order") === "newest" ? "newest" : "oldest") as "newest" | "oldest";

  const { data, isPending, error } = useQuery({
    queryKey: ["detail", kind, id],
    queryFn: () => findApi.detail(kind, id!),
    enabled: Boolean(id),
  });

  // Messages beyond the first page, appended as they arrive.
  //
  // Held here rather than refetched with a bigger limit: re-requesting 500
  // messages to add 500 more doubles the transfer on every fetch, and by the
  // tenth page of a 5,560-message session that is 5MB re-sent to show the
  // last 500.
  const [more, setMore] = useState<TranscriptMessage[]>([]);
  const [loadingAll, setLoadingAll] = useState(false);

  // A new record means the accumulated tail belongs to the previous one.
  useEffect(() => {
    setMore([]);
  }, [kind, id]);

  const related = (data?.related ?? {}) as Record<string, unknown>;
  const firstPage = (related.messages as TranscriptMessage[] | undefined) ?? [];
  const messageTotal = Number(related.message_total ?? firstPage.length);
  const shownCount = firstPage.length + more.length;
  const complete = shownCount >= messageTotal;

  // Fetches every remaining page in one go rather than one click per 500
  // messages. "Show more" ten times over on a 5,560-message session reads as
  // the tool rationing the transcript rather than paging through it.
  async function loadAll() {
    if (loadingAll || !id || complete) return;
    setLoadingAll(true);
    try {
      // A local running count, not `more.length`: state set inside this loop
      // is not visible to this closure until the next render, so re-reading
      // `more` here would re-fetch (and duplicate) the page just added.
      let loaded = firstPage.length + more.length;
      // Updates `more` after every page, not once at the end, so the
      // "Loading… (X of Y)" count on the button actually moves during a long
      // fetch instead of jumping straight from start to finished.
      while (loaded < messageTotal) {
        const next = await findApi.detail(kind, id, { offset: loaded, limit: 500 });
        const page = ((next.related ?? {}) as Record<string, unknown>).messages;
        if (!Array.isArray(page) || page.length === 0) break;
        const chunk = page as TranscriptMessage[];
        setMore((prev) => prev.concat(chunk));
        loaded += chunk.length;
      }
    } finally {
      setLoadingAll(false);
    }
  }

  // Newest-first only means something over the complete transcript — reversing
  // just the first loaded page would show message 500 above message 1 while
  // silently hiding the 5,060 messages actually newer than either of them.
  function setOrder(next: "oldest" | "newest") {
    const p = new URLSearchParams(sp);
    if (next === "oldest") p.delete("order");
    else p.set("order", "newest");
    setSp(p, { replace: true });
    if (next === "newest" && !complete) void loadAll();
  }

  const allMessages = firstPage.concat(more);
  const orderedMessages = order === "newest" ? [...allMessages].reverse() : allMessages;

  // Escape goes back — a detail view is a modal in spirit and must always
  // have a keyboard exit.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === "Escape") navigate(-1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigate]);

  if (isPending) {
    return (
      <>
        <div className="skeleton skeleton-headline" />
        <div className="skeleton skeleton-row" />
      </>
    );
  }

  if (error) {
    const e = error as ApiError;
    return (
      <div className="empty-state">
        <OctagonAlert size={22} aria-hidden />
        <h2>{e.status === 404 ? "Not found" : "Could not load"}</h2>
        <p>{e.message}</p>
        <Link to="/find" className="button">
          Back to Find
        </Link>
      </div>
    );
  }

  const record = data.record;
  const longs = Object.entries(record).filter(([k]) => LONG_FIELDS.has(k));
  const shorts = Object.entries(record).filter(
    ([k]) => !LONG_FIELDS.has(k) && !HIDDEN_FIELDS.has(k),
  );

  return (
    <>
      <header className="page-header">
        <button type="button" className="backlink" onClick={() => navigate(-1)}>
          <ArrowLeft size={14} aria-hidden />
          Back
        </button>
        <h1 className="page-title">
          {TITLE[kind]} <span className="detail-id tabular">#{id}</span>
        </h1>
      </header>

      {longs.map(([k, v]) =>
        v ? (
          <section key={k} className="detail-long">
            <h2 className="section-label">{humanizeLabel(k)}</h2>
            <p>{renderValue(k, v)}</p>
          </section>
        ) : null,
      )}

      <section>
        <h2 className="section-label">Fields</h2>
        <dl className="detail-grid">
          {shorts.map(([k, v]) => (
            <div key={k} className="detail-field">
              <dt>{humanizeLabel(k)}</dt>
              <dd className={typeof v === "number" ? "tabular" : undefined}>{renderValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      </section>

      {Object.entries(data.related ?? {}).map(([name, rowsList]) =>
        rowsList.length && name === "messages" ? (
          <section key={name} className="stack-top">
            <div className="detail-tx-head">
              <h2 className="section-label">
                Transcript{" "}
                <span className="tabular">
                  ({shownCount.toLocaleString("en-US")} of {messageTotal.toLocaleString("en-US")})
                </span>
              </h2>
              {/* Both directions, because both questions are asked: "how did
                  this start" and "what happened most recently". */}
              <div className="mode-switch" role="group" aria-label="Transcript order">
                <button
                  type="button"
                  className={order === "oldest" ? "is-on" : ""}
                  aria-pressed={order === "oldest"}
                  onClick={() => setOrder("oldest")}
                >
                  <ArrowUpWideNarrow size={14} aria-hidden /> Oldest first
                </button>
                <button
                  type="button"
                  className={order === "newest" ? "is-on" : ""}
                  aria-pressed={order === "newest"}
                  onClick={() => setOrder("newest")}
                >
                  <ArrowDownWideNarrow size={14} aria-hidden /> Newest first
                </button>
              </div>
            </div>
            {/* Rendered as a transcript, not as a list of content strings. The
                generic renderer below shows `content` truncated at 400
                characters, which on a real session hides every command the
                model ran and every result it read back — those live in
                `content_blocks`, and 772 of one 5,560-message session's
                assistant messages have no `content` at all. */}
            <Transcript messages={orderedMessages} />
            {/* The endpoint returns 500 messages at a time. A 5,560-message
                session showed its first 500 and gave no way to reach the rest,
                which reads as "the history stops here" — the one impression a
                transcript must not create. Loads every remaining page in one
                go rather than one click per 500 messages. */}
            {!complete && (
              <button
                type="button"
                className="button stack-top"
                onClick={loadAll}
                disabled={loadingAll}
              >
                {loadingAll
                  ? `Loading… (${shownCount.toLocaleString("en-US")} of ${messageTotal.toLocaleString("en-US")})`
                  : `Load full transcript — ${shownCount.toLocaleString("en-US")} of ${messageTotal.toLocaleString("en-US")} shown`}
              </button>
            )}
          </section>
        ) : rowsList.length ? (
          <section key={name} className="stack-top">
            <h2 className="section-label">
              {name} <span className="tabular">({rowsList.length})</span>
            </h2>
            <ul className="results">
              {rowsList.slice(0, 200).map((row, i) => (
                <li key={i} className="result">
                  <div className="result-link">
                    <div className="result-head">
                      {"role" in row && <span className="kind kind-message">{String(row.role)}</span>}
                      {Boolean(row.category) && (
                        <span className="kind kind-memory">{String(row.category)}</span>
                      )}
                      {"other_name" in row && (
                        <span className="result-title">{String(row.other_name)}</span>
                      )}
                    </div>
                    <p className="result-snippet">
                      {String(row.content ?? row.relation_type ?? "").slice(0, 400)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
            {rowsList.length > 200 && (
              <p className="empty-hint">Showing the first 200 of {rowsList.length}.</p>
            )}
          </section>
        ) : null,
      )}
    </>
  );
}
