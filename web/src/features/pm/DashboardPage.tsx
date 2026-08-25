/** /pm — Projekt-Dashboard: every PM project as a card with real aggregates
 *  (GET /pm/overview), plus the catalog entry points and project creation. */

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Users, IdCard, Boxes } from "lucide-react";

import { pmApi, type PmOverviewProject } from "@/lib/api";
import {
  BudgetBar,
  Breadcrumbs,
  EmptyState,
  ErrorState,
  ProjectStatusChip,
  SkeletonRows,
  TASK_STATUS_LABEL,
  fmtInt,
  fmtRelative,
} from "./shared";
import "@/styles/pm.css";

function TaskCountChips({ tasks }: { tasks: PmOverviewProject["tasks"] }) {
  const entries = (Object.keys(TASK_STATUS_LABEL) as (keyof typeof TASK_STATUS_LABEL)[])
    .filter((s) => tasks[s] > 0);
  if (entries.length === 0) {
    return <span className="pm-card-quiet">Noch keine Tasks</span>;
  }
  return (
    <div className="pm-card-tasks">
      {entries.map((s) => (
        <span key={s} className={`pm-status pm-status-${s}`}>
          {fmtInt(tasks[s])} {TASK_STATUS_LABEL[s]}
        </span>
      ))}
    </div>
  );
}

function ProjectCard({ p }: { p: PmOverviewProject }) {
  return (
    <li>
      <Link to={`/pm/projects/${p.id}`} className="pm-card">
        <div className="pm-card-head">
          <span className="pm-card-name">{p.name}</span>
          <ProjectStatusChip status={p.status} />
        </div>
        <div className="pm-card-meta">
          <span>{fmtInt(p.teams)} {p.teams === 1 ? "Team" : "Teams"}</span>
          <span aria-hidden>·</span>
          <span>Aktivität {fmtRelative(p.last_activity)}</span>
        </div>
        <TaskCountChips tasks={p.tasks} />
        <BudgetBar used={p.tokens_used} budget={p.token_budget} />
      </Link>
    </li>
  );
}

function CreateProjectForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);

  const create = useMutation({
    mutationFn: () =>
      pmApi.createProject({
        name: name.trim(),
        token_budget: budget.trim() ? Number(budget) : null,
      }),
    onSuccess: () => {
      setName("");
      setBudget("");
      setOpen(false);
      onCreated();
    },
  });

  if (!open) {
    return (
      <button
        type="button"
        className="button pm-button-flush"
        onClick={() => {
          setOpen(true);
          // Focus lands in the field the moment the form exists.
          requestAnimationFrame(() => nameRef.current?.focus());
        }}
      >
        <Plus size={14} aria-hidden />
        Projekt anlegen
      </button>
    );
  }

  return (
    <form
      className="pm-inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) create.mutate();
      }}
    >
      <label className="pm-field pm-field-inline">
        <span className="pm-label">Name</span>
        <input
          ref={nameRef}
          className="pm-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="z. B. Demoscene Tribute"
          required
        />
      </label>
      <label className="pm-field pm-field-inline">
        <span className="pm-label">Token-Budget (optional)</span>
        <input
          className="pm-input pm-input-number"
          type="number"
          min={0}
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          placeholder="unbegrenzt"
        />
      </label>
      <div className="pm-inline-form-actions">
        <button type="submit" className="button" disabled={create.isPending || !name.trim()}>
          Anlegen
        </button>
        <button type="button" className="button pm-button-quiet" onClick={() => setOpen(false)}>
          Abbrechen
        </button>
      </div>
      {create.isError && (
        <p className="pm-field-error" role="alert">
          Projekt konnte nicht angelegt werden: {(create.error as Error).message}
        </p>
      )}
    </form>
  );
}

export function DashboardPage() {
  const queryClient = useQueryClient();
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-overview"],
    queryFn: pmApi.overview,
    // Running tasks move these numbers; a slow ambient refresh keeps the
    // dashboard honest without per-card polling.
    refetchInterval: 15_000,
  });

  const header = (
    <header className="page-header">
      <Breadcrumbs items={[{ label: "Project Management" }]} />
      <div className="page-header-row pm-header-row">
        <div>
          <h1 className="page-title">Project Management</h1>
          <p className="page-subtitle">Virtuelle Teams, Pipelines und Budgets im Blick.</p>
        </div>
        <CreateProjectForm
          onCreated={() => queryClient.invalidateQueries({ queryKey: ["pm-overview"] })}
        />
      </div>
    </header>
  );

  if (isPending) {
    return (
      <section className="pm-page">
        {header}
        <SkeletonRows n={3} />
      </section>
    );
  }

  if (error) {
    return (
      <section className="pm-page">
        {header}
        <ErrorState title="Übersicht kann nicht geladen werden" error={error} onRetry={refetch} />
      </section>
    );
  }

  const { projects, counts } = data;

  return (
    <section className="pm-page">
      {header}

      <div className="pm-catalog-links" role="group" aria-label="Kataloge">
        <Link to="/pm/roles" className="pm-catalog-link">
          <IdCard size={15} aria-hidden />
          <span>Rollen</span>
          <span className="tabular pm-catalog-count">{fmtInt(counts.roles)}</span>
        </Link>
        <Link to="/pm/members" className="pm-catalog-link">
          <Users size={15} aria-hidden />
          <span>Mitglieder</span>
          <span className="tabular pm-catalog-count">{fmtInt(counts.members)}</span>
        </Link>
        <span className="pm-catalog-link pm-catalog-link-static" title="Teams werden im Projekt-Cockpit verwaltet">
          <Boxes size={15} aria-hidden />
          <span>Teams</span>
          <span className="tabular pm-catalog-count">{fmtInt(counts.teams)}</span>
        </span>
      </div>

      {projects.length === 0 ? (
        <EmptyState title="Noch keine Projekte">
          <p>Ein Projekt bündelt Teams, Tasks und Budgets. Lege das erste an, um zu starten.</p>
        </EmptyState>
      ) : (
        <ul className="pm-card-grid">
          {projects.map((p) => (
            <ProjectCard key={p.id} p={p} />
          ))}
        </ul>
      )}
    </section>
  );
}
