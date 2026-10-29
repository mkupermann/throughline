import { t } from "@/lib/ui";
import { getLang, useLanguage } from "@/lib/language";
import { Link } from "react-router-dom";
import { OctagonAlert } from "lucide-react";

import { ApiError, type TimelineDayItem } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { projectLabel } from "../projects/ProjectName";

/** Where a timeline row opens.
 *
 * Deliberately separate from Find's `routeFor`: that takes a `FindItem`, and a
 * `TimelineDayItem` has its own event kinds — `entity`,
 * `reflection` and `ingestion` have no detail route, so they render as plain
 * text instead of a link that would land on a 404.
 */
function timelineRouteFor(item: TimelineDayItem): string | null {
  switch (item.kind) {
    case "conversation":
      return `/c/${item.id}`;
    case "skill":
      return `/s/${item.id}`;
    case "prompt":
      return `/p/${item.id}`;
    default:
      // entity, reflection, ingestion and project have no detail route here.
      return null;
  }
}

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
  error,
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
  error: unknown;
  onClose: () => void;
}) {
  useLanguage();
  const items = data?.items ?? [];
  const groups = new Map<string, TimelineDayItem[]>();
  for (const item of items.filter(item => item.kind === "conversation")) {
    const key = item.project || "(no project)";
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
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
        <button type="button" className="linkbutton" onClick={onClose}>{t("Close")}</button>
      </div>

      {isLoading && <p className="muted">{t("Loading…")}</p>}

      {error ? (
        <div className="empty-state">
          <OctagonAlert size={22} aria-hidden />
          <h3>{t("Cannot load events for this day")}</h3>
          <p>{(error as ApiError).message}</p>
          {(error as ApiError).hint && <p className="empty-hint">{(error as ApiError).hint}</p>}
        </div>
      ) : null}

      {!isLoading && !error && items.length === 0 && (
        <p className="empty-state">{t("No events on ")}{day}
          {providers.length > 0 ? " for the current provider scope." : "."}
        </p>
      )}

      {items.length > 0 && (
        <>
          {truncated && (
            <p className="timeline-detail-truncated muted">{t("Showing ")}{formatCount(items.length)} of {formatCount(total)}.
            </p>
          )}
          <div className="timeline-projects">
            {[...groups].map(([project, conversations]) => (
              <section className="timeline-project" key={project}>
                <h3><Link to={`/project/${encodeURIComponent(project)}`}>
                  {project === "(no project)" ? t("Assignment missing") : projectLabel({project, display_name: conversations[0].display_name, context_label:conversations[0].context_label})}
                </Link></h3>
                {conversations[0].name_origin === "model" && <p>{t("AI-suggested name · source conversations inside")}</p>}
                <p className="muted">{t(project === "(no project)"
                  ? "No project context is recorded. Review these conversations before assigning them."
                  : "Grouped by imported folder context. This does not establish a dependency between conversations.")}</p>
                <details>
                  <summary>{t("Conversations in this day selection")}: {formatCount(conversations.length)}</summary>
                  <ul className="timeline-detail-list">{conversations.map(item => <TimelineRow key={item.id} item={item} />)}</ul>
                </details>
              </section>
            ))}
          </div>
          <ul className="timeline-detail-list">
            {items.filter(item => item.kind !== "conversation").map(item => <TimelineRow key={`${item.kind}-${item.id}`} item={item} />)}
          </ul>
        </>
      )}
    </div>
  );
}

function exactDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(getLang() === "de" ? "de-DE" : "en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit", timeZoneName: "longOffset", hour12: false,
  }).format(date);
}

function TimelineRow({item}: {item: TimelineDayItem}) {
  const to = timelineRouteFor(item);
  const body = <>
    <time dateTime={item.ts} className="timeline-detail-time tabular">{exactDateTime(item.ts)}</time>
    {item.kind !== "conversation" && <span className={`kind kind-${item.kind}`}>{item.kind}</span>}
    <span className="timeline-detail-title">{item.title}</span>
    <span className="timeline-detail-provider">{item.provider}</span>
  </>;
  return <li>{to ? <Link to={to} className="timeline-detail-row timeline-detail-link">{body}</Link>
    : <span className="timeline-detail-row">{body}</span>}</li>;
}
