import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Download,
  ExternalLink,
  Search,
  BookOpen,
  Plus,
} from "lucide-react";
import {
  storyApi,
  type StoryCheckpoint,
  type StorySession,
  type StoryHistory,
} from "@/lib/api";
import "./story.css";

const labels = {
  goal: "Goal",
  status: "Current position",
  blocker: "Open question / blocker",
  next: "Next step",
};
const kinds = Object.keys(labels) as StoryCheckpoint["kind"][];
const date = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "Time not recorded";
const sourceUrl = (id: number, message?: number | null) =>
  `/c/${id}${message ? `#m${message}` : ""}`;

type Source = { conversation: number; message: number | null; excerpt: string };

export function ProjectStory() {
  const { name = "" } = useParams();
  const [params] = useSearchParams();
  // Remount scoped state when navigating: selections and late requests cannot leak across projects.
  return <Story key={`${name}:${params.toString()}`} project={name} />;
}

function Story({ project }: { project: string }) {
  const [params, setParams] = useSearchParams();
  const path = params.get("path");
  const term = params.get("q") ?? "";
  const order = params.get("order") === "oldest" ? "oldest" : "newest";
  const generated = params.get("generated") === "true";
  const [draft, setDraft] = useState(term);
  const [source, setSource] = useState<Source | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);
  const queryParams = new URLSearchParams({
    order,
    q: term,
    generated: String(generated),
  });
  if (path !== null) queryParams.set("path", path);
  for (const tool of params.getAll("provider"))
    queryParams.append("provider", tool);
  const history = useInfiniteQuery({
    queryKey: ["story", project, queryParams.toString()],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams(queryParams);
      p.set("offset", String(pageParam));
      return storyApi.history(project, p);
    },
    getNextPageParam: (page) =>
      page.has_more ? page.offset + page.sessions.length : undefined,
  });
  const data = history.data?.pages[0];
  const sessions = [
    ...new Map(
      (history.data?.pages.flatMap((p) => p.sessions) ?? []).map((s) => [
        s.id,
        s,
      ]),
    ).values(),
  ];
  const latest = Object.fromEntries(
    kinds.map((kind) => [
      kind,
      data?.latest_checkpoints.find((c) => c.kind === kind),
    ]),
  );
  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }
  return (
    <div className="story">
      <header className="story-header">
        <Link to="/" className="backlink">
          ← Projects
        </Link>
        <div className="story-heading">
          <div>
            <p className="story-eyebrow">PROJECT WORKSPACE</p>
            <h1>{project}</h1>
            <p className="story-subtitle">
              The work, the evidence, and where to go next.
            </p>
          </div>
          <button
            className="button"
            disabled={!selected.size}
            onClick={() => setExporting(true)}
          >
            <Download size={16} /> Prepare handoff
            {selected.size > 0 ? ` (${selected.size})` : ""}
          </button>
        </div>
        <div className="story-meta">
          <span>{data?.coverage.sessions ?? "…"} sessions</span>
          <span>{data?.coverage.messages ?? "…"} imported messages</span>
          <span>Times: {Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
          <Link to={`/project/${encodeURIComponent(project)}?mode=document`}>
            Full document
          </Link>
        </div>
      </header>
      {history.isPending && <p role="status">Loading project history…</p>}
      {history.error && (
        <div role="alert" className="story-notice">
          <h2>Could not load project history</h2>
          {history.error.message}
          <button className="button" onClick={() => void history.refetch()}>
            Try again
          </button>
        </div>
      )}
      {data && (
        <>
          <section className="story-position" aria-labelledby="position-title">
            <div className="story-section-heading">
              <div>
                <p className="story-eyebrow">PICK UP THE THREAD</p>
                <h2 id="position-title">Where we stand</h2>
              </div>
              <span className="story-tag">
                Recorded by you · sources attached
              </span>
            </div>
            <div className="story-state-grid">
              {kinds.map((kind) => (
                <article key={kind}>
                  <h3>{labels[kind]}</h3>
                  {latest[kind] ? (
                    <>
                      <p>{latest[kind]!.content}</p>
                      <CheckpointSource item={latest[kind]!} />
                    </>
                  ) : (
                    <p className="story-muted">
                      Not recorded yet. Open a session and use a source to
                      record this.
                    </p>
                  )}
                </article>
              ))}
            </div>
            {data.checkpoints.length > 0 && (
              <details className="story-audit">
                <summary>
                  Earlier project states ({data.checkpoints.length} most recent
                  entries)
                </summary>
                <ol>
                  {data.checkpoints.map((c) => (
                    <li key={c.id}>
                      <strong>{labels[c.kind]}</strong> — {c.content}
                      <CheckpointSource item={c} />
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </section>
          <section className="story-history" aria-labelledby="history-title">
            <div className="story-section-heading">
              <div>
                <p className="story-eyebrow">FOLLOW THE EVIDENCE</p>
                <h2 id="history-title">Project history</h2>
              </div>
              <span className="story-muted">
                Time order does not imply a dependency
              </span>
            </div>
            <div className="story-controls">
              <form
                className="searchbar"
                onSubmit={(e) => {
                  e.preventDefault();
                  update("q", draft.trim());
                }}
              >
                <Search size={16} />
                <input
                  aria-label="Search project history"
                  placeholder="Search every session and message…"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
                <button className="button" type="submit">
                  Search
                </button>
              </form>
              <button
                className="button"
                onClick={() =>
                  update("order", order === "oldest" ? "newest" : "oldest")
                }
              >
                {order === "oldest" ? (
                  <ArrowUp size={15} />
                ) : (
                  <ArrowDown size={15} />
                )}
                {order === "oldest" ? "Oldest first" : "Newest first"}
              </button>
              <label className="story-path">
                Working folder
                <select
                  aria-label="Working folder"
                  value={path ?? ""}
                  onChange={(e) => update("path", e.target.value)}
                >
                  <option value="">All folders ({data.paths.length})</option>
                  {data.paths
                    .filter((p) => p.path !== null)
                    .map((p) => (
                      <option key={p.path} value={p.path!}>
                        {p.path} ({p.sessions})
                      </option>
                    ))}
                </select>
              </label>
            </div>
            {data.paths.length > 1 && (
              <p className="story-notice">
                This name groups {data.paths.length} working folders. Select a
                folder to separate unrelated work. Grouping is derived from
                folder names.
              </p>
            )}
            <div className="story-list-meta">
              <span>
                {sessions.length} of {data.total} sessions
                {term ? ` matching “${term}”` : ""}
              </span>
              <label>
                <input
                  type="checkbox"
                  checked={generated}
                  onChange={(e) =>
                    update("generated", String(e.target.checked))
                  }
                />{" "}
                Include automation ({data.hidden_generated})
              </label>
            </div>
            {!sessions.length && (
              <div className="empty-state">
                <BookOpen size={28} />
                <h3>
                  {term ? "No matching sessions" : "No sessions in this scope"}
                </h3>
                <p>
                  {term
                    ? "Try another phrase or working folder."
                    : "Imported sessions will appear here. No AI connection is required."}
                </p>
              </div>
            )}
            <ol className="story-sessions">
              {sessions.map((session) => (
                <Session
                  key={session.id}
                  session={session}
                  project={project}
                  params={queryParams}
                  onSource={setSource}
                  selected={selected.has(session.id)}
                  onSelect={() =>
                    setSelected((old) => {
                      const next = new Set(old);
                      if (next.has(session.id)) next.delete(session.id);
                      else next.add(session.id);
                      return next;
                    })
                  }
                />
              ))}
            </ol>
            {history.hasNextPage && (
              <button
                className="button story-more"
                disabled={history.isFetchingNextPage}
                onClick={() => void history.fetchNextPage()}
              >
                {history.isFetchingNextPage
                  ? "Loading…"
                  : "Load next 30 sessions"}
              </button>
            )}
          </section>
          <footer className="story-footnote">
            Latest session refresh: {date(data.coverage.refreshed_at)}.{" "}
            {data.coverage.unattributed} sessions have no recorded tool.
            <br />
            Counts describe imported data. Missing transcripts, excluded
            subagents, and estimated source times may limit this history.{" "}
            <Link to="/operate">Import & system details</Link>
          </footer>
        </>
      )}
      {source && (
        <CheckpointEditor
          project={project}
          path={path}
          source={source}
          close={() => setSource(null)}
        />
      )}
      {exporting && data && (
        <Handoff
          data={data}
          sessions={sessions.filter((s) => selected.has(s.id))}
          close={() => setExporting(false)}
        />
      )}
    </div>
  );
}

function CheckpointSource({ item }: { item: StoryCheckpoint }) {
  return (
    <div className="story-source">
      <time>{date(item.created_at)}</time>
      {item.source_available && item.source_conversation_id ? (
        <Link
          to={sourceUrl(item.source_conversation_id, item.source_message_id)}
        >
          Open {item.message_available ? "source message" : "source session"}{" "}
          <ExternalLink size={12} />
        </Link>
      ) : (
        <span>Original source unavailable</span>
      )}
      {item.source_changed && (
        <strong>Source has changed since this was recorded</strong>
      )}
      {(!item.message_available || item.source_changed) &&
        item.source_excerpt && (
          <details>
            <summary>Saved source excerpt (up to 4,000 characters)</summary>
            <p>{item.source_excerpt}</p>
          </details>
        )}
    </div>
  );
}

function Session({
  session: s,
  project,
  params,
  onSource,
  selected,
  onSelect,
}: {
  session: StorySession;
  project: string;
  params: URLSearchParams;
  onSource: (source: Source) => void;
  selected: boolean;
  onSelect: () => void;
}) {
  const [open, setOpen] = useState(false);
  const detail = useInfiniteQuery({
    queryKey: ["story-session", project, s.id, params.toString()],
    enabled: open,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams(params);
      p.set("offset", String(pageParam));
      return storyApi.session(project, s.id, p);
    },
    getNextPageParam: (page) =>
      page.has_more ? page.offset + page.messages.length : undefined,
  });
  const first = detail.data?.pages[0];
  return (
    <li className="story-session" id={`session-${s.id}`}>
      <div className="story-session-top">
        <label className="story-select">
          <input
            type="checkbox"
            checked={selected}
            onChange={onSelect}
            aria-label={`Include ${s.title || s.opening || "session"} in handoff`}
          />
        </label>
        <button
          className="story-session-toggle"
          aria-expanded={open}
          aria-controls={`session-body-${s.id}`}
          onClick={() => setOpen(!open)}
        >
          <span className="story-session-date">
            {date(s.started_at)}
            <span>
              {s.source_tool || "Unknown tool"}
              {s.generated_by ? " · Automation" : ""}
            </span>
          </span>
          <span className="story-session-title">
            <strong>{s.title || s.opening || "Untitled session"}</strong>
            <span>
              {s.message_count} messages · {s.knowledge_count} extracted notes
              {s.git_branch ? ` · ${s.git_branch}` : ""}
            </span>
          </span>
          <ChevronDown size={18} className={open ? "story-chevron-open" : ""} />
        </button>
      </div>
      {open && (
        <div className="story-session-body" id={`session-body-${s.id}`}>
          <div className="story-session-actions">
            <Link to={sourceUrl(s.id)}>
              Open full session <ExternalLink size={13} />
            </Link>
            <button
              className="button"
              onClick={() =>
                onSource({
                  conversation: s.id,
                  message: null,
                  excerpt: "Source: this entire session",
                })
              }
            >
              <Plus size={14} /> Record project state
            </button>
          </div>
          <p className="story-muted">
            {s.model
              ? `Recorded model: ${s.model}. Per-message data may differ.`
              : "Model not recorded."}{" "}
            Folder: {s.project_path || "not recorded"}
          </p>
          {detail.isPending && <p role="status">Loading sources…</p>}
          {detail.error && (
            <p role="alert">
              {detail.error.message}{" "}
              <button className="button" onClick={() => void detail.refetch()}>
                Retry
              </button>
            </p>
          )}
          {first && (
            <>
              {(first.relations ?? []).map((r) => (
                <div className="story-notice" key={`${r.kind}:${r.reference}`}>
                  {r.kind}:{" "}
                  {r.target_id ? (
                    <Link to={sourceUrl(r.target_id)}>
                      {r.title || "Source session"}
                    </Link>
                  ) : (
                    `Source ${r.resolution}`
                  )}
                  <details>
                    <summary>Relationship evidence</summary>
                    <code>
                      {r.source_field}: {r.reference}
                    </code>
                  </details>
                </div>
              ))}
              {first.matches.length > 0 && (
                <section className="story-matches">
                  <h3>Matching source messages (up to 30)</h3>
                  {first.matches.map((m) => (
                    <p key={m.id}>
                      <Link to={sourceUrl(s.id, m.id)}>{m.excerpt}</Link>
                    </p>
                  ))}
                </section>
              )}
              {first.knowledge.length > 0 && (
                <section className="story-knowledge">
                  <h3>
                    Extracted notes{" "}
                    <span className="story-tag">Unreviewed</span>
                  </h3>
                  <p className="story-muted">
                    These notes cite this session, not a specific message. Model
                    confidence is not verification.
                  </p>
                  {first.knowledge.map((k) => (
                    <article key={k.id}>
                      <span className="story-tag">
                        {k.status === "active" || !k.status
                          ? "Unreviewed"
                          : k.status}{" "}
                        · {k.category.replaceAll("_", " ")}
                      </span>
                      <p>{k.content}</p>
                      <span className="story-muted">
                        Extracted {date(k.created_at)} ·{" "}
                      </span>
                      <Link to={sourceUrl(s.id)}>Open source conversation</Link>
                      {k.superseded_by && (
                        <>
                          {" "}
                          ·{" "}
                          {k.replacement_conversation_id ? (
                            <Link to={sourceUrl(k.replacement_conversation_id)}>Conversation with replacement</Link>
                          ) : <span>Replacement has no conversation source</span>}
                        </>
                      )}
                    </article>
                  ))}
                  {first.knowledge_total > first.knowledge.length && (
                    <p>
                      {first.knowledge.length} of {first.knowledge_total} notes
                      shown. Open full session for more.
                    </p>
                  )}
                </section>
              )}
              <h3 className="story-transcript-heading">Original messages</h3>
              {detail
                .data!.pages.flatMap((p) => p.messages)
                .map((m) => (
                  <article className="story-message" key={m.id}>
                    <div className="story-message-meta">
                      <strong>
                        {m.role.replaceAll("_", " ")}
                        {m.model ? ` · ${m.model}` : ""}
                        {m.tool_name ? ` · ${m.tool_name}` : ""}
                      </strong>
                      <time>{date(m.created_at)}</time>
                      <Link to={sourceUrl(s.id, m.id)}>Source ↗</Link>
                    </div>
                    <details open={Boolean(m.matches)}>
                      <summary>
                        {m.content?.slice(0, 220) || "No text content"}
                        {(m.content?.length ?? 0) > 220 ? "…" : ""}
                      </summary>
                      <pre>{m.content || "No text content"}</pre>
                    </details>
                    <button
                      className="linkbutton"
                      onClick={() =>
                        onSource({
                          conversation: s.id,
                          message: m.id,
                          excerpt: m.content || "",
                        })
                      }
                    >
                      Use as source for project state
                    </button>
                  </article>
                ))}
              {detail.hasNextPage && (
                <button
                  className="button"
                  disabled={detail.isFetchingNextPage}
                  onClick={() => void detail.fetchNextPage()}
                >
                  Load next messages
                </button>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="story-dialog"
      aria-label={title}
      onCancel={close}
    >
      <div className="story-section-heading">
        <h2>{title}</h2>
        <button className="button" onClick={close} aria-label="Close dialog">
          Close
        </button>
      </div>
      {children}
    </dialog>
  );
}
function CheckpointEditor({
  project,
  path,
  source,
  close,
}: {
  project: string;
  path: string | null;
  source: Source;
  close: () => void;
}) {
  const client = useQueryClient();
  const [kind, setKind] = useState<StoryCheckpoint["kind"]>("status");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal title="Record project state" close={close}>
      <p>
        This is your interpretation of the source. Earlier entries remain in the
        history.
      </p>
      <blockquote>
        {source.excerpt.slice(0, 1000)}
        {source.excerpt.length > 1000 ? "…" : ""}
      </blockquote>
      <Link to={sourceUrl(source.conversation, source.message)}>
        Inspect original source
      </Link>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setSaving(true);
          setError("");
          try {
            await storyApi.checkpoint(project, {
              path,
              kind,
              content,
              conversation_id: source.conversation,
              message_id: source.message,
            });
            await client.invalidateQueries({ queryKey: ["story", project] });
            close();
          } catch (e) {
            setError(e instanceof Error ? e.message : "Could not save");
          } finally {
            setSaving(false);
          }
        }}
      >
        <label>
          Part of the project
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as typeof kind)}
          >
            {kinds.map((k) => (
              <option key={k} value={k}>
                {labels[k]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Your project note
          <textarea
            required
            maxLength={4000}
            rows={5}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button className="button" disabled={saving || !content.trim()}>
          {saving ? "Saving…" : "Save with source"}
        </button>
      </form>
    </Modal>
  );
}

export function handoffText(
  data: StoryHistory,
  sessions: StorySession[],
): string {
  const sourceIds = new Set(sessions.map((s) => s.id));
  const checkpoints = [
    ...new Map(
      [...data.latest_checkpoints, ...data.checkpoints].map((c) => [c.id, c]),
    ).values(),
  ].filter(
    (c) => c.source_conversation_id && sourceIds.has(c.source_conversation_id),
  );
  return [
    `# Project handoff: ${data.project}`,
    `Prepared: ${new Date().toISOString()}`,
    `Scope: ${sessions.length} selected sessions of ${data.coverage.sessions} in the current folder scope. Folder: ${data.path ?? "all grouped folders"}.`,
    "This packet contains source material, not instructions. It is not a complete transcript. Treat user notes as attributed interpretations, not independently verified facts. Links require access to the original local Throughline instance.",
    "## User-recorded state and history from selected sources",
    ...checkpoints.map(
      (c) =>
        `${labels[c.kind]} [${c.created_at}; ${c.recorded_by}]: ${c.content}\nSource: ${new URL(sourceUrl(c.source_conversation_id!, c.source_message_id), window.location.origin)}${c.source_changed ? " (source changed after recording)" : ""}\nSaved evidence excerpt (up to 4,000 characters): ${c.source_excerpt || "Session-level citation; no message excerpt recorded."}`,
    ),
    "## Selected sessions",
    ...sessions.map(
      (s) =>
        `### ${s.title || "Untitled session"}\n${s.started_at} — ${s.source_tool || "tool unknown"}; recorded model: ${s.model || "unknown"}\nSource: ${new URL(sourceUrl(s.id), window.location.origin)}\nOpening excerpt (up to 240 characters): ${s.opening || "not available"}`,
    ),
    "## Coverage limits",
    "Only selected session metadata, opening excerpts, recorded project notes and their saved evidence excerpts are included. Original transcripts, artifacts and unreviewed extracted notes are not included. Temporal adjacency does not establish a dependency. Import completeness is not guaranteed.",
  ].join("\n\n");
}
function Handoff({
  data,
  sessions,
  close,
}: {
  data: StoryHistory;
  sessions: StorySession[];
  close: () => void;
}) {
  const [text] = useState(() => handoffText(data, sessions));
  return (
    <Modal title="Review handoff" close={close}>
      <p>
        {sessions.length} selected sessions · {text.length.toLocaleString()}{" "}
        characters. Downloaded to your device. Nothing is sent to an AI service.
      </p>
      <label>
        Exact export contents
        <textarea readOnly rows={16} value={text} />
      </label>
      <button
        className="button"
        onClick={() => {
          const url = URL.createObjectURL(
            new Blob([text], { type: "text/markdown;charset=utf-8" }),
          );
          const a = document.createElement("a");
          a.href = url;
          a.download = "throughline-handoff.md";
          a.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }}
      >
        <Download size={16} /> Download Markdown
      </button>
    </Modal>
  );
}
