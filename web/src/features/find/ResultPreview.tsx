import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";

import { findApi, type FindItem } from "@/lib/api";

import { routeFor } from "./ResultList";

/**
 * The selected result, read in place.
 *
 * Every result used to be a link and nothing else, so inspecting one cost a
 * navigation away and a navigation back — and the way back lost the scroll
 * position, so a search over 83,000 messages became: scan, open, read, return,
 * find your place again, open the next. That round trip is the difference
 * between a tool someone uses and one they try once.
 *
 * Deliberately not a second implementation of DetailPage. It shares that page's
 * query — same `["detail", kind, id]` key — so previewing an item warms the
 * cache the full page reads from, and opening it afterwards renders from memory
 * instead of refetching. The preview shows the record; the page shows the
 * record plus everything related to it, which is why both still exist.
 */

//: FindItem.kind -> the API's kind segment. Only the kinds Find returns.
const API_KIND: Record<string, string> = {
  conversation: "conversation",
  message: "conversation", // a message is previewed through its conversation
  memory: "memory",
  skill: "skill",
  project: "project",
  prompt: "prompt",
};

//: Rendered as their own block rather than in the key/value list. Mirrors
//: DetailPage's LONG_FIELDS — if that list grows, this one has to follow.
const LONG_FIELDS = new Set(["content", "description", "summary", "reasoning"]);
const HIDDEN_FIELDS = new Set(["id", "embedding", "content_blocks"]);

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** The (kind, id) a result is previewed by, or null when it has no detail. */
export function previewTarget(item: FindItem): { kind: string; id: string } | null {
  const kind = API_KIND[item.kind];
  if (!kind) return null;
  if (item.kind === "message") {
    // A message's own row carries no standalone detail record; its conversation
    // does, and that is what the reader wants to see anyway.
    return item.conversation_id ? { kind: "conversation", id: String(item.conversation_id) } : null;
  }
  if (item.kind === "project") return { kind: "project", id: String(item.title ?? item.id) };
  return { kind, id: String(item.id) };
}

export function ResultPreview({ item }: { item: FindItem | null }) {
  const target = item ? previewTarget(item) : null;

  const { data, isPending } = useQuery({
    // Same key shape as DetailPage, on purpose — see the note above.
    queryKey: ["detail", target?.kind, target?.id],
    queryFn: () =>
      target!.kind === "project"
        ? findApi.projectByName(target!.id)
        : findApi.detail(target!.kind, target!.id),
    enabled: Boolean(target),
    staleTime: 60_000,
  });

  if (!item) {
    return (
      <aside className="preview preview-empty" aria-label="Preview">
        <p>Select a result to read it here.</p>
        <p className="preview-hint">
          <kbd>↓</kbd> from the search box, then <kbd>j</kbd> <kbd>k</kbd> to move ·{" "}
          <kbd>Enter</kbd> to open in full
        </p>
      </aside>
    );
  }

  if (!target) {
    return (
      <aside className="preview" aria-label="Preview">
        <p className="preview-hint">This result has no record to show.</p>
      </aside>
    );
  }

  const record = (data?.record ?? {}) as Record<string, unknown>;
  const entries = Object.entries(record).filter(([k]) => !HIDDEN_FIELDS.has(k));
  const long = entries.filter(([k]) => LONG_FIELDS.has(k));
  const short = entries.filter(([k]) => !LONG_FIELDS.has(k));

  return (
    <aside className="preview" aria-label="Preview" aria-busy={isPending}>
      <div className="preview-head">
        <span className="preview-kind">{item.kind}</span>
        <Link to={routeFor(item)} className="preview-open">
          Open in full
          <ArrowUpRight size={14} aria-hidden />
        </Link>
      </div>

      {/* The title is known from the search result itself, so it renders
          immediately — the panel never blanks while the record loads, which is
          what makes holding j down feel like scrolling rather than like a
          series of requests. */}
      <h2 className="preview-title">{item.title || `${item.kind} ${item.id}`}</h2>

      {isPending && <p className="preview-hint">Loading…</p>}

      {!isPending &&
        long.map(([k, v]) => (
          <div key={k} className="preview-block">
            <h3>{k}</h3>
            <p>{renderValue(v)}</p>
          </div>
        ))}

      {!isPending && short.length > 0 && (
        <dl className="preview-fields">
          {short.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{renderValue(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </aside>
  );
}
