import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { providersApi, timelineApi } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { readProviders } from "@/lib/providerScope";

import { RangeControl, bucketSpan, presetRange, type Range } from "./RangeControl";
import { TimelineDetail } from "./TimelineDetail";

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

//: `COALESCE(source_tool, 'unattributed')` in queries/timeline.py's SELECT —
//: a real lane that CAN be filtered on: queries/timeline.py's
//: `_split_providers`/`_provider_filter` translate this sentinel into
//: `source_tool IS NULL` server-side (real rows never hold the literal
//: string), OR'd with any named providers in the same request. Unlike
//: NOT_TOOL_SPECIFIC, this lane's rows DO have a provider column — they're
//: conversations/messages/memory with no recorded tool, not a different
//: kind of event — so it is sent through like any other lane.
const UNATTRIBUTED = "unattributed";

function laneLabel(provider: string, labels: Map<string, string>): string {
  if (provider === NOT_TOOL_SPECIFIC) return "not tool-specific";
  if (provider === UNATTRIBUTED) return "(unattributed)";
  return labels.get(provider) ?? provider;
}

//: Column-header thinning for the date axis (Fix: the grid had no date axis
//: at all — dates lived only in tooltips/aria-labels). A day-bucket range can
//: have up to ~90 columns; a week bucket up to ~104; labelling every one
//: overlaps illegibly, so only every Nth gets text. The last bucket always
//: gets one too, so the range's end date is never the one label thinning drops.
const MAX_AXIS_LABELS = 12;

function axisStride(bucketCount: number): number {
  return Math.max(1, Math.ceil(bucketCount / MAX_AXIS_LABELS));
}

function axisLabel(bucketDate: string, bucket: string): string {
  // Full ISO date is wasted width on a 4px-wide column; a bucket already
  // implies its own year in the common case (a 90-day day-bucket view, a
  // multi-year week/month view still gets the year so ticks stay unambiguous).
  return bucket === "month" ? bucketDate.slice(0, 7) : bucketDate.slice(5);
}

