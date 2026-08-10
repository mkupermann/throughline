import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { timelineApi } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { readProviders } from "@/lib/providerScope";

import { RangeControl, presetRange, type Range } from "./RangeControl";

/**
 * Activity over a date range, in per-provider lanes.
 *
 * The previous Timeline rendered the current page of search results — 30 rows
 * by default — and called it a timeline. This asks the server for an
 * aggregate over an explicit range, so what you see is the range, whole.
 *
 * Lanes use INTENSITY, not categorical hue (design spec §5.2). Six validated
 * chart hues exist against nine providers; each lane already carries its
 * provider name, so hue would be redundant, and intensity is what a cell
 * actually means. Categorical colour stays on the provider chips, where a
 * label alone is not enough.
 */
const NOT_TOOL_SPECIFIC = "not_tool_specific";

function laneLabel(provider: string): string {
  if (provider === NOT_TOOL_SPECIFIC) return "not tool-specific";
  if (provider === "unattributed") return "(unattributed)";
  return provider;
}

export function TimelinePage() {
  const [sp] = useSearchParams();
  const [range, setRange] = useState<Range>(() => presetRange(90));
  const providers = readProviders(sp);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    p.set("since", range.since);
    p.set("until", range.until);
    for (const name of providers) p.append("provider", name);
    return p;
  }, [range.since, range.until, providers.join(",")]);

  const { data, isLoading } = useQuery({
    queryKey: ["timeline", qs.toString()],
    queryFn: () => timelineApi.range(qs),
  });

  const { lanes, buckets, max, totals } = useMemo(() => {
    const cells = data?.cells ?? [];
    const laneSet = new Set<string>();
    const bucketSet = new Set<string>();
    const cellTotals = new Map<string, number>();
    for (const c of cells) {
      laneSet.add(c.provider);
      bucketSet.add(c.bucket);
      const key = `${c.provider}|${c.bucket}`;
      cellTotals.set(key, (cellTotals.get(key) ?? 0) + c.n);
    }
    const ordered = [...laneSet].sort((a, b) =>
      a === NOT_TOOL_SPECIFIC ? 1 : b === NOT_TOOL_SPECIFIC ? -1 : a.localeCompare(b),
    );
    return {
      lanes: ordered,
      buckets: [...bucketSet].sort(),
      max: Math.max(1, ...cellTotals.values()),
      totals: cellTotals,
    };
  }, [data]);

  const grandTotal = (data?.cells ?? []).reduce((s, c) => s + c.n, 0);

  return (
    <section className="timeline-page">
      <header className="page-header">
        <h1 className="page-title">Timeline</h1>
        <p className="page-hint">
          {formatCount(grandTotal)} event(s) between {range.since} and {range.until}
          {data ? `, bucketed by ${data.bucket}` : ""}
        </p>
      </header>

      <RangeControl value={range} onChange={setRange} />

      {isLoading && <p className="muted">Loading…</p>}

      {!isLoading && lanes.length === 0 && (
        <p className="empty-state">No activity in this range.</p>
      )}

      {lanes.length > 0 && (
        <div className="timeline-grid" role="table" aria-label="Activity by provider over time">
          {lanes.map((lane) => (
            <div className="timeline-lane" role="row" key={lane}>
              <span className="timeline-lane-label" role="rowheader">
                {laneLabel(lane)}
              </span>
              <div className="timeline-cells">
                {buckets.map((b) => {
                  const n = totals.get(`${lane}|${b}`) ?? 0;
                  return (
                    <span
                      key={b}
                      role="cell"
                      className="timeline-cell"
                      style={{ opacity: n === 0 ? 0.08 : 0.25 + 0.75 * (n / max) }}
                      title={`${laneLabel(lane)} · ${b} · ${n}`}
                      aria-label={`${laneLabel(lane)}, ${b}, ${n} events`}
                    />
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
