import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import type { FindItem, Kind } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { routeFor } from "./ResultList";

/**
 * Results arranged over time.
 *
 * Replaces the Calendar page. That page wrapped FullCalendar, which is a
 * month-grid widget for *scheduling* — it wants events with start and end
 * times in a fixed grid. What this data actually is, is a stream of things
 * that happened, and the questions are "when was this busy?" and "what
 * happened that day?". A density strip plus a day-grouped list answers both,
 * matches the token system, and does not add 200 kB of calendar engine for a
 * grid nobody schedules into.
 *
 * All eight sources the old page assembled are reachable, because the kind
 * facet drives the same query: conversations, messages, memory, skills,
 * projects, prompts, plus entities and reflections via their own routes.
 */
const KIND_VAR: Record<Kind, string> = {
  memory: "var(--chart-1)",
  message: "var(--chart-5)",
  conversation: "var(--chart-6)",
  skill: "var(--chart-4)",
  project: "var(--chart-3)",
  prompt: "var(--chart-2)",
};

function dayKey(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

function humanDay(key: string): string {
  const d = new Date(`${key}T00:00:00`);
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(d);
}

export function ResultTimeline({ items }: { items: FindItem[] }) {
  const [focused, setFocused] = useState<string | null>(null);

  const { days, undated, max } = useMemo(() => {
    const byDay = new Map<string, FindItem[]>();
    const noDate: FindItem[] = [];
    for (const item of items) {
      const key = dayKey(item.occurred_at);
      if (!key) {
        noDate.push(item);
        continue;
      }
      const list = byDay.get(key);
      if (list) list.push(item);
      else byDay.set(key, [item]);
    }
    const sorted = [...byDay.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
    return {
      days: sorted,
      undated: noDate,
      max: Math.max(1, ...sorted.map(([, v]) => v.length)),
    };
  }, [items]);

  if (!days.length && !undated.length) return null;

  const visible = focused ? days.filter(([d]) => d === focused) : days;

  return (
    <div className="timeline">
      {/* Density strip: one column per day with results, tallest = busiest.
          Click to focus a day; the label is on the tooltip and the aria-label
          so the bar is never the only carrier of meaning.

          Hidden below two days: a single full-width bar compares nothing and
          reads as a rendering fault rather than as data. */}
      {days.length > 1 && (
      <div className="tl-strip" role="group" aria-label="Activity by day">
        {days
          .slice()
          .reverse()
          .map(([day, list]) => (
            <button
              key={day}
              type="button"
              className={`tl-bar${focused === day ? " is-on" : ""}`}
              style={{ height: `${Math.max(8, (list.length / max) * 100)}%` }}
              title={`${humanDay(day)} — ${list.length} result${list.length === 1 ? "" : "s"}`}
              aria-label={`${humanDay(day)}, ${list.length} results`}
              onClick={() => setFocused((f) => (f === day ? null : day))}
            />
          ))}
      </div>
      )}

      {focused && (
        <button type="button" className="tl-clear" onClick={() => setFocused(null)}>
          Showing {humanDay(focused)} — show all days
        </button>
      )}

      <ol className="tl-days">
        {visible.map(([day, list]) => (
          <li key={day} className="tl-day">
            <div className="tl-day-head">
              <h3>{humanDay(day)}</h3>
              <span className="tabular">{formatCount(list.length)}</span>
            </div>
            <ul className="tl-events">
              {list.map((item) => (
                <li key={`${item.kind}-${item.id}`}>
                  <Link to={routeFor(item)} className="tl-event">
                    <span
                      className="tl-dot"
                      style={{ background: KIND_VAR[item.kind] }}
                      aria-hidden
                    />
                    <span className={`kind kind-${item.kind}`}>{item.kind}</span>
                    <span className="tl-event-text">
                      {/* A message's `title` is its conversation's summary,
                          identical across every message in that conversation —
                          as a row label it makes distinct events look like
                          duplicates. Its own text is the snippet. */}
                      {(item.kind === "message"
                        ? item.snippet || item.title
                        : item.title || item.snippet) || `#${item.id}`}
                    </span>
                    {item.project && <span className="tl-event-project">{item.project}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>

      {undated.length > 0 && !focused && (
        <div className="tl-undated">
          {formatCount(undated.length)} result{undated.length === 1 ? "" : "s"} with no date.
        </div>
      )}
    </div>
  );
}
