/** Preset ranges plus explicit dates. The Timeline's own control — it no
 * longer borrows the search page's pagination, which was the actual defect. */
export interface Range {
  since: string;
  until: string;
}

const DAY = 86_400_000;

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function presetRange(days: number): Range {
  const until = new Date();
  return { since: iso(new Date(until.getTime() - days * DAY)), until: iso(until) };
}

function addDays(dateStr: string, days: number): Date {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d;
}

/**
 * The calendar span one aggregate bucket covers, so a week/month cell can be
 * "zoomed into" as a narrower range instead of being misrepresented as a
 * single day — a single date cannot stand for a week or a month without
 * showing a fraction of what the cell actually counted, the same
 * "the number and the list disagree" failure `day_detail`'s default already
 * had to be fixed for. Dates are UTC-normalised throughout: they come from
 * the server's `date_trunc`, and this is pure calendar math, not "now".
 */
export function bucketSpan(bucket: "day" | "week" | "month", bucketDate: string): Range {
  if (bucket === "day") return { since: bucketDate, until: bucketDate };
  if (bucket === "week") {
    // Postgres date_trunc('week', ts) is Monday-based (ISO 8601).
    return { since: bucketDate, until: iso(addDays(bucketDate, 6)) };
  }
  // `bucketDate` is already the first of its month (date_trunc('month', ts)).
  // Date.UTC(y, m, 0) — `m` here is the 1-indexed current month, which as a
  // 0-indexed month argument names the *next* month; day 0 of that month is
  // the last day of the current one.
  const [y, m] = bucketDate.split("-").map(Number);
  return { since: bucketDate, until: iso(new Date(Date.UTC(y, m, 0))) };
}

export function RangeControl({
  value,
  onChange,
}: {
  value: Range;
  onChange: (r: Range) => void;
}) {
  const presets: Array<[string, number]> = [
    ["30d", 30],
    ["90d", 90],
    ["1y", 365],
    ["All", 3650],
  ];
  return (
    <div className="range-control" role="group" aria-label="Date range">
      {presets.map(([label, days]) => (
        <button
          key={label}
          type="button"
          className="range-preset"
          onClick={() => onChange(presetRange(days))}
        >
          {label}
        </button>
      ))}
      <label className="range-date">
        From
        <input
          type="date"
          value={value.since}
          onChange={(e) => onChange({ ...value, since: e.target.value })}
        />
      </label>
      <label className="range-date">
        To
        <input
          type="date"
          value={value.until}
          onChange={(e) => onChange({ ...value, until: e.target.value })}
        />
      </label>
    </div>
  );
}
