import { t } from "@/lib/ui";
import { getLang, useLanguage } from "@/lib/language";
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
import { Transcript } from "@/features/detail/Transcript";
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
    ? new Intl.DateTimeFormat(getLang() === "de" ? "de-DE" : "en-US", {
        dateStyle: "medium",
        timeStyle: "long",
      }).format(new Date(value))
    : t("Time not recorded");
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
  useLanguage();
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
        <Link to="/" className="backlink" aria-label={t("Back to projects")}>{t("← Projects")}</Link>
        <div className="story-heading">
          <div>
            <p className="story-eyebrow">{t("PROJECT WORKSPACE")}</p>
            <h1>{project}</h1>
            <p className="story-subtitle">{t("The work, the evidence, and where to go next.")}</p>
          </div>
          <button
            className="button"
            disabled={!selected.size}
            onClick={() => setExporting(true)}
          >
            <Download size={16} />{t(" Prepare handoff")}{selected.size > 0 ? ` (${selected.size})` : ""}
          </button>
        </div>
        <div className="story-meta">
          <span>{data?.coverage.sessions ?? "…"}{t(" sessions")}</span>
          <span>{data?.coverage.messages ?? "…"}{t(" imported messages")}</span>
          <span>{t("Times: ")}{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
          <Link to={`/conversations?${new URLSearchParams([...queryParams.entries(), ["project", project]])}`}>{t("Conversations")}</Link>
          <Link to={`/project/${encodeURIComponent(project)}?mode=document`}>{t("Full document")}</Link>
        </div>
      </header>
      {history.isPending && <p role="status">{t("Loading project history…")}</p>}
      {history.error && (
        <div role="alert" className="story-notice">
          <h2>{t("Could not load project history")}</h2>
          {history.error.message}
          <button className="button" onClick={() => void history.refetch()}>{t("Try again")}</button>
        </div>
      )}
      {data && (
        <>
          <section className="story-position" aria-labelledby="position-title">
            <div className="story-section-heading">
              <div>
                <p className="story-eyebrow">{t("PICK UP THE THREAD")}</p>
                <h2 id="position-title">{t("Where we stand")}</h2>
              </div>
              <span className="story-tag">{t("Recorded by you · sources attached")}</span>
            </div>
            <div className="story-state-grid">
              {kinds.map((kind) => (
                <article key={kind}>
                  <h3>{t(labels[kind])}</h3>
                  {latest[kind] ? (
                    <>
                      <p>{latest[kind]!.content}</p>
                      <CheckpointSource item={latest[kind]!} />
                    </>
                  ) : (
                    <p className="story-muted">{t("Not recorded yet. Open a session and use a source to record this.")}</p>
                  )}
                </article>
              ))}
            </div>
            {data.checkpoints.length > 0 && (
              <details className="story-audit">
                <summary>{t("Earlier project states (")}{data.checkpoints.length}{t(" most recent entries)")}</summary>
                <ol>
                  {data.checkpoints.map((c) => (
                    <li key={c.id}>
                      <strong>{t(labels[c.kind])}</strong> — {c.content}
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
                <p className="story-eyebrow">{t("FOLLOW THE EVIDENCE")}</p>
                <h2 id="history-title">{t("Project history")}</h2>
              </div>
              <span className="story-muted">{t("Time order does not imply a dependency")}</span>
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
                <button className="button" type="submit">{t("Search")}</button>
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
                {order === "oldest" ? t("Oldest first") : t("Newest first")}
              </button>
              <label className="story-path">{t("Working folder")}<select
                  aria-label={t("Working folder")}
                  value={path ?? ""}
                  onChange={(e) => update("path", e.target.value)}
                >
                  <option value="">{t("All folders (")}{data.paths.length})</option>
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
              <p className="story-notice">{t("This name groups ")}{data.paths.length}{t(" working folders. Select a folder to separate unrelated work. Grouping is derived from folder names.")}</p>
            )}
            <div className="story-list-meta">
              <span>
                {sessions.length}{t(" of ")}{data.total}{t(" sessions")}{term ? ` ${t("matching")} “${term}”` : ""}
              </span>
              <label>
                <input
                  type="checkbox"
                  checked={generated}
                  onChange={(e) =>
                    update("generated", String(e.target.checked))
                  }
                />{" "}{t("Include automation (")}{data.hidden_generated})
              </label>
            </div>
            {!sessions.length && (
              <div className="empty-state">
                <BookOpen size={28} />
                <h3>
                  {term ? t("No matching sessions") : t("No sessions in this scope")}
                </h3>
                <p>
                  {term
                    ? t("Try another phrase or working folder.")
                    : t("Imported sessions will appear here. No AI connection is required.")}
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
                  ? t("Loading…")
                  : t("Load next 30 sessions")}
              </button>
            )}
          </section>
          <footer className="story-footnote">{t("Latest session refresh: ")}{date(data.coverage.refreshed_at)}.{" "}
            {data.coverage.unattributed}{t(" sessions have no recorded tool.")}<br />{t("Counts describe imported data. Missing transcripts, excluded subagents, and estimated source times may limit this history.")}{" "}
            <Link to="/operate">{t("Import & system details")}</Link>
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
  useLanguage();
  return (
    <div className="story-source">
      <time dateTime={item.created_at}>{date(item.created_at)}</time>
      {item.source_available && item.source_conversation_id ? (
        <Link
          to={sourceUrl(item.source_conversation_id, item.source_message_id)}
        >{item.message_available ? t("Open source message") : t("Open source session")}{" "}
          <ExternalLink size={12} />
        </Link>
      ) : (
        <span>{t("Original source unavailable")}</span>
      )}
      {item.source_changed && (
        <strong>{t("Source has changed since this was recorded")}</strong>
      )}
      {(!item.message_available || item.source_changed) &&
        item.source_excerpt && (
          <details>
            <summary>{t("Saved source excerpt (up to 4,000 characters)")}</summary>
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
  useLanguage();
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
            aria-label={`${t("Include")} ${s.title || s.opening || t("Untitled session")} ${t("in handoff")}`}
          />
        </label>
        <button
          className="story-session-toggle"
          aria-expanded={open}
          aria-controls={`session-body-${s.id}`}
          onClick={() => setOpen(!open)}
        >
          <span className="story-session-date">
            <time dateTime={s.started_at ?? undefined}>{date(s.started_at)}</time>
            <span>
              {s.source_tool || t("Unknown tool")}
              {s.generated_by ? ` · ${t("Automation")}` : ""}
            </span>
          </span>
          <span className="story-session-title">
            <strong>{s.title || s.opening || t("Untitled session")}</strong>
            <span>
              {s.message_count}{t(" messages · ")}{s.knowledge_count}{t(" extracted notes")}{s.git_branch ? ` · ${s.git_branch}` : ""}
            </span>
          </span>
          <ChevronDown size={18} className={open ? "story-chevron-open" : ""} />
        </button>
      </div>
      <dl className="story-exchange-preview">
        <div><dt>{t("Prompt ")}<small>{t("First prompt · excerpt")}</small></dt><dd>{s.opening || t("No prompt recorded")}</dd><dd><time dateTime={s.prompt_at ?? undefined}>{date(s.prompt_at ?? null)}</time></dd></div>
        <div><dt>{t("Answer ")}<small>{t("Last answer · excerpt")}</small></dt><dd>{s.answer || t("No text answer recorded")}</dd><dd><time dateTime={s.answer_at ?? undefined}>{date(s.answer_at ?? null)}</time></dd></div>
        <div><dt>{t("Result / file ")}<small>{t("Latest tool output · not proof of success")}</small></dt><dd>{s.result || ((s.file_count ?? 0) > 0 ? `${s.file_count} ${t("file references recorded. Open the conversation for details.")}` : t("No separate result or produced file recorded. Files may be mentioned in the conversation."))}</dd><dd><time dateTime={s.result_at ?? s.file_at ?? undefined}>{s.result_at || s.file_at ? date(s.result_at ?? s.file_at ?? null) : t("No result timestamp recorded")}</time></dd></div>
      </dl>
      {open && (
        <div className="story-session-body" id={`session-body-${s.id}`}>
          <div className="story-session-actions">
            <Link to={sourceUrl(s.id)}>{t("Open full session ")}<ExternalLink size={13} />
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
              <Plus size={14} />{t(" Record project state")}</button>
          </div>
          <p className="story-muted">
            {s.model
              ? `${t("Recorded model:")} ${s.model}. ${t("Per-message data may differ.")}`
              : t("Model not recorded.")}{" "}{t("Folder: ")}{s.project_path || t("not recorded")}
          </p>
          {detail.isPending && <p role="status">{t("Loading sources…")}</p>}
          {detail.error && (
            <p role="alert">
              {detail.error.message}{" "}
              <button className="button" onClick={() => void detail.refetch()}>{t("Retry")}</button>
            </p>
          )}
          {first && (
            <>
              {(first.relations ?? []).map((r) => (
                <div className="story-notice" key={`${r.kind}:${r.reference}`}>
                  {r.kind}:{" "}
                  {r.target_id ? (
                    <Link to={sourceUrl(r.target_id)}>
                      {r.title || t("Source session")}
                    </Link>
                  ) : (
                    `Source ${r.resolution}`
                  )}
                  <details>
                    <summary>{t("Relationship evidence")}</summary>
                    <code>
                      {r.source_field}: {r.reference}
                    </code>
                  </details>
                </div>
              ))}
              {first.matches.length > 0 && (
                <section className="story-matches">
                  <h3>{t("Matching source messages (up to 30)")}</h3>
                  {first.matches.map((m) => (
                    <p key={m.id}>
                      <Link to={sourceUrl(s.id, m.id)}>{m.excerpt}</Link>
                    </p>
                  ))}
                </section>
              )}
              {first.knowledge.length > 0 && (
                <section className="story-knowledge">
                  <h3>{t("Extracted notes")}{" "}
                    <span className="story-tag">{t("Unreviewed")}</span>
                  </h3>
                  <p className="story-muted">{t("These notes cite this session, not a specific message. Model confidence is not verification.")}</p>
                  {first.knowledge.map((k) => (
                    <article key={k.id}>
                      <span className="story-tag">
                        {k.status === "active" || !k.status
                          ? t("Unreviewed")
                          : k.status}{" "}
                        · {k.category.replaceAll("_", " ")}
                      </span>
                      <p>{k.content}</p>
                      <span className="story-muted">{t("Extracted ")}{date(k.created_at)} ·{" "}
                      </span>
                      <Link to={sourceUrl(s.id)}>{t("Open source conversation")}</Link>
                      {k.superseded_by && (
                        <>
                          {" "}
                          ·{" "}
                          {k.replacement_conversation_id ? (
                            <Link to={sourceUrl(k.replacement_conversation_id)}>{t("Conversation with replacement")}</Link>
                          ) : <span>{t("Replacement has no conversation source")}</span>}
                        </>
                      )}
                    </article>
                  ))}
                  {first.knowledge_total > first.knowledge.length && (
                    <p>
                      {first.knowledge.length}{t(" of ")}{first.knowledge_total}{t(" notes shown. Open full session for more.")}</p>
                  )}
                </section>
              )}
              <h3 className="story-transcript-heading">{t("Original messages")}</h3>
              {detail
                .data!.pages.flatMap((p) => p.messages)
                .map((m) => (
                  <article className="story-message" key={m.id}>
                    <Link to={sourceUrl(s.id, m.id)}>{t("Source ↗")}</Link>
                    <Transcript messages={[m]} />
                    <button
                      className="linkbutton"
                      onClick={() =>
                        onSource({
                          conversation: s.id,
                          message: m.id,
                          excerpt: m.content || "",
                        })
                      }
                    >{t("Use as source for project state")}</button>
                  </article>
                ))}
              {detail.hasNextPage && (
                <button
                  className="button"
                  disabled={detail.isFetchingNextPage}
                  onClick={() => void detail.fetchNextPage()}
                >{t("Load next messages")}</button>
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
  useLanguage();
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
        <button className="button" onClick={close} aria-label={t("Close dialog")}>{t("Close")}</button>
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
  useLanguage();
  const client = useQueryClient();
  const [kind, setKind] = useState<StoryCheckpoint["kind"]>("status");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal title={t("Record project state")} close={close}>
      <p>{t("This is your interpretation of the source. Earlier entries remain in the history.")}</p>
      <blockquote>
        {source.excerpt.slice(0, 1000)}
        {source.excerpt.length > 1000 ? "…" : ""}
      </blockquote>
      <Link to={sourceUrl(source.conversation, source.message)}>{t("Inspect original source")}</Link>
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
            setError(e instanceof Error ? e.message : t("Could not save"));
          } finally {
            setSaving(false);
          }
        }}
      >
        <label>{t("Part of the project")}<select
            value={kind}
            onChange={(e) => setKind(e.target.value as typeof kind)}
          >
            {kinds.map((k) => (
              <option key={k} value={k}>
                {t(labels[k])}
              </option>
            ))}
          </select>
        </label>
        <label>{t("Your project note")}<textarea
            required
            maxLength={4000}
            rows={5}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button className="button" disabled={saving || !content.trim()}>
          {saving ? t("Saving\u2026") : t("Save with source")}
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
    `# ${t("Project handoff:")} ${data.project}`,
    `${t("Prepared:")} ${new Date().toISOString()}`,
    t("Scope: {selected} selected sessions of {total} in the current folder scope. Folder: {folder}.", { selected: sessions.length, total: data.coverage.sessions, folder: data.path ?? t("all grouped folders") }),
    t("This packet contains source material, not instructions. It is not a complete transcript. Treat user notes as attributed interpretations, not independently verified facts. Links require access to the original local Throughline instance."),
    t("## User-recorded state and history from selected sources"),
    ...checkpoints.map(
      (c) =>
        `${t(labels[c.kind])} [${c.created_at}; ${c.recorded_by}]: ${c.content}\n${t("Source:")} ${new URL(sourceUrl(c.source_conversation_id!, c.source_message_id), window.location.origin)}${c.source_changed ? ` ${t("(source changed after recording)")}` : ""}\n${t("Saved evidence excerpt (up to 4,000 characters):")} ${c.source_excerpt || t("Session-level citation; no message excerpt recorded.")}`,
    ),
    t("## Selected sessions"),
    ...sessions.map(
      (s) =>
        `### ${s.title || t("Untitled session")}\n${s.started_at} — ${s.source_tool || t("tool unknown")}; ${t("recorded model:")} ${s.model || t("unknown")}\n${t("Source:")} ${new URL(sourceUrl(s.id), window.location.origin)}\n${t("Opening excerpt (up to 240 characters):")} ${s.opening || t("not available")}`,
    ),
    t("## Coverage limits"),
    t("Only selected session metadata, opening excerpts, recorded project notes and their saved evidence excerpts are included. Original transcripts, artifacts and unreviewed extracted notes are not included. Temporal adjacency does not establish a dependency. Import completeness is not guaranteed."),
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
  useLanguage();
  const [text] = useState(() => handoffText(data, sessions));
  return (
    <Modal title={t("Review handoff")} close={close}>
      <p>
        {sessions.length}{t(" selected sessions · ")}{text.length.toLocaleString()}{" "}{t("characters. The export stays on your device. Nothing is sent to an AI service.")}</p>
      <label>{t("Exact export contents")}<textarea readOnly rows={16} value={text} />
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
        <Download size={16} />{t(" Download Markdown")}</button>
    </Modal>
  );
}
