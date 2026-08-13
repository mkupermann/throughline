/** Formatting helpers. Numbers in tables also need the `.tabular` class so
 *  columns do not jitter as values change.
 *
 *  Numbers are formatted in `UI_LOCALE`, not the browser's. Every label in
 *  this app is written in English, and `new Intl.NumberFormat()` follows the
 *  browser instead — so on a German-configured machine the interface read
 *  "3.330 conversations" and "37.255 event(s)". To an English reader that is
 *  three-point-three-three-zero: the separator means the opposite of what it
 *  says, and there is nothing on screen to signal which convention is in play.
 *  Matching the numbers to the language of the words around them removes the
 *  ambiguity. When the interface itself becomes translatable, this constant is
 *  the single place that has to follow it.
 *
 *  Dates follow the same rule, and for a stronger reason: `formatDay` renders a
 *  month NAME, so the browser locale put "13. Juli" and "11. Aug." on the axis
 *  of a chart captioned "Conversations, last 30 days". A separator can at least
 *  be misread silently; a German month name beside English words is simply a
 *  different language on the same line. */

const UI_LOCALE = "en-US";

const nf = new Intl.NumberFormat(UI_LOCALE);

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return nf.format(n);
}

/** Compact form for headline numbers only — tables keep full precision. */
export function formatCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (Math.abs(n) < 10_000) return nf.format(n);
  return new Intl.NumberFormat(UI_LOCALE, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

export function formatDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(UI_LOCALE, { month: "short", day: "numeric" }).format(d);
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
