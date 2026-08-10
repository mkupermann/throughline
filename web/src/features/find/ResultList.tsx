import { Link } from "react-router-dom";
import type { FindItem, Kind } from "@/lib/api";

/** Route for a result, so the list and the table cannot disagree. */
export function routeFor(item: FindItem): string {
  switch (item.kind) {
    case "message":
      return item.conversation_id ? `/c/${item.conversation_id}#m${item.id}` : `/m/${item.id}`;
    case "conversation":
      return `/c/${item.id}`;
    case "memory":
      return `/m/${item.id}`;
    case "skill":
      return `/s/${item.id}`;
    case "project":
      // Projects route by name: the registry id is 0 for the many projects
      // that have memory but no `projects` row.
      return `/p/${encodeURIComponent(item.title ?? item.project ?? String(item.id))}`;
    case "prompt":
      return `/pr/${item.id}`;
  }
}

const KIND_LABEL: Record<Kind, string> = {
  memory: "memory",
  message: "message",
  conversation: "conversation",
  skill: "skill",
  project: "project",
  prompt: "prompt",
};

/**
 * Highlight query terms in a snippet.
 *
 * Builds React elements rather than an HTML string — there is no
 * `dangerouslySetInnerHTML` anywhere in this codebase, and search results are
 * exactly the place where user-controlled database content would otherwise be
 * interpolated into markup.
 */
function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length || !text) return <>{text}</>;
  const escaped = terms
    .filter(Boolean)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .sort((a, b) => b.length - a.length);
  if (!escaped.length) return <>{text}</>;

  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(re);
  return (
    <>
      {parts.map((part, i) =>
        re.test(part) && i % 2 === 1 ? (
          <mark key={i} className="hl">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(d);
}

export function ResultRow({ item, terms }: { item: FindItem; terms: string[] }) {
  // A message's own text is the snippet — its `title` is the *conversation*
  // summary, which is identical for every message in that conversation. Using
  // it as the heading made five distinct results look like the same row, so
  // for messages the conversation is context in the meta line instead.
  const isMessage = item.kind === "message";
  const heading = isMessage
    ? null
    : item.title || (item.kind === "memory" ? item.category : null) || KIND_LABEL[item.kind];

  return (
    <li className="result">
      <Link to={routeFor(item)} className="result-link">
        <div className="result-head">
          <span className={`kind kind-${item.kind}`}>
            {isMessage && item.category ? item.category : KIND_LABEL[item.kind]}
          </span>
          {heading && (
            <span className="result-title">
              <Highlight text={String(heading)} terms={terms} />
            </span>
          )}
          {item.retrievers > 1 && (
            <span className="result-both" title="Matched by both text and meaning">
              both
            </span>
          )}
        </div>
        {item.snippet && (
          <p className={`result-snippet${isMessage ? " is-primary" : ""}`}>
            <Highlight text={item.snippet} terms={terms} />
          </p>
        )}
        <div className="result-meta">
          {isMessage && item.title && (
            <span className="result-in">in {item.title}</span>
          )}
          {item.project && <span>{item.project}</span>}
          {item.category && !isMessage && item.kind !== "memory" && <span>{item.category}</span>}
          {item.status && item.status !== "active" && (
            <span className="result-status">{item.status}</span>
          )}
          {item.confidence !== null && (
            <span className="tabular">conf {item.confidence.toFixed(2)}</span>
          )}
          {item.occurred_at && <span>{when(item.occurred_at)}</span>}
        </div>
      </Link>
    </li>
  );
}

export function ResultList({ items, terms }: { items: FindItem[]; terms: string[] }) {
  return (
    <ul className="results">
      {items.map((item) => (
        <ResultRow key={`${item.kind}-${item.id}`} item={item} terms={terms} />
      ))}
    </ul>
  );
}
