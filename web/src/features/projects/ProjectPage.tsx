import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowDownWideNarrow, ArrowUpWideNarrow, Search, X } from "lucide-react";

import { ApiError, projectsApi, type ProjectSession } from "@/lib/api";
import { formatCount } from "@/lib/format";

/**
 * One project's history: its sessions, oldest or newest first, searchable.
 *
 * Sessions, not messages. This project holds 7,461 messages across 28
 * sessions, and one session alone holds 5,560 — rendering "the whole history"
 * as a flat message list is a page that never finishes and a reader who cannot
 * find anything. A session row is the unit a person remembers: a date, a
 * length, and what it was about.
 *
 * Search runs on the server against both the session title and its messages.
 * Filtering 7,461 messages in the browser would mean shipping them first,
 * which is the same mistake in a different place.
 */

const PAGE = 50;

function when(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(d);
}

/** How long a session ran, when both ends are known. */
function duration(start: string | null, end: string | null): string {
  if (!start || !end) return "";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 60_000) return "";
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  return `${h}h ${mins % 60}m`;
}

export function ProjectPage() {
  const { name } = useParams();
  const [sp, setSp] = useSearchParams();
  const project = decodeURIComponent(name ?? "");

  // Sort and search live in the URL, so a view of a project is a link someone
  // can keep or share rather than a state only this tab knows about.
  const order = (sp.get("order") === "oldest" ? "oldest" : "newest") as "newest" | "oldest";
  const q = sp.get("q") ?? "";
  // Whether machine-generated sessions are listed. In the URL like the rest,
  // and off by default — but the reader's call, not the interface's. Some of
  // what gets labelled "agent-consultation" is a person's own multi-agent
  // work, and hiding that permanently would be deciding what counts as their
  // history on their behalf.
  const includeGenerated = sp.get("generated") === "1";
  const [draft, setDraft] = useState(q);

  // Sessions beyond the first page, appended as they arrive. Held here rather
  // than fetched by bumping an `offset` query param on the main query: that
  // approach fetched page N but never combined it with pages 1..N-1, so
  // clicking "Show more" replaced the list with the next 50 rather than
  // growing it — the button's own "X of Y" label promised accumulation that
  // never happened.
  const [more, setMore] = useState<ProjectSession[]>([]);
  const [loadingAll, setLoadingAll] = useState(false);

  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["project-sessions", project, order, q, includeGenerated],
    queryFn: () =>
      projectsApi.sessions(project, {
        order,
        q,
        limit: PAGE,
        offset: 0,
        includeGenerated,
      }),
    enabled: Boolean(project),
  });

  // A new filter, sort, or project means the accumulated tail belongs to the
  // previous view.
  useEffect(() => {
    setMore([]);
  }, [project, order, q, includeGenerated]);

  function update(next: Record<string, string | null>) {
    const p = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(next)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    setSp(p, { replace: true });
  }

  const firstPage = data?.sessions ?? [];
  const sessions = firstPage.concat(more);
  const total = data?.total ?? 0;
  const complete = sessions.length >= total;

  // Fetches every remaining page in one loop rather than one click per 50
  // sessions, mirroring the conversation transcript's "Load full transcript".
  async function loadAll() {
    if (loadingAll || complete) return;
    setLoadingAll(true);
    try {
      let loaded = sessions.length;
      while (loaded < total) {
        const next = await projectsApi.sessions(project, {
          order,
          q,
          limit: PAGE,
          offset: loaded,
          includeGenerated,
        });
        if (!next.sessions.length) break;
        setMore((prev) => prev.concat(next.sessions));
        loaded += next.sessions.length;
      }
    } finally {
      setLoadingAll(false);
    }
  }

  if (error) {
    const e = error as ApiError;
    return (
      <>
        <header className="page-header">
          <Link to="/" className="backlink">
            ← Overview
          </Link>
          <h1 className="page-title">{project}</h1>
        </header>
        <div className="empty-state">
          <h2>Could not load project history</h2>
          <p>{e.message}</p>
          {e.hint && <p className="empty-hint">{e.hint}</p>}
          <button type="button" className="button" onClick={() => refetch()}>
            Try again
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <header className="page-header">
        <Link to="/" className="backlink">
          ← Overview
        </Link>
        <h1 className="page-title">{project}</h1>
        <p className="page-subtitle">
          {data
            ? `${formatCount(data.total)} session${data.total === 1 ? "" : "s"}${
                q ? ` matching “${q}”` : ""
              }`
            : "…"}
        </p>
      </header>

      <div className="proj-controls">
        <form
          className="searchbar proj-search"
          onSubmit={(e) => {
            e.preventDefault();
            update({ q: draft.trim() || null });
          }}
        >
          <Search size={15} aria-hidden className="searchbar-icon" />
          <input
            className="searchbar-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={`Search inside ${project}…`}
            aria-label={`Search inside ${project}`}
          />
          {draft && (
            <button
              type="button"
              className="icon-button"
              onClick={() => {
                setDraft("");
                update({ q: null });
              }}
              aria-label="Clear search"
            >
              <X size={15} aria-hidden />
            </button>
          )}
        </form>

        {/* Both directions, because both questions are asked: "what was I just
            doing" and "how did this start". */}
        <div className="mode-switch" role="group" aria-label="Sort order">
          <button
            type="button"
            className={order === "newest" ? "is-on" : ""}
            aria-pressed={order === "newest"}
            onClick={() => update({ order: null })}
          >
            <ArrowDownWideNarrow size={14} aria-hidden /> Newest first
          </button>
          <button
            type="button"
            className={order === "oldest" ? "is-on" : ""}
            aria-pressed={order === "oldest"}
            onClick={() => update({ order: "oldest" })}
          >
            <ArrowUpWideNarrow size={14} aria-hidden /> Oldest first
          </button>
        </div>
      </div>

      {isPending && <p className="ask-status">Loading…</p>}

      {!isPending && sessions.length === 0 && (
        <div className="empty-state">
          <h2>{q ? `Nothing in ${project} matches “${q}”` : "No sessions yet"}</h2>
          {q && <p>Search covers session titles and every message inside them.</p>}
        </div>
      )}

      <ol className="proj-sessions">
        {sessions.map((s) => (
          <li key={s.id}>
            <Link to={`/c/${s.id}`} className="proj-session">
              <div className="proj-session-main">
                <span className="proj-session-title">
                  {s.title || <span className="proj-untitled">(untitled session)</span>}
                </span>
                <span className="proj-session-meta">
                  <time dateTime={s.started_at ?? undefined}>{when(s.started_at)}</time>
                  {duration(s.started_at, s.ended_at) && <span>{duration(s.started_at, s.ended_at)}</span>}
                  <span>{s.source_tool}</span>
                  {s.git_branch && <span className="proj-branch">{s.git_branch}</span>}
                </span>
              </div>
              <span className="proj-session-count tabular">
                {formatCount(s.message_count)}
                <span className="proj-session-unit">msg</span>
              </span>
            </Link>
          </li>
        ))}
      </ol>

      {!complete && (
        <button type="button" className="button stack-top" onClick={loadAll} disabled={loadingAll}>
          {loadingAll
            ? `Loading… (${formatCount(sessions.length)} of ${formatCount(total)})`
            : `Load all sessions — ${formatCount(sessions.length)} of ${formatCount(total)} shown`}
        </button>
      )}

      {/* What is not shown, and why. A project listing 29 sessions out of 689
          stored rows has to account for the difference, or the interface is
          quietly deciding what counts as the user's history. */}
      {data && data.hidden_generated > 0 && (
        <p className="proj-hidden">
          {includeGenerated ? (
            <>
              Showing machine-generated sessions too — tool calls Throughline and other automation
              made on your behalf.{" "}
              <button type="button" className="linkbutton" onClick={() => update({ generated: null })}>
                Hide them
              </button>
            </>
          ) : (
            <>
              {formatCount(data.hidden_generated)} machine-generated sessions in this project are
              not listed — tool calls Throughline and other automation made on your behalf. They are
              stored, not deleted.{" "}
              <button type="button" className="linkbutton" onClick={() => update({ generated: "1" })}>
                Show them
              </button>
            </>
          )}
        </p>
      )}
    </>
  );
}
