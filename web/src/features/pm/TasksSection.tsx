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
  const { t } = useLang();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");

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

      <form
        className="pm-launch"
        onSubmit={(e) => {
          e.preventDefault();
          if (formValid) launch.mutate();
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
            <button type="submit" className="button" disabled={!formValid || launch.isPending}>
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

      <details className="pm-register">
        <summary>{t.tasksSection.registerSummary}</summary>
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
      </details>

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
