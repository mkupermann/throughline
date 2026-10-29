/** Tasks in the Projekt-Cockpit: labeled launch form, register accordion
 *  for adopting an existing pipeline.sh run, and the filterable task list. */

import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Play } from "lucide-react";

import { pmApi, type PmTask, type PmTaskStatus, type PmTeam } from "@/lib/api";
import { useLang } from "./i18n";
import {
  Disclosure,
  EmptyState,
  ErrorState,
  InlineConfirmButton,
  SkeletonRows,
  TASK_STATUSES,
  TERMINAL_STATUSES,
  TaskStatusChip,
  fmtInt,
  fmtRelative,
} from "./shared";

type Filter = PmTaskStatus | "all";

function TaskRow({ task, projectId }: { task: PmTask; projectId: number }) {
  const { t } = useLang();
  const queryClient = useQueryClient();
  // Deleting a running/pending task would either orphan a live process or
  // remove a task the watcher hasn't even had a chance to record anything
  // for — the affordance only ever shows once a task has actually finished.
  const canDelete = TERMINAL_STATUSES.includes(task.status);

  const del = useMutation({
    mutationFn: () => pmApi.deleteTask(task.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pm-project-tasks", projectId] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
    },
  });

  return (
    <li className="pm-task-row">
      <Link to={`/pm/tasks/${task.id}`} className="pm-task-row-title">
        {task.title}
      </Link>
      <TaskStatusChip status={task.status} />
      <span className="pm-task-row-tokens tabular">{fmtInt(task.tokens_used)} {t.common.tokens}</span>
      <span className="pm-task-row-time">
        {task.status === "running"
          ? t.tasksSection.startedAt(fmtRelative(task.started_at))
          : task.ended_at
            ? t.tasksSection.endedAt(fmtRelative(task.ended_at))
            : fmtRelative(task.started_at)}
      </span>
      {canDelete && (
        <InlineConfirmButton
          className="pm-linklike pm-linklike-danger"
          disabled={del.isPending}
          pending={del.isPending}
          title={t.tasksSection.deleteTitle}
          onConfirm={() => del.mutate()}
        >
          {t.tasksSection.deleteTask}
        </InlineConfirmButton>
      )}
      {del.isError && (
        <p className="pm-field-error" role="alert">
          {t.tasksSection.deleteFailed((del.error as Error).message)}
        </p>
      )}
    </li>
  );
}

