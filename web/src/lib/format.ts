/** Format numbers and dates in the selected interface language. */
import { getLang } from "./language";
const uiLocale = () => getLang() === "de" ? "de-DE" : "en-US";

const numberFormat = () => new Intl.NumberFormat(uiLocale());

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return numberFormat().format(n);
}

/** Compact form for headline numbers only — tables keep full precision. */
export function formatCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (Math.abs(n) < 10_000) return numberFormat().format(n);
  return new Intl.NumberFormat(uiLocale(), {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

export function formatDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(uiLocale(), { month: "short", day: "numeric" }).format(d);
}

const dateTimeFormat = () => new Intl.DateTimeFormat(uiLocale(), {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/**
 * A full timestamp (date + time), in the selected interface locale as every
 * other formatter here. `DetailPage`'s generic field renderer used to print
 * whatever ISO-8601 string the API returned verbatim — microseconds, UTC
 * offset and all (`2026-08-25T18:42:40.747073+00:00`) — the one place in the
 * app that didn't honour this file's own stated contract. Returns the raw
 * string unchanged when it isn't a parseable date, so a non-date field never
 * silently renders as "Invalid Date".
 */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTimeFormat().format(d);
}

/** True for a string shaped like an ISO-8601 timestamp (date, optionally
 *  time) — enough to decide whether a generic API field should be run
 *  through `formatDateTime` rather than printed as-is. */
export function looksLikeIsoDate(v: string): boolean {
  return /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?/.test(v);
}

const timeFormat = () => new Intl.DateTimeFormat(uiLocale(), { hour: "2-digit", minute: "2-digit" });

/** HH:MM only, in the selected interface locale as every other formatter here —
 *  for a list of same-day rows where the date is already known from
 *  context and only the time distinguishes one row from the next
 *  (Timeline's day-detail panel). */
export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return timeFormat().format(d);
}

/**
 * `n` with a unit that agrees with it: "1 session", "2 sessions".
 *
 * Trivial, and worth having in one place: the counts on these pages are almost
 * always plural, so "1 sessions" survives review by being rare enough that
 * nobody hits it — until a screenshot of a one-session project puts it at the
 * top of the README.
 */
export function pluralise(n: number, one: string, many = `${one}s`): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}
