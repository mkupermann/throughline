// web/src/features/pm/TasksTab.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { pmApi } from "@/lib/api";

export function TasksTab({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [teamId, setTeamId] = useState<number | "">("");
  const [repoPath, setRepoPath] = useState("");

  const { data: teams } = useQuery({
    queryKey: ["pm-project-teams", projectId],
    queryFn: () => pmApi.projectTeams(projectId),
  });

  const { data: tasksData } = useQuery({
    queryKey: ["pm-project-tasks", projectId],
    queryFn: () => pmApi.projectTasks(projectId),
    // A launched/registered task starts "running", so a plain refetch on
    // this list (rather than a tight poll) is enough to notice new rows;
    // TaskDetailPage is where live per-task polling actually happens.
    refetchInterval: 8000,
  });

  const launch = useMutation({
    mutationFn: () =>
      pmApi.launch({ pm_project_id: projectId, team_id: teamId as number, title, repo_path: repoPath }),
    onSuccess: () => {
      setTitle("");
      queryClient.invalidateQueries({ queryKey: ["pm-project-tasks", projectId] });
    },
  });

  const [registerRunId, setRegisterRunId] = useState("");
  const register = useMutation({
    mutationFn: () =>
      pmApi.register({
        pm_project_id: projectId,
        team_id: teamId as number,
        title,
        repo_path: repoPath,
        run_id: registerRunId,
      }),
    onSuccess: () => {
      setTitle("");
      setRegisterRunId("");
      queryClient.invalidateQueries({ queryKey: ["pm-project-tasks", projectId] });
    },
  });

  return (
    <div className="pm-tasks-tab">
      <form
        className="pm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (teamId && title.trim() && repoPath.trim()) launch.mutate();
        }}
      >
        <select value={teamId} onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Team…</option>
          {(teams?.teams ?? []).map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <input placeholder="Aufgabenbeschreibung" value={title} onChange={(e) => setTitle(e.target.value)} />
        <input placeholder="Repo-Pfad" value={repoPath} onChange={(e) => setRepoPath(e.target.value)} />
        <button type="submit" disabled={launch.isPending}>Task starten</button>
      </form>

      <details className="pm-register">
        <summary>Bestehenden Lauf registrieren</summary>
        <div className="pm-form">
          <input placeholder="Run-ID (Ordnername)" value={registerRunId} onChange={(e) => setRegisterRunId(e.target.value)} />
          <button disabled={!teamId || !title.trim() || !repoPath.trim() || !registerRunId.trim()} onClick={() => register.mutate()}>
            Registrieren
          </button>
        </div>
      </details>

      <ul className="pm-list">
        {(tasksData?.tasks ?? []).map((t) => (
          <li key={t.id} className="pm-list-row">
            <Link to={`/pm/tasks/${t.id}`} className="pm-list-name">{t.title}</Link>
            <span className={`pm-status pm-status-${t.status}`}>{t.status}</span>
            <span className="pm-list-meta">{t.tokens_used.toLocaleString()} Tokens</span>
          </li>
        ))}
        {(tasksData?.tasks ?? []).length === 0 && <li className="pm-list-empty">Noch keine Tasks.</li>}
      </ul>
    </div>
  );
}
