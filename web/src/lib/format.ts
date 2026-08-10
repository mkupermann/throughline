/** Locale-aware formatting helpers. Numbers in tables also need the
 *  `.tabular` class so columns do not jitter as values change. */

const nf = new Intl.NumberFormat();

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return nf.format(n);
}

/** Compact form for headline numbers only — tables keep full precision. */
export function formatCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (Math.abs(n) < 10_000) return nf.format(n);
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

export function formatDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(d);
}