export function TasksSection({
  projectId,
  teams,
  tasks,
}: {
  projectId: number;
  teams: PmTeam[];
  tasks: UseQueryResult<{ tasks: PmTask[] }>;
}) {
  const { t, lang } = useLang();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const de = lang === "de";
  const runtime = useQuery({
    queryKey: ["pm-execution-readiness"],
    queryFn: pmApi.executionReadiness,
    staleTime: 0,
  });
  const runtimeAvailable = runtime.data?.available === true && !runtime.isError && !runtime.isFetching;

  const [teamId, setTeamId] = useState<number | "">("");
  const [title, setTitle] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [runId, setRunId] = useState("");

  // Linked repo projects with a known repo_path let the form prefill the
  // Repo-Pfad input instead of the user having to remember/retype it — the
  // same query key as CockpitPage's RepoLinksSection, so react-query
  // dedupes the request once both have mounted.
  const linkedRepos = useQuery({
    queryKey: ["pm-project-repos", projectId],
    queryFn: () => pmApi.projectRepos(projectId),
  });
  const repoChoices = (linkedRepos.data?.repo_projects ?? []).filter(
    (r): r is typeof r & { repo_path: string } => r.repo_path !== null,
  );

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["pm-project-tasks", projectId] });
    queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
  };

  const launch = useMutation({
    mutationFn: () =>
      pmApi.launch({
        pm_project_id: projectId,
        team_id: teamId as number,
        title: title.trim(),
        repo_path: repoPath.trim(),
      }),
    onSuccess: () => {
      setTitle("");
      invalidate();
    },
  });

  const register = useMutation({
    mutationFn: () =>
      pmApi.register({
        pm_project_id: projectId,
        team_id: teamId as number,
        title: title.trim(),
        repo_path: repoPath.trim(),
        run_id: runId.trim(),
      }),
    onSuccess: () => {
      setTitle("");
      setRunId("");
      invalidate();
    },
  });

  const list = tasks.data?.tasks ?? [];
  const filtered = filter === "all" ? list : list.filter((tk) => tk.status === filter);
  const presentStatuses = TASK_STATUSES.filter((s) => list.some((tk) => tk.status === s));

  const formValid = teamId !== "" && title.trim() !== "" && repoPath.trim() !== "";

  return (
    <section className="pm-section" aria-labelledby="pm-tasks-h">
      <h2 id="pm-tasks-h" className="section-label">
        {t.tasksSection.h2}
      </h2>

      <section className="pm-launch pm-runtime" aria-labelledby="pm-runtime-h">
        <h3 id="pm-runtime-h">{de ? "Ausführung vorbereiten" : "Execution setup"}</h3>
        <p role="status">
          {runtime.isFetching
            ? (de ? "Abhängigkeiten werden geprüft…" : "Checking dependencies…")
            : runtime.isError
              ? (de ? "Abhängigkeiten konnten nicht geprüft werden. Start ist gesperrt." : "Could not check dependencies. Launch is blocked.")
              : runtime.data?.available
                ? (de ? "Bash und Pipeline sind verfügbar." : "Bash and pipeline are available.")
                : (de ? "Die Ausführungsumgebung ist noch nicht bereit." : "The execution runtime is not ready yet.")}
        </p>
        <Disclosure
          key={String(runtime.data?.available)}
          defaultOpen={runtime.data?.available !== true}
          summary={de ? "Abhängigkeiten und nächste Schritte" : "Dependencies and next steps"}
        >
          {runtime.data && <ul>
            {runtime.data.checks.map(check => <li key={check.id}>
              <strong>{check.id === "bash" ? "Bash" : "Pipeline"}</strong>: {check.available
                ? (de ? "verfügbar" : "available")
                : (de ? "fehlt oder ist nicht lesbar" : "missing or unreadable")}
              {!check.available && <p>
                {check.id === "bash"
                  ? (de ? "Installiere Bash auf dem Throughline-Server (unter Windows: Git Bash) und starte Throughline neu." : "Install Bash on the Throughline server (Git Bash on Windows), then restart Throughline.")
                  : (de ? "Stelle deine kompatible pipeline.sh auf dem Throughline-Server bereit. Setze AI_PIPELINE_SCRIPT_PATH auf die Datei und starte Throughline neu. In Docker muss die Datei im Container verfügbar sein." : "Provide your compatible pipeline.sh on the Throughline server. Set AI_PIPELINE_SCRIPT_PATH to that file and restart Throughline. In Docker, the file must be available inside the container.")}
              </p>}
              {check.path && <code style={{ overflowWrap: "anywhere" }}>{check.path}</code>}
            </li>)}
          </ul>}
          <p>
            {de ? "Diese Prüfung startet keine Agenten. Sie prüft keine CLI-Anmeldung oder Anbieter-Verbindung. Weise dem Team Mitglieder und Modelle zu und verwende einen Repository-Pfad, der auf dem Server verfügbar ist." : "This check does not start agents or verify CLI sign-in and provider connectivity. Assign team members and models, and use a repository path available on the server."}{" "}
            <Link to="/pm/members">{t.dashboard.catalogMembers}</Link> · <Link to="/pm/models">{t.dashboard.catalogModels}</Link>
          </p>
        </Disclosure>
        <button type="button" className="button pm-button-quiet" disabled={runtime.isFetching} onClick={() => runtime.refetch()}>
          {de ? "Erneut prüfen" : "Check again"}
        </button>
      </section>
      <form
        className="pm-launch"
        onSubmit={(e) => {
          e.preventDefault();
          if (formValid && runtimeAvailable && !launch.isPending) launch.mutate();
        }}
      >
        <div className="pm-launch-fields">
          <label className="pm-field">
            <span className="pm-label">{t.tasksSection.teamLabel}</span>
            <select
              className="pm-input"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : "")}
              required
            >
              <option value="">{t.tasksSection.teamPlaceholder}</option>
              {teams.map((tm) => (
                <option key={tm.id} value={tm.id}>
                  {tm.name}
                </option>
              ))}
            </select>
          </label>
          <label className="pm-field pm-field-grow">
            <span className="pm-label">{t.tasksSection.titleLabel}</span>
            <input
              className="pm-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t.tasksSection.titlePlaceholder}
              required
            />
          </label>
          {repoChoices.length > 0 && (
            <label className="pm-field">
              <span className="pm-label">{t.tasksSection.repoPick.label}</span>
              <select
                className="pm-input"
                value=""
                onChange={(e) => {
                  const picked = repoChoices.find((r) => String(r.id) === e.target.value);
                  if (picked) setRepoPath(picked.repo_path);
                }}
              >
                <option value="">{t.tasksSection.repoPick.placeholder}</option>
                {repoChoices.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="pm-field pm-field-grow">
            <span className="pm-label">{t.tasksSection.repoLabel}</span>
            <input
              className="pm-input"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder={t.tasksSection.repoPlaceholder}
              required
            />
          </label>
          <div className="pm-field pm-field-submit">
            <span className="pm-label" aria-hidden>
              &nbsp;
            </span>
            <button type="submit" className="button" disabled={!formValid || !runtimeAvailable || launch.isPending}>
              <Play size={14} aria-hidden />
              {launch.isPending ? t.tasksSection.launching : t.tasksSection.launch}
            </button>
          </div>
        </div>
        {launch.isError && (
          <p className="pm-field-error" role="alert">
            {t.tasksSection.launchFailed((launch.error as Error).message)}
          </p>
        )}
      </form>

      <Disclosure className="pm-register" summary={t.tasksSection.registerSummary}>
        <div className="pm-register-body">
          <p className="pm-register-hint">
            {t.tasksSection.registerHint} <code>.ai-pipeline/</code>).
          </p>
          <div className="pm-launch-fields">
            <label className="pm-field">
              <span className="pm-label">{t.tasksSection.runIdLabel}</span>
              <input
                className="pm-input"
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
                placeholder={t.tasksSection.runIdPlaceholder}
                // Matches the server-side rule in register_existing_run
                // (throughline/queries/pm.py): a single path component, no
                // "/" or "\". The ".." check isn't expressible cleanly as
                // part of this character-class pattern, so it stays a
                // server-side check plus the hint below rather than a
                // more elaborate regex here.
                pattern="[^/\\\\]+"
                title={t.tasksSection.runIdHint}
                aria-describedby="pm-run-id-hint"
              />
              <p id="pm-run-id-hint" className="pm-field-hint">
                {t.tasksSection.runIdHint}
              </p>
            </label>
            <div className="pm-field pm-field-submit">
              <span className="pm-label" aria-hidden>
                &nbsp;
              </span>
              <button
                type="button"
                className="button"
                disabled={!formValid || !runId.trim() || register.isPending}
                onClick={() => register.mutate()}
              >
                {register.isPending ? t.tasksSection.registering : t.tasksSection.registerSubmit}
              </button>
            </div>
          </div>
          {register.isError && (
            <p className="pm-field-error" role="alert">
              {t.tasksSection.registerFailed((register.error as Error).message)}
            </p>
          )}
        </div>
      </Disclosure>

      {tasks.isPending ? (
        <SkeletonRows n={3} />
      ) : tasks.error ? (
        <ErrorState
          title={t.tasksSection.errorTitle}
          error={tasks.error}
          onRetry={tasks.refetch}
        />
      ) : list.length === 0 ? (
        <EmptyState title={t.tasksSection.emptyTitle}>
          <p>{t.tasksSection.emptyBody}</p>
        </EmptyState>
      ) : (
        <>
          {presentStatuses.length > 1 && (
            <div className="pm-filter" role="group" aria-label={t.tasksSection.filterGroupLabel}>
              <button
                type="button"
                className={`pm-filter-chip${filter === "all" ? " is-active" : ""}`}
                aria-pressed={filter === "all"}
                onClick={() => setFilter("all")}
              >
                {t.tasksSection.filterAll(fmtInt(list.length))}
              </button>
              {presentStatuses.map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`pm-filter-chip${filter === s ? " is-active" : ""}`}
                  aria-pressed={filter === s}
                  onClick={() => setFilter(s)}
                >
                  {t.status.task[s]} ({fmtInt(list.filter((tk) => tk.status === s).length)})
                </button>
              ))}
            </div>
          )}
          <ul className="pm-task-list">
            {filtered.map((tk) => (
              <TaskRow key={tk.id} task={tk} projectId={projectId} />
            ))}
            {filtered.length === 0 && (
              <li className="pm-task-list-none">{t.tasksSection.noneWithStatus}</li>
            )}
          </ul>
        </>
      )}
    </section>
  );
}
