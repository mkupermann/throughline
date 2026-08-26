import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { formatCount, formatDateTime } from "@/lib/format";

/**
 * Shared chrome for every detail page. One layout system across kinds —
 * breadcrumb, title block, metadata list, content, related records, raw
 * JSON — so a conversation, a memory, and a skill read as rooms in the same
 * building even though each presents its own furniture.
 */

export const KIND_LABEL: Record<string, string> = {
  conversation: "Conversation",
  memory: "Memory",
  entity: "Entity",
  skill: "Skill",
  prompt: "Prompt",
};

/** "error_solution" -> "Error solution". Sentence case, matching the app's
 *  copy convention — a raw identifier never reaches the screen. */
export function humanize(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
}

// ── Safe readers over the untyped API record ─────────────────────────────

export function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

export function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  // The API serialises some numerics (confidence, decimals) as strings.
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) return Number(v);
  return null;
}

export function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : [];
}

export function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

/** "0.80" -> "80%". Confidence is stored 0..1. */
export function percent(v: unknown): string | null {
  const n = num(v);
  if (n === null) return null;
  return `${Math.round(n * 100)}%`;
}

/** How long something ran. Sub-minute sessions are real (a one-question
 *  session runs 49s) so seconds are kept below the minute mark. */
export function duration(startIso: string | null, endIso: string | null): string | null {
  if (!startIso || !endIso) return null;
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!Number.isFinite(ms) || ms < 1000) return null;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const mins = Math.round(s / 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  return `${h}h ${mins % 60}m`;
}

/** A timestamp as a real <time>, formatted through the app's Intl helper. */
export function When({ iso }: { iso: string | null }) {
  if (!iso) return null;
  return <time dateTime={iso}>{formatDateTime(iso)}</time>;
}

/** A metadata entry for a timestamp — or nothing, when there is none.
 *  (A `<When iso={null}>` element is truthy even though it renders nothing,
 *  so passing it straight to MetaList left labels standing over empty
 *  values.) */
export function whenEntry(label: string, iso: unknown): MetaEntry | null {
  const s = str(iso);
  return s ? { label, value: <When iso={s} /> } : null;
}

// ── Breadcrumb ───────────────────────────────────────────────────────────

/** Find › Kind › This record. The kind crumb is a live filter link, not a
 *  label: it lands on Find already narrowed to that kind. */
export function Crumbs({ kind, current }: { kind: string; current: string }) {
  return (
    <nav aria-label="Breadcrumb" className="detail-crumbs">
      <ol>
        <li>
          <Link to="/find">Find</Link>
        </li>
        <li>
          <Link to={`/find?kinds=${kind}`}>{KIND_LABEL[kind] ?? humanize(kind)}</Link>
        </li>
        <li aria-current="page">{current}</li>
      </ol>
    </nav>
  );
}

// ── Metadata list ────────────────────────────────────────────────────────

export interface MetaEntry {
  label: string;
  value: ReactNode;
  /** Monospace value — paths, ids, branches. */
  mono?: boolean;
  /** Tabular numerals — counts, tokens, money. */
  num?: boolean;
}

/** Definition list of what's known about the record. Entries with nothing to
 *  say are dropped rather than rendered as "—" walls — absence of a value is
 *  not information the reader needs eleven times over. */
export function MetaList({ items, label }: { items: (MetaEntry | null)[]; label?: string }) {
  const shown = items.filter(
    (i): i is MetaEntry => i !== null && i.value !== null && i.value !== undefined && i.value !== "",
  );
  if (shown.length === 0) return null;
  return (
    <dl className="detail-meta" aria-label={label ?? "Details"}>
      {shown.map((i) => (
        <div key={i.label} className="detail-meta-item">
          <dt>{i.label}</dt>
          <dd className={[i.mono ? "is-mono" : "", i.num ? "tabular" : ""].join(" ").trim() || undefined}>
            {i.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ── Chip list (tags, triggers, variables) ────────────────────────────────

export function ChipList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className="detail-chipsection">
      <h2 className="section-label">
        {label} <span className="tabular">({formatCount(items.length)})</span>
      </h2>
      <ul className="detail-chips">
        {items.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </section>
  );
}

// ── Related records ──────────────────────────────────────────────────────

/** One group of related records: a heading with a count, then navigable
 *  rows. Rows reuse Find's result-link treatment so "click this to go
 *  there" looks the same everywhere in the app. */
export function RelatedSection({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: ReactNode;
}) {
  return (
    <section className="stack-top detail-related">
      <h2 className="section-label">
        {title} <span className="tabular">({formatCount(count)})</span>
      </h2>
      <ul className="results">{children}</ul>
    </section>
  );
}

// ── Raw JSON escape hatch ────────────────────────────────────────────────

/** The full record, pretty-printed, collapsed at the bottom of every detail
 *  page. Power users keep complete access to what the API returned; the page
 *  above stops depending on it. */
export function RawData({ record }: { record: Record<string, unknown> }) {
  return (
    <details className="detail-raw stack-top">
      <summary>Raw data</summary>
      <pre className="detail-raw-json">{JSON.stringify(record, null, 2)}</pre>
    </details>
  );
}
