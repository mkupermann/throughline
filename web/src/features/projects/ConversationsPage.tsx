import { OutputDisclosure } from "@/features/detail/OutputDisclosure";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { projectsApi, storyApi, type StorySession } from "@/lib/api";
import { getLang, useLanguage } from "@/lib/language";
import { t } from "@/lib/ui";
import { Transcript } from "@/features/detail/Transcript";
import "./conversations.css";

function validTime(value: string | null | undefined) {
  return value && Number.isFinite(Date.parse(value)) ? value : undefined;
}

function timestamp(value: string | null | undefined) {
  if (value && !validTime(value)) return `${t("Invalid recorded time")}: ${value}`;
  return value ? new Intl.DateTimeFormat(getLang() === "de" ? "de-DE" : "en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "longOffset", hour12: false,
  }).format(new Date(value)) : t("Time not recorded");
}

function Timestamp({value}: {value: string | null | undefined}) {
  return validTime(value) ? <time dateTime={value!}>{timestamp(value)}</time> : <span>{timestamp(value)}</span>;
}

export function ConversationsPage() {
  useLanguage();
  const [params, setParams] = useSearchParams();
  const project = params.get("project") ?? "";
  const providers = params.getAll("provider");
  const projects = useQuery({ queryKey: ["project-library", providers], queryFn: () => projectsApi.all(providers) });
  function chooseProject(value: string) {
    const next = new URLSearchParams();
    for (const provider of providers) next.append("provider", provider);
    if (value) next.set("project", value);
    setParams(next);
  }
  return <div className="conversation-workspace">
    <header className="conversation-heading">
      <div><h1>{t("Conversations")}</h1><p>{t("Choose a project, select a conversation, and read it in context.")}</p></div>
      <label>{t("Project")}<select aria-label={t("Project")} value={project} onChange={e => chooseProject(e.target.value)}>
        <option value="">{t("Choose a project…")}</option>
        {[...(projects.data?.projects ?? [])].sort((a, b) => a.project.localeCompare(b.project)).map(p => <option key={p.project} value={p.project}>{p.project} ({p.sessions})</option>)}
      </select></label>
    </header>
    {projects.isPending && <p role="status">{t("Loading projects…")}</p>}
    {projects.error && <p role="alert">{t("Could not load projects")} <button onClick={() => void projects.refetch()}>{t("Retry")}</button></p>}
    {project ? <ConversationBrowser key={`${project}:${params.get("q")}:${params.get("path")}:${params.get("generated")}:${providers.join(",")}`} project={project} /> :
      <div className="conversation-welcome"><h2>{t("Start with a project")}</h2><p>{t("Your conversations stay with their project. Choose one above to see its conversation list and open a single transcript.")}</p>
        <div className="conversation-projects">{[...(projects.data?.projects ?? [])].sort((a,b) => (b.last_active ?? "").localeCompare(a.last_active ?? "")).slice(0,6).map(p => <button key={p.project} onClick={() => chooseProject(p.project)}><strong>{p.project}</strong><span>{p.sessions} {t("Conversations")}</span></button>)}</div>
      </div>}
  </div>;
}

function ConversationBrowser({ project }: { project: string }) {
  useLanguage();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const scope = new URLSearchParams(params);
  scope.delete("conversation"); scope.delete("project"); scope.set("order", "newest");
  const history = useInfiniteQuery({
    queryKey: ["conversation-browser", project, scope.toString()], initialPageParam: 0,
    queryFn: ({ pageParam }) => { const q = new URLSearchParams(scope); q.set("offset", String(pageParam)); return storyApi.history(project, q); },
    getNextPageParam: page => page.has_more ? page.offset + page.sessions.length : undefined,
  });
  const first = history.data?.pages[0];
  const sessions = history.data?.pages.flatMap(p => p.sessions) ?? [];
  const requested = Number(params.get("conversation"));
  const id = Number.isSafeInteger(requested) && requested > 0 ? requested : sessions[0]?.id;
  const selected = sessions.find(s => s.id === id);
  function changeScope(key: string, value: string) {
    const next = new URLSearchParams(params); next.delete("conversation");
    if (value) next.set(key,value); else next.delete(key);
    setParams(next);
  }
  return <>
    <div className="conversation-project-context"><Link to={`/project/${encodeURIComponent(project)}?${scope}`}>{t("Project history")} · {project}</Link><span>{t("Times:")} {Intl.DateTimeFormat().resolvedOptions().timeZone}</span></div>
    <div className="conversation-layout">
      <section className="conversation-list" aria-label={t("Conversation list")}><h2 className="conversation-list-project">{project}</h2>
        <form onSubmit={e => { e.preventDefault(); changeScope("q", search.trim()); }} className="conversation-search">
          <input aria-label={t("Search conversations")} placeholder={t("Search conversations")} value={search} onChange={e => setSearch(e.target.value)} maxLength={200} />
          <button className="button" type="submit">{t("Search")}</button>
        </form>
        {!!first && <div className="conversation-list-options">
          <p>{first.total} {t("Conversations")} · {t("Newest first")}</p>
          {first.paths.length > 1 && <select aria-label={t("Working folder")} value={params.get("path") ?? ""} onChange={e => changeScope("path", e.target.value)}><option value="">{t("All working folders")}</option>{first.paths.filter(p => p.path !== null).map(p => <option key={p.path} value={p.path!}>{p.path} ({p.sessions})</option>)}</select>}
          <label><input type="checkbox" checked={params.get("generated") === "true"} onChange={e => changeScope("generated", String(e.target.checked))} /> {t("Include automation")} ({first.hidden_generated})</label>
        </div>}
        {history.isPending && <p role="status">{t("Loading project history…")}</p>}
        {history.error && <p role="alert">{t("Could not load project history")} <button onClick={() => void history.refetch()}>{t("Retry")}</button></p>}
        {first && !sessions.length && <p className="conversation-empty">{first.hidden_generated ? t("Only automated conversations are hidden. Include automation to inspect them.") : t("No matching sessions")} {t("Try another phrase or working folder.")}</p>}
        <ol>{sessions.map(s => <li key={s.id}><button className="conversation-choice" title={s.title || s.opening || t("Untitled session")} aria-pressed={id === s.id} onClick={() => { const next = new URLSearchParams(params); next.set("conversation",String(s.id)); setParams(next); window.scrollTo({top:0,behavior:"auto"}); }}>
          <strong>{s.title || s.opening || t("Untitled session")}</strong>
          <span>{s.source_tool || t("Unknown tool")} · {s.message_count} {t("messages")}</span>
          <Timestamp value={s.started_at} />
        </button></li>)}</ol>
        {history.hasNextPage && <button className="button" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? t("Loading…") : t("Load next 30 sessions")}</button>}
      </section>
      {id ? <ConversationReader key={`${id}:${scope.toString()}`} project={project} id={id} session={selected} scope={scope} /> : <div className="conversation-reader conversation-empty">{first && !first.total ? t("No conversations available in this scope.") : t("Select a conversation to read its prompt, answer and recorded results.")}</div>}
    </div>
  </>;
}

