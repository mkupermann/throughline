import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ArrowDownWideNarrow, ArrowUpWideNarrow } from "lucide-react";

import { findApi } from "@/lib/api";
import { formatCount, formatDateTime } from "@/lib/format";
import { Transcript, type TranscriptMessage } from "./Transcript";
import {
  Crumbs,
  MetaList,
  RawData,
  RelatedSection,
  When,
  duration,
  humanize,
  num,
  obj,
  percent,
  str,
  whenEntry,
} from "./parts";

/**
 * A conversation read as a conversation: who was talking to what, over which
 * project, for how long and at what cost — then the transcript itself, which
 * is the page. The record's row-level fields (session ids, token counters,
 * the metadata blob) inform the header; none of them appear as raw columns.
 */

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

export function ConversationDetail({
  id,
  record,
  related,
}: {
  id: string;
  record: Record<string, unknown>;
  related: Record<string, unknown>;
}) {
  const [sp, setSp] = useSearchParams();
  const { hash } = useLocation();

  // In the URL like Find's and the project page's own sort, so a link to a
  // conversation read newest-first stays newest-first for whoever opens it.
  const order = (sp.get("order") === "newest" ? "newest" : "oldest") as "newest" | "oldest";

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
  }, [id]);

  const firstPage = (related.messages as TranscriptMessage[] | undefined) ?? [];
  const messageTotal = Number(related.message_total ?? firstPage.length);
  const shownCount = firstPage.length + more.length;
  const complete = shownCount >= messageTotal;

  // Fetches every remaining page in one go rather than one click per 500
  // messages. "Show more" ten times over on a 5,560-message session reads as
  // the tool rationing the transcript rather than paging through it.
  async function loadAll() {
    if (loadingAll || complete) return;
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
        const next = await findApi.detail("conversation", id, { offset: loaded, limit: 500 });
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

  // ── Message anchor: /c/{id}#m{messageId} ──────────────────────────────
  // Find's message results land here with a hash naming one message. Scroll
  // to it and move focus there once it exists; if it sits beyond the loaded
  // pages, fetch the rest first — a link that silently lands at the top of
  // the wrong 500 messages is a broken link with extra steps.
  const targetId = /^#m\d+$/.test(hash) ? hash.slice(1) : null;
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!targetId || scrolled.current === targetId) return;
    const el = document.getElementById(targetId);
    if (el) {
      scrolled.current = targetId;
      el.scrollIntoView({ block: "start" });
      (el as HTMLElement).focus({ preventScroll: true });
    } else if (!complete && !loadingAll) {
      void loadAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shownCount is the
    // "new messages arrived" signal; loadAll/complete are derived from it.
  }, [targetId, shownCount]);

  // ── Header content, from the record ───────────────────────────────────
  const meta = obj(record.metadata) ?? {};
  const stats = obj(meta.stats) ?? {};
  const title = str(meta.title) ?? str(record.summary) ?? "Untitled conversation";
  const project = str(record.project_name);
  const startedAt = str(record.started_at);
  const endedAt = str(record.ended_at);
  const ran = duration(startedAt, endedAt);
  const cost = num(record.cost_usd) ?? num(stats.session_cost);

  const chunks = Array.isArray(related.chunks)
    ? (related.chunks as Record<string, unknown>[])
    : [];

  return (
    <>
      <Crumbs kind="conversation" current={title} />
      <header className="page-header detail-head">
        <p className="detail-kicker">
          <span className="kind kind-conversation">Conversation</span>
          <span className="detail-id tabular">#{id}</span>
        </p>
        <h1 className="page-title detail-title">{title}</h1>
        {startedAt && (
          <p className="page-subtitle">
            Started {formatDateTime(startedAt)}
            {ran ? ` · ran ${ran}` : ""}
            {project ? " · " : ""}
            {project && <Link to={`/project/${encodeURIComponent(project)}`}>{project}</Link>}
          </p>
        )}
      </header>

      <MetaList
        label="Conversation details"
        items={[
          project
            ? {
                label: "Project",
                value: <Link to={`/project/${encodeURIComponent(project)}`}>{project}</Link>,
              }
            : null,
          { label: "Tool", value: str(meta.source) ?? str(record.entrypoint) },
          { label: "Model", value: str(record.model), mono: true },
          { label: "Branch", value: str(record.git_branch), mono: true },
          whenEntry("Started", startedAt),
          whenEntry("Ended", endedAt),
          num(record.message_count) !== null
            ? { label: "Messages", value: formatCount(num(record.message_count)!), num: true }
            : null,
          num(record.token_count_in) !== null
            ? { label: "Tokens in", value: formatCount(num(record.token_count_in)!), num: true }
            : null,
          num(record.token_count_out) !== null
            ? { label: "Tokens out", value: formatCount(num(record.token_count_out)!), num: true }
            : null,
          cost !== null ? { label: "Cost", value: money.format(cost), num: true } : null,
        ]}
      />

      <section className="stack-top">
        <div className="detail-tx-head">
          <h2 className="section-label">
            Transcript{" "}
            <span className="tabular">
              ({formatCount(shownCount)} of {formatCount(messageTotal)})
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
        <Transcript messages={orderedMessages} targetId={targetId} />
        {/* The endpoint returns 500 messages at a time. A 5,560-message
            session showed its first 500 and gave no way to reach the rest,
            which reads as "the history stops here" — the one impression a
            transcript must not create. Loads every remaining page in one
            go rather than one click per 500 messages. */}
        {!complete && (
          <button type="button" className="button stack-top" onClick={loadAll} disabled={loadingAll}>
            {loadingAll
              ? `Loading… (${formatCount(shownCount)} of ${formatCount(messageTotal)})`
              : `Load full transcript — ${formatCount(shownCount)} of ${formatCount(messageTotal)} shown`}
          </button>
        )}
      </section>

      {chunks.length > 0 && (
        <RelatedSection title="Memories from this conversation" count={chunks.length}>
          {chunks.map((c) => (
            <li key={String(c.id)} className="result">
              <Link to={`/m/${c.id}`} className="result-link">
                <div className="result-head">
                  {str(c.category) && (
                    <span className="kind kind-memory">{humanize(str(c.category)!)}</span>
                  )}
                  {percent(c.confidence) && (
                    <span className="detail-rel-note tabular">{percent(c.confidence)} confidence</span>
                  )}
                  {str(c.created_at) && (
                    <span className="detail-rel-note">
                      <When iso={str(c.created_at)} />
                    </span>
                  )}
                </div>
                <p className="result-snippet">{str(c.content) ?? ""}</p>
              </Link>
            </li>
          ))}
        </RelatedSection>
      )}

      <RawData record={record} />
    </>
  );
}
