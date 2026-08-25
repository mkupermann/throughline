/** Tasks in the Projekt-Cockpit: labeled launch form, register accordion
 *  for adopting an existing pipeline.sh run, and the filterable task list. */

import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Play } from "lucide-react";

import { pmApi, type PmTask, type PmTaskStatus, type PmTeam } from "@/lib/api";
import {
  EmptyState,
  ErrorState,
  SkeletonRows,
  TASK_STATUS_LABEL,
  TaskStatusChip,
  fmtInt,
  fmtRelative,
} from "./shared";

type Filter = PmTaskStatus | "all";

function TaskRow({ task }: { task: PmTask }) {
  return (
    <li className="pm-task-row">
      <Link to={`/pm/tasks/${task.id}`} className="pm-task-row-title">
        {task.title}
      </Link>
      <TaskStatusChip status={task.status} />
      <span className="pm-task-row-tokens tabular">{fmtInt(task.tokens_used)} Tokens</span>
      <span className="pm-task-row-time">
        {task.status === "running"
          ? `gestartet ${fmtRelative(task.started_at)}`
          : task.ended_at
            ? `beendet ${fmtRelative(task.ended_at)}`
            : fmtRelative(task.started_at)}
      </span>
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
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");

  const [teamId, setTeamId] = useState<number | "">("");
  const [title, setTitle] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [runId, setRunId] = useState("");

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
  const filtered = filter === "all" ? list : list.filter((t) => t.status === filter);
  const presentStatuses = (Object.keys(TASK_STATUS_LABEL) as PmTaskStatus[]).filter((s) =>
    list.some((t) => t.status === s),
  );

  const formValid = teamId !== "" && title.trim() !== "" && repoPath.trim() !== "";

  return (
    <section className="pm-section" aria-labelledby="pm-tasks-h">
      <h2 id="pm-tasks-h" className="section-label">
        Tasks
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
            <span className="pm-label">Team</span>
            <select
              className="pm-input"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : "")}
              required
            >
              <option value="">Team wählen…</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="pm-field pm-field-grow">
            <span className="pm-label">Titel</span>
            <input
              className="pm-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Was soll das Team bauen?"
              required
            />
          </label>
          <label className="pm-field pm-field-grow">
            <span className="pm-label">Repo-Pfad</span>
            <input
              className="pm-input"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder="C:\Pfad\zum\Repository"
              required
            />
          </label>
          <div className="pm-field pm-field-submit">
            <span className="pm-label" aria-hidden>
              &nbsp;
            </span>
            <button type="submit" className="button" disabled={!formValid || launch.isPending}>
              <Play size={14} aria-hidden />
              {launch.isPending ? "Startet…" : "Task starten"}
            </button>
          </div>
        </div>
        {launch.isError && (
          <p className="pm-field-error" role="alert">
            Start fehlgeschlagen: {(launch.error as Error).message}
          </p>
        )}
      </form>

      <details className="pm-register">
        <summary>Bestehenden Lauf registrieren</summary>
        <div className="pm-register-body">
          <p className="pm-register-hint">
            Übernimmt einen bereits laufenden pipeline.sh-Lauf. Team, Titel und Repo-Pfad oben
            ausfüllen, dazu die Run-ID (der Ordnername unter <code>.ai-pipeline/</code>).
          </p>
          <div className="pm-launch-fields">
            <label className="pm-field">
              <span className="pm-label">Run-ID</span>
              <input
                className="pm-input"
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
                placeholder="z. B. 20260825-184848"
              />
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
                {register.isPending ? "Registriert…" : "Lauf registrieren"}
              </button>
            </div>
          </div>
          {register.isError && (
            <p className="pm-field-error" role="alert">
              Registrieren fehlgeschlagen: {(register.error as Error).message}
            </p>
          )}
        </div>
      </details>

      {tasks.isPending ? (
        <SkeletonRows n={3} />
      ) : tasks.error ? (
        <ErrorState
          title="Tasks können nicht geladen werden"
          error={tasks.error}
          onRetry={tasks.refetch}
        />
      ) : list.length === 0 ? (
        <EmptyState title="Noch keine Tasks">
          <p>Oben ein Team wählen, Titel und Repo-Pfad angeben und den ersten Task starten.</p>
        </EmptyState>
      ) : (
        <>
          {presentStatuses.length > 1 && (
            <div className="pm-filter" role="group" aria-label="Nach Status filtern">
              <button
                type="button"
                className={`pm-filter-chip${filter === "all" ? " is-active" : ""}`}
                aria-pressed={filter === "all"}
                onClick={() => setFilter("all")}
              >
                Alle ({fmtInt(list.length)})
              </button>
              {presentStatuses.map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`pm-filter-chip${filter === s ? " is-active" : ""}`}
                  aria-pressed={filter === s}
                  onClick={() => setFilter(s)}
                >
                  {TASK_STATUS_LABEL[s]} ({fmtInt(list.filter((t) => t.status === s).length)})
                </button>
              ))}
            </div>
          )}
          <ul className="pm-task-list">
            {filtered.map((t) => (
              <TaskRow key={t.id} task={t} />
            ))}
            {filtered.length === 0 && (
              <li className="pm-task-list-none">Kein Task mit diesem Status.</li>
            )}
          </ul>
        </>
      )}
    </section>
  );
}
