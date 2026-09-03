import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { OctagonAlert, RefreshCw } from "lucide-react";

import { ApiError, providersApi, timelineApi } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { readProviders } from "@/lib/providerScope";

import { RangeControl, bucketSpan, enumerateBuckets, presetRange, type Range } from "./RangeControl";
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

/** Cell shade for `n` events, against the busiest cell in the range.
 *
 * Logarithmic, not linear. Activity here is heavily skewed — one day in this
 * corpus holds 8,596 message events while a typical day holds single digits.
 * On a linear ramp every ordinary day computes to within a percent of the
 * floor, so the grid renders as one flat tone with a couple of bright cells
 * and reads as "nothing happened", which is false. A log ramp spends its
 * range where the data actually lives.
 *
 * Zero keeps a faint tint rather than vanishing: an empty day is a real
 * observation, and a blank cell is indistinguishable from a missing one.
 */
export function cellOpacity(n: number, max: number): number {
  if (n <= 0) return 0.08;
  if (max <= 1) return 1;
  const t = Math.log(n + 1) / Math.log(max + 1);
  return 0.25 + 0.75 * t;
}

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
  // Set once the reader picks or dismisses a day. Guards the arrival default
  // below from overriding a deliberate choice — including the choice to close.
  const chosenByUser = useRef(false);
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
    // A new range is a new question, so it gets the default answer again.
    chosenByUser.current = false;
  };

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    p.set("since", range.since);
    p.set("until", range.until);
    for (const name of providers) p.append("provider", name);
    return p;
  }, [range.since, range.until, providers.join(",")]);

  const { data, isLoading, error, refetch } = useQuery({
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

  const { data: dayData, isLoading: dayLoading, error: dayError } = useQuery({
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
    chosenByUser.current = true;
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
    const cellTotals = new Map<string, number>();
    const laneTotals = new Map<string, number>();
    for (const c of cells) {
      laneSet.add(c.provider);
      const key = `${c.provider}|${c.bucket}`;
      cellTotals.set(key, (cellTotals.get(key) ?? 0) + c.n);
      laneTotals.set(c.provider, (laneTotals.get(c.provider) ?? 0) + c.n);
    }
    // Busiest lane first, so the eye starts where the activity is. Alphabetical
    // order put "(unattributed)" — a residue lane holding 8 rows — above Vibe,
    // which is a tool the user actually works in. The two catch-all lanes sink
    // to the bottom regardless of size: they are the leftovers, and reading
    // them first tells you nothing about your work.
    const rank = (p: string) => (p === NOT_TOOL_SPECIFIC ? 2 : p === UNATTRIBUTED ? 1 : 0);
    const ordered = [...laneSet].sort(
      (a, b) =>
        rank(a) - rank(b) ||
        (laneTotals.get(b) ?? 0) - (laneTotals.get(a) ?? 0) ||
        a.localeCompare(b),
    );
    return {
      lanes: ordered,
      // Every bucket in the range, not only the ones with rows — see
      // enumerateBuckets. Columns must be evenly spaced in TIME, or the axis
      // is decoration.
      //
      // Spanned from the RESPONSE's dates, not the control's. The server is
      // free to answer for a different window than the one requested (it
      // re-buckets, and "All" resolves to whatever the data actually covers),
      // and columns derived from the request would then not line up with the
      // cells they are supposed to hold.
      buckets: data ? enumerateBuckets(data.since, data.until, data.bucket) : [],
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

  /**
   * Open the most recent active day on arrival, so the page answers its own
   * question without being asked twice.
   *
   * The grid alone filled the top third of the screen and left the rest blank
   * behind an instruction to click something. "What happened, and when" has an
   * obvious default answer — the last thing that happened — and showing it
   * costs the reader nothing: the detail panel is already scoped, closable, and
   * replaced by any other cell they pick.
   *
   * `chosenByUser` is what keeps this from fighting them. Once they have
   * clicked a cell or closed the panel, this never runs again for the session,
   * so closing the panel does not immediately reopen it. Changing the range
   * clears the flag deliberately — a new range is a new question, and the same
   * default applies to it.
   */
  useEffect(() => {
    if (chosenByUser.current || selectedDay || !data || data.bucket !== "day") return;
    let bestLane: string | null = null;
    let bestDay: string | null = null;
    for (const [key, n] of totals) {
      if (n <= 0) continue;
      const [lane, day] = key.split("|");
      // Latest day wins; within a day, the busiest lane wins. Equal totals
      // need a stable final key too: SQL row order is not a UI decision, and
      // otherwise reloading the same timeline could open a different lane.
      const bestCount = bestLane && bestDay ? totals.get(`${bestLane}|${bestDay}`) ?? 0 : 0;
      if (
        !bestDay ||
        day > bestDay ||
        (day === bestDay &&
          (n > bestCount || (n === bestCount && lane.localeCompare(bestLane ?? "") < 0)))
      ) {
        bestLane = lane;
        bestDay = day;
      }
    }
    if (bestDay && bestLane) {
      setSelectedLane(bestLane);
      setSelectedDay(bestDay);
    }
  }, [data, totals, selectedDay]);

  return (
    <section className="timeline-page">
      <header className="page-header">
        <h1 className="page-title">Timeline</h1>
        {/* Says what the page answers, then what an "event" is. The subtitle
            used to read "38,376 event(s) between … , bucketed by day", which
            names a unit nothing on the page defines and a word ("bucketed")
            that belongs to the query, not to the reader. */}
        <p className="page-subtitle">What happened, and when.</p>
        <p className="page-hint">
          {formatCount(grandTotal)} messages and memory entries from {range.since} to{" "}
          {range.until}, one column per {data?.bucket ?? "day"}.
        </p>
      </header>

      <RangeControl value={range} onChange={handleRangeChange} />

      {isLoading && <p className="muted">Loading…</p>}

      {error && (
        <div className="empty-state">
          <OctagonAlert size={22} aria-hidden />
          <h2>Cannot load the timeline</h2>
          <p>{(error as ApiError).message}</p>
          {(error as ApiError).hint && <p className="empty-hint">{(error as ApiError).hint}</p>}
          <button type="button" className="button" onClick={() => refetch()}>
            <RefreshCw size={14} aria-hidden />
            Try again
          </button>
        </div>
      )}

      {!isLoading && !error && lanes.length === 0 && (
        <p className="empty-state">No activity in this range.</p>
      )}

      {lanes.length > 0 && (
        <div className="timeline-grid" role="table" aria-label="Activity by provider over time">
          <div className="timeline-lane timeline-axis" role="row" aria-hidden="true">
            <span className="timeline-lane-label" />
            <div className="timeline-cells">
              {buckets.map((b, i) => {
                const last = buckets.length - 1;
                // The end date always gets a tick, and a strided tick is
                // dropped when it would land on top of it. Labelling both
                // unconditionally put "08-09" and "08-11" 21px apart with ~30px
                // of text each: they overlapped into an unreadable smear at the
                // one end of the axis a reader looks at first.
                const show =
                  i === last || (i % stride === 0 && last - i >= stride / 2);
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
                  const bar = (
                    <span
                      className="timeline-cell-bar"
                      style={{ opacity: cellOpacity(n, max) }}
                      aria-hidden
                    />
                  );
                  return (
                    // The wrapper carries the grid's `cell` role. Active days
                    // contain a real button. Empty days stay visual but inert,
                    // keeping hundreds of no-op targets out of the keyboard
                    // order without breaking the continuous date grid.
                    <span key={b} role="cell" className="timeline-cell-wrap">
                      {n > 0 ? (
                        <button
                          type="button"
                          className="timeline-cell"
                          title={`${laneLabel(lane, providerLabels)} · ${b} · ${n}`}
                          aria-label={`${laneLabel(lane, providerLabels)}, ${b}, ${n} events`}
                          onClick={() => handleCellClick(lane, b)}
                        >
                          {bar}
                        </button>
                      ) : (
                        <span className="timeline-cell is-empty" aria-hidden>
                          {bar}
                        </span>
                      )}
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* A key and an instruction. The grid shipped with neither: five rows of
          blue squares, nothing saying what darker meant, and no sign the cells
          could be clicked at all — so the drill-down that exists was invisible,
          and two thirds of the page stayed empty because nobody opened it. */}
      {lanes.length > 0 && (
        <div className="timeline-key">
          {/* Even steps of SHADE, not of count. Stepping the counts evenly and
              running them through cellOpacity produced four near-identical
              dark swatches, because the ramp is logarithmic — the legend then
              showed two apparent levels for an encoding that has many, which
              is worse than no legend. A ramp key explains the visual range; the
              endpoints carry the numbers. */}
          <span className="timeline-key-scale" aria-hidden="true">
            <span className="timeline-key-label">quiet</span>
            {[0.08, 0.32, 0.55, 0.78, 1].map((o) => (
              <span key={o} className="timeline-cell">
                <span className="timeline-cell-bar" style={{ opacity: o }} />
              </span>
            ))}
            <span className="timeline-key-label">busy ({formatCount(max)})</span>
          </span>
          <span className="timeline-key-hint">
            {selectedDay ? "Select another cell to change days." : "Select a cell to read that day."}
          </span>
        </div>
      )}

      {selectedDay && (
        <TimelineDetail
          day={selectedDay}
          providers={selectedLane && selectedLane !== NOT_TOOL_SPECIFIC ? [selectedLane] : []}
          total={selectedTotal}
          data={dayData}
          isLoading={dayLoading}
          error={dayError}
          onClose={() => {
            chosenByUser.current = true;
            setSelectedDay(null);
            setSelectedLane(null);
          }}
        />
      )}
    </section>
  );
}
