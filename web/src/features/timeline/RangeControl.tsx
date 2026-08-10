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
