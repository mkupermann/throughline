/** /pm/projects/:id — Projekt-Cockpit: header with editable status/budget,
 *  team pipeline rows (the signature element; seats double as the
 *  assignment UI) and tasks. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { pmApi, type PmProject } from "@/lib/api";
import { useLang } from "./i18n";
import {
  BudgetBar,
  ErrorState,
  InlineConfirmButton,
  PmHeaderBar,
  ProjectStatusChip,
  SkeletonRows,
  fmtInt,
} from "./shared";
import { TeamsSection } from "./TeamsSection";
import { TasksSection } from "./TasksSection";
import "@/styles/pm.css";

/** Click-to-edit token budget, shared by the project header and team rows. */
export function InlineBudget({
  value,
  onSave,
  saving,
  label,
}: {
  value: number | null;
  onSave: (budget: number | null) => void;
  saving: boolean;
  label?: string;
}) {
  const { t } = useLang();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const shownLabel = label ?? t.budget.label;

  if (!editing) {
    return (
      <span className="pm-inline-budget">
        <span className="pm-inline-budget-value tabular">
          {shownLabel}: {value === null ? t.common.unlimited : `${fmtInt(value)} ${t.common.tokens}`}
        </span>
        <button
          type="button"
          className="pm-linklike"
          onClick={() => {
            setDraft(value === null ? "" : String(value));
            setEditing(true);
          }}
        >
          {t.common.edit}
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
        className="pm-input pm-budget-input"
        type="number"
        min={0}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={t.common.unlimited}
        aria-label={`${shownLabel} ${t.cockpit.budgetAriaSuffix}`}
        autoFocus
      />
      <button type="submit" className="button pm-button-flush" disabled={saving}>
        {t.common.save}
      </button>
      <button
        type="button"
        className="button pm-button-flush pm-button-quiet"
        onClick={() => setEditing(false)}
      >
        {t.common.cancel}
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
  const { t } = useLang();
  const statuses: PmProject["status"][] = ["active", "paused", "completed", "archived"];
  return (
    <label className="pm-status-select">
      <span>{t.cockpit.statusLabel}</span>
      <select
        className="pm-input pm-input-compact"
        value={project.status}
        disabled={saving}
        onChange={(e) => onChange(e.target.value as PmProject["status"])}
      >
        {statuses.map((s) => (
          <option key={s} value={s}>
            {t.status.project[s]}
          </option>
        ))}
      </select>
    </label>
  );
}

export function CockpitPage() {
  const { t } = useLang();
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

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

  const deleteProject = useMutation({
    mutationFn: () => pmApi.deleteProject(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pm-projects"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
      navigate("/pm");
    },
  });

  const project = projects.data?.projects.find((p) => p.id === projectId);

  if (projects.isPending) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.cockpit.breadcrumbFallback }]} />
        </header>
        <SkeletonRows n={4} header />
      </section>
    );
  }

  if (projects.error) {
    return (
      <section className="pm-page">
        <header className="page-header">
          <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.cockpit.breadcrumbFallback }]} />
        </header>
        <ErrorState
          title={t.cockpit.errorTitle}
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
          <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.cockpit.breadcrumbFallback }]} />
          <h1 className="page-title">{t.cockpit.notFoundTitle}</h1>
          <p className="page-subtitle">{t.cockpit.notFoundBody}</p>
        </header>
      </section>
    );
  }

  const taskList = tasks.data?.tasks ?? [];
  const running = taskList.filter((tk) => tk.status === "running").length;
  const tokensUsed = taskList.reduce((sum, tk) => sum + tk.tokens_used, 0);
  const teamCount = teams.data?.teams.length ?? 0;

  return (
    <section className="pm-page">
      <header className="page-header">
        <PmHeaderBar
          items={[{ label: t.common.projectManagement, to: "/pm" }, { label: project.name }]}
        />
        <div className="page-header-row pm-header-row">
          <div className="pm-cockpit-title">
            <h1 className="page-title">{project.name}</h1>
            <ProjectStatusChip status={project.status} />
          </div>
          <InlineConfirmButton
            className="button pm-button-danger"
            disabled={deleteProject.isPending}
            pending={deleteProject.isPending}
            onConfirm={() => deleteProject.mutate()}
          >
            {deleteProject.isPending ? t.cockpit.deleting : t.cockpit.deleteProject}
          </InlineConfirmButton>
        </div>
        {deleteProject.isError && (
          <p className="pm-field-error" role="alert">
            {t.cockpit.deleteFailed((deleteProject.error as Error).message)}
          </p>
        )}
        <div className="pm-cockpit-meta">
          <StatusSelect
            project={project}
            saving={patchProject.isPending}
            onChange={(status) => patchProject.mutate({ status })}
          />
          <span aria-hidden>·</span>
          <span className="tabular">
            {fmtInt(teamCount)} {teamCount === 1 ? t.cockpit.teamOne : t.cockpit.teamMany}
          </span>
          <span aria-hidden>·</span>
          <span className="tabular">
            {fmtInt(taskList.length)} {taskList.length === 1 ? t.cockpit.taskOne : t.cockpit.taskMany}
            {running > 0 && `, ${fmtInt(running)} ${t.status.task.running}`}
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

      <TasksSection projectId={projectId} teams={teams.data?.teams ?? []} tasks={tasks} />
    </section>
  );
}
