import type { TimelineDayItem } from "@/lib/api";
import { formatCount } from "@/lib/format";

/**
 * What a clicked cell actually contains (design spec §5.1: "clicking a cell
 * is what loads rows"). Only a day-bucket cell can open this directly — a
 * week or month cell has no single date to ask `/timeline/day/{date}` for,
 * so TimelinePage zooms the range into that bucket's span instead of calling
 * this with a fraction of what the cell counted.
 *
 * `/timeline/day/{date}` caps its response (throughline/api/routers/
 * timeline.py's MAX_DETAIL default of 100) — a cell whose count is in the
 * thousands would otherwise open a silently truncated list. `total` is the
 * cell's own aggregate count, the same number already shown in its
 * aria-label, not a second query — see TimelinePage's `selectedTotal`.
 */
export function TimelineDetail({
  day,
  providers,
  total,
  data,
  isLoading,
  onClose,
}: {
  day: string;
  /** The active provider scope, carried into the detail request and stated
   *  here so the list never implies it covers more than it does. */
  providers: string[];
  /** The clicked cell's own count, for "showing N of total" — undefined
   *  before the cell total is known (should not happen once a day is open,
   *  but the type stays honest about it). */
  total: number | undefined;
  data: { day: string; items: TimelineDayItem[] } | undefined;
  isLoading: boolean;
  onClose: () => void;
}) {
  const items = data?.items ?? [];
  const truncated = total !== undefined && items.length < total;

  return (
    <div className="timeline-detail" role="region" aria-label={`Events on ${day}`}>
      <div className="timeline-detail-head">
        <h2>
          {day}
          {providers.length > 0 && (
            <span className="timeline-detail-scope"> · scoped to {providers.join(", ")}</span>
          )}
        </h2>
        <button type="button" className="linkbutton" onClick={onClose}>
          Close
        </button>
      </div>

      {isLoading && <p className="muted">Loading…</p>}

      {!isLoading && items.length === 0 && (
        <p className="empty-state">
          No events on {day}
          {providers.length > 0 ? " for the current provider scope." : "."}
        </p>
      )}

      {items.length > 0 && (
        <>
          {truncated && (
            <p className="timeline-detail-truncated muted">
              Showing {formatCount(items.length)} of {formatCount(total)}.
            </p>
          )}
          <ul className="timeline-detail-list">
            {items.map((item) => (
              <li key={`${item.kind}-${item.id}`} className="timeline-detail-row">
                <span className={`kind kind-${item.kind}`}>{item.kind}</span>
                <span className="timeline-detail-title">{item.title}</span>
                <span className="timeline-detail-provider">{item.provider}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
