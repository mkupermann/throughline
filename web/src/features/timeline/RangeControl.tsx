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

/**
 * Every bucket start between `since` and `until`, including ones the server
 * returned no rows for.
 *
 * The grid used to take its columns from the buckets present in the response,
 * so a day nothing happened on simply did not exist and its neighbours became
 * adjacent. The axis then read 05-25, 06-06, 06-13 at even spacing: distance
 * along it meant nothing, while looking exactly like it meant time. A quiet
 * fortnight and a busy one were indistinguishable — which is the single thing
 * a timeline is for.
 *
 * Bounded by the server's own bucket choice (queries/timeline.py pick_bucket):
 * at most ~91 day columns, ~105 week columns, and months beyond that.
 *
 * All arithmetic is UTC. Local-time date construction would shift a bucket
 * across midnight for anyone east or west of UTC, silently misaligning every
 * column against the server's `date_trunc`.
 */
export function enumerateBuckets(
  since: string,
  until: string,
  bucket: "day" | "week" | "month",
): string[] {
  const end = new Date(`${until}T00:00:00Z`);
  if (Number.isNaN(end.getTime())) return [];
  const out: string[] = [];

  if (bucket === "month") {
    const [ys, ms] = since.split("-").map(Number);
    let cursor = new Date(Date.UTC(ys, ms - 1, 1));
    while (cursor <= end) {
      out.push(iso(cursor));
      cursor = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1));
    }
    return out;
  }

  let cursor = new Date(`${since}T00:00:00Z`);
  if (Number.isNaN(cursor.getTime())) return [];
  if (bucket === "week") {
    // Align to the Monday at or before `since`, matching Postgres
    // date_trunc('week', …). getUTCDay() is 0 for Sunday, so Sunday steps back
    // six days rather than forward one.
    const dow = cursor.getUTCDay();
    cursor = addDays(iso(cursor), dow === 0 ? -6 : 1 - dow);
  }
  const step = bucket === "week" ? 7 : 1;
  while (cursor <= end) {
    out.push(iso(cursor));
    cursor = addDays(iso(cursor), step);
  }
  return out;
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