export function TimelinePage() {
  const [sp] = useSearchParams();
  const [range, setRange] = useState<Range>(() => presetRange(90));
  // The day whose detail is open, or null when no cell is selected. Only a
  // day-bucket cell ever sets this directly — see handleCellClick below.
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  // The lane of the cell that opened `selectedDay` — the detail request must
  // scope to exactly this lane, not the app-wide provider scope, or clicking
  // one provider's row opens every provider's events for that day.
  const [selectedLane, setSelectedLane] = useState<string | null>(null);
  const providers = readProviders(sp);

  // Same queryKey as ProviderBar — one shared cache entry, not a second
  // request for the same data (see OperatePage.tsx for the same pattern).
  const { data: providersData } = useQuery({
    queryKey: ["providers"],
    queryFn: () => providersApi.list(),
    staleTime: 60_000,
  });
  const providerLabels = useMemo(
    () => new Map((providersData?.providers ?? []).map((p) => [p.name, p.label])),
    [providersData],
  );

  // Any range change invalidates whatever day was open — it may no longer
  // even be in range, and showing stale detail for the old range is worse
  // than closing it.
  const handleRangeChange = (r: Range) => {
    setRange(r);
    setSelectedDay(null);
    setSelectedLane(null);
  };

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

  const dayQs = useMemo(() => {
    const p = new URLSearchParams();
    // The clicked lane IS the scope for its own detail request — see the
    // state comment on selectedLane. NOT_TOOL_SPECIFIC is the one exception:
    // its kinds (skills, projects, ...) have no provider column at all, and
    // day_detail drops them entirely the moment ANY provider filter is
    // present — so that lane's click must carry none. "unattributed" DOES
    // have a provider column (it means source_tool IS NULL), and
    // queries/timeline.py's provider filter now understands it as a real
    // filter value, so it's sent through like any other lane.
    if (selectedLane && selectedLane !== NOT_TOOL_SPECIFIC) {
      p.append("provider", selectedLane);
    }
    return p;
  }, [selectedLane]);

  const { data: dayData, isLoading: dayLoading } = useQuery({
    queryKey: ["timeline-day", selectedDay, dayQs.toString()],
    queryFn: () => timelineApi.day(selectedDay as string, dayQs),
    enabled: selectedDay !== null,
  });

  /**
   * Clicking a cell loads its rows (spec §5.1) — but only a day-bucket cell
   * has a single date to ask `/timeline/day/{date}` for. A week or month
   * cell would have to pick one day out of several and show a fraction of
   * what it counted — the exact "the number and the list disagree" failure
   * `day_detail`'s default was fixed for. So a week/month click zooms the
   * range into that bucket's span instead, and `pick_bucket` re-buckets it
   * finer on the next fetch — clicking a month shows that month by day.
   */
  const handleCellClick = (lane: string, bucketDate: string) => {
    if (!data) return;
    if (data.bucket === "day") {
      setSelectedLane(lane);
      setSelectedDay(bucketDate);
    } else {
      handleRangeChange(bucketSpan(data.bucket, bucketDate));
    }
  };

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
  const stride = axisStride(buckets.length);
  // The cell's own aggregate count — the authority for "showing X of N" in
  // the detail panel below, instead of trusting day_detail's (capped) row
  // count. Looked up by the exact (lane, bucket) pair the open panel is for.
  const selectedTotal =
    selectedLane && selectedDay ? totals.get(`${selectedLane}|${selectedDay}`) : undefined;

  return (
    <section className="timeline-page">
      <header className="page-header">
        <h1 className="page-title">Timeline</h1>
        <p className="page-hint">
          {formatCount(grandTotal)} event(s) between {range.since} and {range.until}
          {data ? `, bucketed by ${data.bucket}` : ""}
        </p>
      </header>

      <RangeControl value={range} onChange={handleRangeChange} />

      {isLoading && <p className="muted">Loading…</p>}

      {!isLoading && lanes.length === 0 && (
        <p className="empty-state">No activity in this range.</p>
      )}

      {lanes.length > 0 && (
        <div className="timeline-grid" role="table" aria-label="Activity by provider over time">
          <div className="timeline-lane timeline-axis" role="row" aria-hidden="true">
            <span className="timeline-lane-label" />
            <div className="timeline-cells">
              {buckets.map((b, i) => {
                const show = i % stride === 0 || i === buckets.length - 1;
                return (
                  <span key={b} className="timeline-axis-label">
                    {show ? axisLabel(b, data?.bucket ?? "day") : ""}
                  </span>
                );
              })}
            </div>
          </div>
          {lanes.map((lane) => (
            <div className="timeline-lane" role="row" key={lane}>
              <span className="timeline-lane-label" role="rowheader">
                {laneLabel(lane, providerLabels)}
              </span>
              <div className="timeline-cells">
                {buckets.map((b) => {
                  const n = totals.get(`${lane}|${b}`) ?? 0;
                  return (
                    // The wrapper carries the grid's `cell` role; the button
                    // inside stays an unmodified, un-role-overridden button
                    // (role="button" would be dropped if `role="cell"` sat
                    // on the button itself), so it is findable and operable
                    // as a button by both assistive tech and tests.
                    <span key={b} role="cell" className="timeline-cell-wrap">
                      <button
                        type="button"
                        className="timeline-cell"
                        style={{ opacity: n === 0 ? 0.08 : 0.25 + 0.75 * (n / max) }}
                        title={`${laneLabel(lane, providerLabels)} · ${b} · ${n}`}
                        aria-label={`${laneLabel(lane, providerLabels)}, ${b}, ${n} events`}
                        onClick={() => handleCellClick(lane, b)}
                      />
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedDay && (
        <TimelineDetail
          day={selectedDay}
          providers={selectedLane && selectedLane !== NOT_TOOL_SPECIFIC ? [selectedLane] : []}
          total={selectedTotal}
          data={dayData}
          isLoading={dayLoading}
          onClose={() => {
            setSelectedDay(null);
            setSelectedLane(null);
          }}
        />
      )}
    </section>
  );
}