function ConversationReader({ project, id, session, scope }: {project: string; id: number; session?: StorySession; scope: URLSearchParams}) {
  useLanguage();
  const detail = useInfiniteQuery({ queryKey: ["conversation-reader", project, id, scope.toString()], initialPageParam: 0,
    queryFn: ({pageParam}) => { const q = new URLSearchParams(scope); q.set("offset", String(pageParam)); return storyApi.session(project,id,q); },
    getNextPageParam: page => page.has_more ? page.offset + page.messages.length : undefined,
  });
  return <section className="conversation-reader" aria-label={t("Selected conversation")}>
    <header><p>{project}</p><h2>{session?.title || t("Conversation") + ` #${id}`}</h2><Link to={`/c/${id}`}>{t("Open source conversation")}</Link></header>
    {session && <dl className="conversation-summary">
      <div><dt>{t("Prompt")} <small>{t("First prompt · excerpt")}</small></dt><dd>{session.opening || t("No prompt recorded")}</dd><dd><Timestamp value={session.prompt_at} /></dd></div>
      <div><dt>{t("Answer")} <small>{t("Last answer · excerpt")}</small></dt><dd>{session.answer || t("No text answer recorded")}</dd><dd><Timestamp value={session.answer_at} /></dd></div>
      <div><dt>{t("Result / file")} <small>{session.result_at ? t("Latest tool output · not proof of success") : session.file_at ? t("File-reference message · availability not verified") : t("No result timestamp recorded")}</small></dt><dd>{session.result ? <OutputDisclosure key={session.id} body={session.result} /> : (session.file_count ? t("{count} file references recorded.", {count:session.file_count}) : t("No separate result or produced file recorded. Files may be mentioned in the conversation."))}</dd><dd><Timestamp value={session.result_at ?? session.file_at} /></dd></div>
    </dl>}
    {!!detail.data?.pages[0].matches?.length && <details open className="conversation-notes"><summary>{t("Matching source messages (up to 30)")}</summary>{detail.data.pages[0].matches.map(match => <p key={match.id}><Link to={`/c/${id}#m${match.id}`}>{match.excerpt}</Link></p>)}</details>}
    <h3>{t("Original messages")}</h3>
    {detail.isPending && <p role="status">{t("Loading sources…")}</p>}
    {detail.error && <p role="alert">{t("Could not load conversation")} <button onClick={() => void detail.refetch()}>{t("Retry")}</button></p>}
    {detail.data && <><p className="conversation-transcript-note">{t("Messages are shown in their original order. Tool output stays inside this conversation.")}</p><Transcript messages={detail.data.pages.flatMap(p => p.messages)} />{!detail.data.pages[0].total && <p>{t("No transcript messages yet.")}</p>}</>}
    {!!detail.data?.pages[0].knowledge_total && <details className="conversation-notes"><summary>{t("Extracted notes")} ({detail.data.pages[0].knowledge_total})</summary><p>{t("These notes cite this session, not a specific message. Model confidence is not verification.")}</p><p>{t("Showing {shown} of {total} extracted notes.", {shown:detail.data.pages[0].knowledge.length,total:detail.data.pages[0].knowledge_total})}</p>{detail.data.pages[0].knowledge.map(note => <article key={note.id}><p>{note.content}</p><small>{note.category} · {note.superseded_by ? t("Superseded") : note.status ?? t("Unreviewed")}</small></article>)}</details>}
    {detail.hasNextPage && <button className="button" disabled={detail.isFetchingNextPage} onClick={() => void detail.fetchNextPage()}>{detail.isFetchingNextPage ? t("Loading…") : t("Load next messages")}</button>}
  </section>;
}
