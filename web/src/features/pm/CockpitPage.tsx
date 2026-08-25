/** /pm/projects/:id — Projekt-Cockpit: header with editable status/budget,
 *  team pipeline rows (the signature element), Zuordnungs-Matrix and tasks. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { pmApi, type PmProject } from "@/lib/api";
import {
  Breadcrumbs,
  BudgetBar,
  ErrorState,
  PROJECT_STATUS_LABEL,
  ProjectStatusChip,
  SkeletonRows,
  fmtInt,
} from "./shared";
import { TeamsSection } from "./TeamsSection";
import { MatrixSection } from "./MatrixSection";
import { TasksSection } from "./TasksSection";
import "@/styles/pm.css";

/** Click-to-edit token budget, shared by the project header and team rows. */
export function InlineBudget({
  value,
  onSave,
  saving,
  label = "Budget",
}: {
  value: number | null;
  onSave: (budget: number | null) => void;
  saving: boolean;
  label?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  if (!editing) {
    return (
      <span className="pm-inline-budget">
        <span className="pm-inline-budget-value tabular">
          {label}: {value === null ? "unbegrenzt" : `${fmtInt(value)} Tokens`}
        </span>
        <button
          type="button"
          className="pm-linklike"
          onClick={() => {
            setDraft(value === null ? "" : String(value));
            setEditing(true);
          }}
        >
          Bearbeiten
        </button>
      </span>
    );
  }

  return (
    <form
      className="pm-inline-budget pm-inline-budget-editing"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft.trim() === "" ? null : Number(draft));
        setEditing(false);
      }}
    >
      <input
        className="pm-input pm-input-number"
        type="number"
        min={0}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="unbegrenzt"
        aria-label={`${label} in Tokens`}
        autoFocus
      />
      <button type="submit" className="button pm-button-flush" disabled={saving}>
        Speichern
      </button>
      <button
        type="button"
        className="button pm-button-flush pm-button-quiet"
        onClick={() => setEditing(false)}
      >
        Abbrechen
      </button>
    </form>
  );
}

function StatusSelect({
  project,
  onChange,
  saving,
}: {
  project: PmProject;
  onChange: (status: PmProject["status"]) => void;
  saving: boolean;
}) {
  return (
    <label className="pm-status-select">
      <span>Status:</span>
      <select
        className="pm-input pm-input-compact"
        value={project.status}
        disabled={saving}
        onChange={(e) => onChange(e.target.value as PmProject["status"])}
      >
        {(Object.keys(PROJECT_STATUS_LABEL) as PmProject["status"][]).map((s) => (
          <option key={s} value={s}>
            {PROJECT_STATUS_LABEL[s]}
          </option>
        ))}
      </select>
    </label>
  );
}

export function CockpitPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const queryClient = useQueryClient();

  const projects = useQuery({ queryKey: ["pm-projects"], queryFn: pmApi.listProjects });
  const teams = useQuery({
    queryKey: ["pm-project-teams", projectId],
    queryFn: () => pmApi.projectTeams(projectId),
  });
  const tasks = useQuery({
    queryKey: ["pm-project-tasks", projectId],
    queryFn: () => pmApi.projectTasks(projectId),
    // New launches/registrations start "running"; an ambient refresh notices
    // them. Tight per-task polling lives on the drill-down page.
    refetchInterval: 8000,
  });
  const assignments = useQuery({
    queryKey: ["pm-assignments", projectId],
    queryFn: () => pmApi.listAssignments(projectId),
  });
  const members = useQuery({ queryKey: ["pm-members"], queryFn: pmApi.listMembers });

  const patchProject = useMutation({
    mutationFn: (body: Partial<PmProject>) => pmApi.patchProject(projectId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pm-projects"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
    },
  });

  const project = projects.data?.projects.find((p) => p.id === projectId);

  if (projects.isPending) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <Breadcrumbs items={[{ label: "Project Management", to: "/pm" }, { label: "Projekt" }]} />
        </header>
        <SkeletonRows n={4} header />
      </section>
    );
  }

  if (projects.error) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <Breadcrumbs items={[{ label: "Project Management", to: "/pm" }, { label: "Projekt" }]} />
        </header>
        <ErrorState
          title="Projekt kann nicht geladen werden"
          error={projects.error}
          onRetry={projects.refetch}
        />
      </section>
    );
  }

  if (!project) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <Breadcrumbs items={[{ label: "Project Management", to: "/pm" }, { label: "Projekt" }]} />
          <h1 className="page-title">Projekt nicht gefunden</h1>
          <p className="page-subtitle">
            Unter dieser Adresse liegt kein Projekt. Zurück zur Übersicht, um eines auszuwählen.
          </p>
        </header>
      </section>
    );
  }

  const taskList = tasks.data?.tasks ?? [];
  const running = taskList.filter((t) => t.status === "running").length;
  const tokensUsed = taskList.reduce((sum, t) => sum + t.tokens_used, 0);

  return (
    <section className="pm-page">
      <header className="page-header">
        <Breadcrumbs
          items={[{ label: "Project Management", to: "/pm" }, { label: project.name }]}
        />
        <div className="page-header-row pm-header-row">
          <div className="pm-cockpit-title">
            <h1 className="page-title">{project.name}</h1>
            <ProjectStatusChip status={project.status} />
          </div>
        </div>
        <div className="pm-cockpit-meta">
          <StatusSelect
            project={project}
            saving={patchProject.isPending}
            onChange={(status) => patchProject.mutate({ status })}
          />
          <span aria-hidden>·</span>
          <span className="tabular">
            {fmtInt(teams.data?.teams.length ?? 0)}{" "}
            {(teams.data?.teams.length ?? 0) === 1 ? "Team" : "Teams"}
          </span>
          <span aria-hidden>·</span>
          <span className="tabular">
            {fmtInt(taskList.length)} {taskList.length === 1 ? "Task" : "Tasks"}
            {running > 0 && `, ${fmtInt(running)} läuft`}
          </span>
          <span aria-hidden>·</span>
          <InlineBudget
            value={project.token_budget}
            saving={patchProject.isPending}
            onSave={(token_budget) => patchProject.mutate({ token_budget })}
          />
        </div>
        {project.token_budget !== null && (
          <div className="pm-cockpit-budgetbar">
            <BudgetBar used={tokensUsed} budget={project.token_budget} />
          </div>
        )}
      </header>

      <TeamsSection
        projectId={projectId}
        teams={teams}
        assignments={assignments}
        members={members}
        tasks={taskList}
      />

      <MatrixSection
        projectId={projectId}
        teams={teams}
        assignments={assignments}
        members={members}
      />

      <TasksSection projectId={projectId} teams={teams.data?.teams ?? []} tasks={tasks} />
    </section>
  );
}
