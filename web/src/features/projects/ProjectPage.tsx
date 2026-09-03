import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowDownWideNarrow, ArrowUpWideNarrow, Search, X } from "lucide-react";

import {
  projectsApi,
  type ApiError,
  type ProjectContextMessage,
  type ProjectSession,
} from "@/lib/api";
import { formatCount } from "@/lib/format";
import { ProjectDocument } from "./ProjectDocument";

const CONTEXT_PAGE = 500;
const SESSION_PAGE = 50;

type Mode = "document" | "sessions";
type Order = "oldest" | "newest";

function when(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function duration(start: string | null, end: string | null): string {
  if (!start || !end) return "";
  const milliseconds = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 60_000) return "";
  const minutes = Math.round(milliseconds / 60_000);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function ProjectPage() {
  const { name } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const project = decodeURIComponent(name ?? "");
  const mode: Mode = searchParams.get("mode") === "sessions" ? "sessions" : "document";
  const explicitOrder = searchParams.get("order");
  const documentOrder: Order = explicitOrder === "newest" ? "newest" : "oldest";
  const sessionOrder: Order = explicitOrder === "oldest" ? "oldest" : "newest";
  const query = searchParams.get("q") ?? "";
  const includeGenerated = searchParams.get("generated") === "1";

  const [draft, setDraft] = useState(query);
  const [moreMessages, setMoreMessages] = useState<ProjectContextMessage[]>([]);
  const [loadingAllMessages, setLoadingAllMessages] = useState(false);
  const [allMessagesLoaded, setAllMessagesLoaded] = useState(false);
  const [messageLoadError, setMessageLoadError] = useState<string | null>(null);
  const [moreSessions, setMoreSessions] = useState<ProjectSession[]>([]);
  const [loadingAllSessions, setLoadingAllSessions] = useState(false);
  const [allSessionsLoaded, setAllSessionsLoaded] = useState(false);
  const [sessionTotalOverride, setSessionTotalOverride] = useState<number | null>(null);
  const [sessionLoadError, setSessionLoadError] = useState<string | null>(null);
  const documentTab = useRef<HTMLButtonElement>(null);
  const sessionsTab = useRef<HTMLButtonElement>(null);
  const messageLoading = useRef(false);
  const sessionLoading = useRef(false);
  const automaticDocumentLoad = useRef<string | null>(null);
  const messageScope = `${project}\u0000${includeGenerated ? "generated" : "human"}`;
  const sessionScope = `${project}\u0000${sessionOrder}\u0000${query}\u0000${includeGenerated ? "generated" : "human"}`;
  const activeMessageScope = useRef(messageScope);
  const activeSessionScope = useRef(sessionScope);

  const context = useQuery({
    queryKey: ["project-context", project, includeGenerated],
    queryFn: () =>
      projectsApi.context(project, {
        order: "oldest",
        limit: CONTEXT_PAGE,
        includeGenerated,
      }),
    enabled: Boolean(project) && mode === "document",
  });

  const sessionIndex = useQuery({
    queryKey: ["project-sessions", project, sessionOrder, query, includeGenerated],
    queryFn: () =>
      projectsApi.sessions(project, {
        order: sessionOrder,
        q: query || undefined,
        limit: SESSION_PAGE,
        offset: 0,
        includeGenerated,
      }),
    enabled: Boolean(project) && mode === "sessions",
  });

  const activeMessageVersion = useRef(context.dataUpdatedAt);
  const activeSessionVersion = useRef(sessionIndex.dataUpdatedAt);
  activeMessageScope.current = messageScope;
  activeSessionScope.current = sessionScope;
  activeMessageVersion.current = context.dataUpdatedAt;
  activeSessionVersion.current = sessionIndex.dataUpdatedAt;

  useEffect(() => {
    setMoreMessages([]);
    setAllMessagesLoaded(false);
    setMessageLoadError(null);
    setLoadingAllMessages(false);
    messageLoading.current = false;
    automaticDocumentLoad.current = null;
  }, [context.dataUpdatedAt, messageScope]);

  useEffect(() => {
    setMoreSessions([]);
    setAllSessionsLoaded(false);
    setSessionTotalOverride(null);
    setSessionLoadError(null);
    setLoadingAllSessions(false);
    sessionLoading.current = false;
  }, [sessionIndex.dataUpdatedAt, sessionScope]);

  useEffect(() => {
    setDraft(query);
  }, [project, query]);

  function update(next: Record<string, string | null>) {
    const updated = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(next)) {
      if (value) updated.set(key, value);
      else updated.delete(key);
    }
    setSearchParams(updated, { replace: true });
  }

  function selectMode(nextMode: Mode, focus = false) {
    update({ mode: nextMode === "sessions" ? "sessions" : null });
    if (focus) {
      (nextMode === "document" ? documentTab : sessionsTab).current?.focus();
    }
  }

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    let nextMode: Mode | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      nextMode = mode === "document" ? "sessions" : "document";
    } else if (event.key === "Home") {
      nextMode = "document";
    } else if (event.key === "End") {
      nextMode = "sessions";
    }
    if (!nextMode) return;
    event.preventDefault();
    selectMode(nextMode, true);
  }

  const contextData = context.data;
  const messages = (contextData?.messages ?? []).concat(moreMessages);
  const documentComplete = Boolean(contextData && (contextData.complete || allMessagesLoaded));

  const loadCompleteDocument = useCallback(async (): Promise<boolean> => {
    if (!contextData) return false;
    if (contextData.complete || allMessagesLoaded) return true;
    if (messageLoading.current) return false;
    const scope = messageScope;
    const version = context.dataUpdatedAt;
    messageLoading.current = true;
    setLoadingAllMessages(true);
    setMessageLoadError(null);
    try {
      let offset = contextData.offset + contextData.messages.length;
      let expectedTotal = contextData.total;
      const seen = new Set(contextData.messages.map((message) => message.id));
      const collected: ProjectContextMessage[] = [];
      let complete: boolean = contextData.complete;
      while (!complete && offset < expectedTotal) {
        const page = await projectsApi.context(project, {
          order: "oldest",
          offset,
          limit: CONTEXT_PAGE,
          includeGenerated,
        });
        if (
          activeMessageScope.current !== scope ||
          activeMessageVersion.current !== version
        ) {
          return false;
        }
        expectedTotal = page.total;
        if (!page.messages.length && !page.complete) {
          throw new Error("The server returned no next page.");
        }
        for (const message of page.messages) {
          if (seen.has(message.id)) continue;
          seen.add(message.id);
          collected.push(message);
        }
        offset = page.offset + page.messages.length;
        complete = page.complete || offset >= expectedTotal;
      }
      if (
        activeMessageScope.current !== scope ||
        activeMessageVersion.current !== version
      ) {
        return false;
      }
      if (!complete && offset < expectedTotal) {
        throw new Error("The complete project could not be loaded.");
      }
      setMoreMessages(collected);
      setAllMessagesLoaded(true);
      return true;
    } catch {
      if (
        activeMessageScope.current === scope &&
        activeMessageVersion.current === version
      ) {
        setMessageLoadError("Could not load the complete project. Try again.");
      }
      return false;
    } finally {
      if (
        activeMessageScope.current === scope &&
        activeMessageVersion.current === version
      ) {
        messageLoading.current = false;
        setLoadingAllMessages(false);
      }
    }
  }, [allMessagesLoaded, context.dataUpdatedAt, contextData, includeGenerated, messageScope, project]);

  useEffect(() => {
    const attempt = `${messageScope}\u0000newest`;
    if (
      mode === "document" &&
      documentOrder === "newest" &&
      contextData &&
      !documentComplete &&
      automaticDocumentLoad.current !== attempt
    ) {
      automaticDocumentLoad.current = attempt;
      void loadCompleteDocument();
    }
  }, [contextData, documentComplete, documentOrder, loadCompleteDocument, messageScope, mode]);

  async function setDocumentOrder(nextOrder: Order) {
    if (nextOrder === "newest" && !documentComplete) {
      const loaded = await loadCompleteDocument();
      if (!loaded) return;
    }
    update({ order: nextOrder === "newest" ? "newest" : null });
  }

  const visibleMessages =
    documentOrder === "newest" && documentComplete ? [...messages].reverse() : messages;

  const firstSessionPage = sessionIndex.data?.sessions ?? [];
  const visibleSessions = firstSessionPage.concat(moreSessions);
  const sessionTotal = sessionTotalOverride ?? sessionIndex.data?.total ?? 0;
  const sessionsComplete = Boolean(
    sessionIndex.data && (allSessionsLoaded || !sessionIndex.data.has_more),
  );

  async function loadAllSessions(): Promise<boolean> {
    if (!sessionIndex.data) return false;
    if (sessionsComplete) return true;
    if (sessionLoading.current) return false;
    const scope = sessionScope;
    const version = sessionIndex.dataUpdatedAt;
    sessionLoading.current = true;
    setLoadingAllSessions(true);
    setSessionLoadError(null);
    try {
      let offset = sessionIndex.data.offset + sessionIndex.data.sessions.length;
      let expectedTotal = sessionIndex.data.total;
      let hasMore = sessionIndex.data.has_more;
      const seen = new Set(sessionIndex.data.sessions.map((session) => session.id));
      const collected: ProjectSession[] = [];
      while (hasMore || offset < expectedTotal) {
        const page = await projectsApi.sessions(project, {
          order: sessionOrder,
          q: query || undefined,
          limit: SESSION_PAGE,
          offset,
          includeGenerated,
        });
        if (
          activeSessionScope.current !== scope ||
          activeSessionVersion.current !== version
        ) {
          return false;
        }
        expectedTotal = page.total;
        hasMore = page.has_more;
        if (!page.sessions.length && hasMore) {
          throw new Error("The server returned no next page.");
        }
        for (const session of page.sessions) {
          if (seen.has(session.id)) continue;
          seen.add(session.id);
          collected.push(session);
        }
        offset = page.offset + page.sessions.length;
        if (!page.sessions.length) break;
      }
      if (
        activeSessionScope.current !== scope ||
        activeSessionVersion.current !== version
      ) {
        return false;
      }
      if (hasMore || offset < expectedTotal) {
        throw new Error("The complete session list could not be loaded.");
      }
      setMoreSessions(collected);
      setSessionTotalOverride(expectedTotal);
      setAllSessionsLoaded(true);
      return true;
    } catch {
      if (
        activeSessionScope.current === scope &&
        activeSessionVersion.current === version
      ) {
        setSessionLoadError("Could not load every session. Try again.");
      }
      return false;
    } finally {
      if (
        activeSessionScope.current === scope &&
        activeSessionVersion.current === version
      ) {
        sessionLoading.current = false;
        setLoadingAllSessions(false);
      }
    }
  }

  const pageSubtitle =
    mode === "document"
      ? (contextData?.summary ?? "…")
      : sessionIndex.data
        ? `${formatCount(sessionIndex.data.total)} session${sessionIndex.data.total === 1 ? "" : "s"}${
            query ? ` matching “${query}”` : ""
          }`
        : "…";

  return (
    <>
      <header className="page-header">
        <Link to="/" className="backlink">
          ← Overview
        </Link>
        <h1 className="page-title">{project}</h1>
        <p className="page-subtitle">{pageSubtitle}</p>
      </header>

      <div className="project-view-tabs mode-switch" role="tablist" aria-label="Project view">
        <button
          ref={documentTab}
          id="project-document-tab"
          type="button"
          role="tab"
          aria-selected={mode === "document"}
          aria-controls="project-document-panel"
          tabIndex={mode === "document" ? 0 : -1}
          className={mode === "document" ? "is-on" : ""}
          onClick={() => selectMode("document")}
          onKeyDown={handleTabKeyDown}
        >
          Document
        </button>
        <button
          ref={sessionsTab}
          id="project-sessions-tab"
          type="button"
          role="tab"
          aria-selected={mode === "sessions"}
          aria-controls="project-sessions-panel"
          tabIndex={mode === "sessions" ? 0 : -1}
          className={mode === "sessions" ? "is-on" : ""}
          onClick={() => selectMode("sessions")}
          onKeyDown={handleTabKeyDown}
        >
          Sessions
        </button>
      </div>

      <section
        id="project-document-panel"
        role="tabpanel"
        aria-labelledby="project-document-tab"
        hidden={mode !== "document"}
      >
          <div className="proj-controls project-document-controls">
            <div className="mode-switch" role="group" aria-label="Document chronology">
              <button
                type="button"
                className={documentOrder === "oldest" ? "is-on" : ""}
                aria-pressed={documentOrder === "oldest"}
                onClick={() => void setDocumentOrder("oldest")}
              >
                <ArrowUpWideNarrow size={14} aria-hidden /> Oldest first
              </button>
              <button
                type="button"
                className={documentOrder === "newest" ? "is-on" : ""}
                aria-pressed={documentOrder === "newest"}
                onClick={() => void setDocumentOrder("newest")}
              >
                <ArrowDownWideNarrow size={14} aria-hidden /> Newest first
              </button>
            </div>
          </div>

          {context.isPending && <p className="ask-status">Loading…</p>}
          {context.error && (
            <ProjectError error={context.error as ApiError} onRetry={() => context.refetch()} />
          )}
          {messageLoadError && <p className="ask-status" role="alert">{messageLoadError}</p>}
          {contextData && (
            <ProjectDocument
              summary={contextData.summary}
              knowledge={contextData.knowledge}
              messages={visibleMessages}
              complete={documentComplete}
              onLoadComplete={loadCompleteDocument}
              loading={loadingAllMessages}
            />
          )}
      </section>

      <section
        id="project-sessions-panel"
        role="tabpanel"
        aria-labelledby="project-sessions-tab"
        hidden={mode !== "sessions"}
      >
          <div className="proj-controls">
            <form
              className="searchbar proj-search"
              onSubmit={(event) => {
                event.preventDefault();
                update({ q: draft.trim() || null });
              }}
            >
              <Search size={15} aria-hidden className="searchbar-icon" />
              <input
                className="searchbar-input"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
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

            <div className="mode-switch" role="group" aria-label="Session sort order">
              <button
                type="button"
                className={sessionOrder === "newest" ? "is-on" : ""}
                aria-pressed={sessionOrder === "newest"}
                onClick={() => update({ order: null })}
              >
                <ArrowDownWideNarrow size={14} aria-hidden /> Newest first
              </button>
              <button
                type="button"
                className={sessionOrder === "oldest" ? "is-on" : ""}
                aria-pressed={sessionOrder === "oldest"}
                onClick={() => update({ order: "oldest" })}
              >
                <ArrowUpWideNarrow size={14} aria-hidden /> Oldest first
              </button>
            </div>
          </div>

          {sessionIndex.isPending && <p className="ask-status">Loading…</p>}
          {sessionIndex.error && (
            <ProjectError error={sessionIndex.error as ApiError} onRetry={() => sessionIndex.refetch()} />
          )}

          {!sessionIndex.isPending && !sessionIndex.error && visibleSessions.length === 0 && (
            <div className="empty-state">
              <h2>{query ? `Nothing in ${project} matches “${query}”` : "No sessions yet"}</h2>
              {query && <p>Search covers session titles and every message inside them.</p>}
            </div>
          )}
          {sessionLoadError && <p className="ask-status" role="alert">{sessionLoadError}</p>}

          <ol className="proj-sessions">
            {visibleSessions.map((session) => (
              <li key={session.id}>
                <Link to={`/c/${session.id}`} className="proj-session">
                  <div className="proj-session-main">
                    <span className="proj-session-title">
                      {session.title || <span className="proj-untitled">(untitled session)</span>}
                    </span>
                    <span className="proj-session-meta">
                      <time dateTime={session.started_at ?? undefined}>{when(session.started_at)}</time>
                      {duration(session.started_at, session.ended_at) && (
                        <span>{duration(session.started_at, session.ended_at)}</span>
                      )}
                      <span>{session.source_tool}</span>
                      {session.git_branch && <span className="proj-branch">{session.git_branch}</span>}
                    </span>
                  </div>
                  <span className="proj-session-count tabular">
                    {formatCount(session.message_count)}
                    <span className="proj-session-unit">msg</span>
                  </span>
                </Link>
              </li>
            ))}
          </ol>

          {!sessionsComplete && (
            <button
              type="button"
              className="button stack-top"
              onClick={() => void loadAllSessions()}
              disabled={loadingAllSessions}
            >
              {loadingAllSessions
                ? `Loading… (${formatCount(visibleSessions.length)} of ${formatCount(sessionTotal)})`
                : `Load all sessions — ${formatCount(visibleSessions.length)} of ${formatCount(sessionTotal)} shown`}
            </button>
          )}

          {sessionIndex.data && sessionIndex.data.hidden_generated > 0 && (
            <p className="proj-hidden">
              {includeGenerated ? (
                <>
                  Showing machine-generated sessions too. These are tool calls Throughline and other
                  automation made on your behalf.{" "}
                  <button type="button" className="linkbutton" onClick={() => update({ generated: null })}>
                    Hide them
                  </button>
                </>
              ) : (
                <>
                  {formatCount(sessionIndex.data.hidden_generated)} machine-generated sessions in this
                  project are not listed. They are stored, not deleted.{" "}
                  <button type="button" className="linkbutton" onClick={() => update({ generated: "1" })}>
                    Show them
                  </button>
                </>
              )}
            </p>
          )}
      </section>
    </>
  );
}

function ProjectError({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  return (
    <div className="empty-state">
      <h2>Could not load project history</h2>
      <p>{error.message}</p>
      {error.hint && <p className="empty-hint">{error.hint}</p>}
      <button type="button" className="button" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}
