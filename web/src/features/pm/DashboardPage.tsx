/** /pm — Projekt-Dashboard: every PM project as a card with real aggregates
 *  (GET /pm/overview), plus the catalog entry points and project creation. */

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Users, IdCard, Boxes } from "lucide-react";

import { pmApi, type PmOverviewProject, type PmProject, type PmRepoProject } from "@/lib/api";
import { useLang } from "./i18n";
import {
  BudgetBar,
  EmptyState,
  ErrorState,
  InlineConfirmButton,
  LangToggle,
  ProjectStatusChip,
  SkeletonRows,
  TASK_STATUSES,
  fmtInt,
  fmtRelative,
  plural,
} from "./shared";
import "@/styles/pm.css";

function TaskCountChips({ tasks }: { tasks: PmOverviewProject["tasks"] }) {
  const { t } = useLang();
  const entries = TASK_STATUSES.filter((s) => tasks[s] > 0);
  if (entries.length === 0) {
    return <span className="pm-card-quiet">{t.dashboard.noTasksYet}</span>;
  }
  return (
    <div className="pm-card-tasks">
      {entries.map((s) => (
        <span key={s} className={`pm-status pm-status-${s}`}>
          {fmtInt(tasks[s])} {t.status.task[s]}
        </span>
      ))}
    </div>
  );
}

function ProjectCard({ p }: { p: PmOverviewProject }) {
  const { t } = useLang();
  return (
    <li>
      <Link to={`/pm/projects/${p.id}`} className="pm-card">
        <div className="pm-card-head">
          <span className="pm-card-name">{p.name}</span>
          <ProjectStatusChip status={p.status} />
        </div>
        <div className="pm-card-meta">
          <span>{fmtInt(p.teams)} {p.teams === 1 ? t.dashboard.teamOne : t.dashboard.teamMany}</span>
          <span aria-hidden>·</span>
          <span>{t.dashboard.activity(fmtRelative(p.last_activity))}</span>
        </div>
        <TaskCountChips tasks={p.tasks} />
        <BudgetBar used={p.tokens_used} budget={p.token_budget} />
      </Link>
    </li>
  );
}

function CreateProjectForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useLang();
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
        {t.dashboard.createProject}
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
        <span className="pm-label">{t.dashboard.nameLabel}</span>
        <input
          ref={nameRef}
          className="pm-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t.dashboard.namePlaceholder}
          required
        />
      </label>
      <label className="pm-field pm-field-inline">
        <span className="pm-label">{t.dashboard.budgetLabel}</span>
        <input
          className="pm-input pm-input-number"
          type="number"
          min={0}
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          placeholder={t.common.unlimited}
        />
      </label>
      <div className="pm-inline-form-actions">
        <button type="submit" className="button" disabled={create.isPending || !name.trim()}>
          {t.dashboard.createSubmit}
        </button>
        <button type="button" className="button pm-button-quiet" onClick={() => setOpen(false)}>
          {t.common.cancel}
        </button>
      </div>
      {create.isError && (
        <p className="pm-field-error" role="alert">
          {t.dashboard.createFailed((create.error as Error).message)}
        </p>
      )}
    </form>
  );
}

/** Client-side name+description substring match, case-insensitive — same
 *  look and feel as the catalog filters (RolesPage/MembersPage). */
function matchesRepoFilter(q: string, rp: PmRepoProject): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return rp.name.toLowerCase().includes(needle) || (rp.description ?? "").toLowerCase().includes(needle);
}

function RepoProjectRow({ rp }: { rp: PmRepoProject }) {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const adopt = useMutation({
    mutationFn: () => pmApi.adoptRepoProject(rp.id),
    onSuccess: (pmProject) => {
      // Seed the projects cache with the fresh row BEFORE navigating: the
      // cockpit resolves its project from ["pm-projects"], and a stale cached
      // list (staleTime 30s) without the just-created id rendered a false
      // "Project not found" (live-gemeldeter Bug beim Adoptieren).
      queryClient.setQueryData<{ projects: PmProject[] }>(["pm-projects"], (old) =>
        old ? { projects: [pmProject, ...old.projects] } : { projects: [pmProject] },
      );
      queryClient.invalidateQueries({ queryKey: ["pm-projects"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
      queryClient.invalidateQueries({ queryKey: ["pm-repo-projects"] });
      navigate(`/pm/projects/${pmProject.id}`);
    },
  });

  return (
    <li className="pm-repo-row">
      <span className="pm-repo-row-name">{rp.name}</span>
      <span className="pm-repo-row-meta tabular">
        {plural(rp.sessions, t.dashboard.repoProjects.sessionOne, t.dashboard.repoProjects.sessionMany)}
      </span>
      <span className="pm-repo-row-meta">
        {rp.last_active ? t.dashboard.repoProjects.lastActive(fmtRelative(rp.last_active)) : t.dashboard.repoProjects.neverActive}
      </span>
      {rp.linked_pm_project_id !== null ? (
        <Link to={`/pm/projects/${rp.linked_pm_project_id}`} className="pm-repo-row-linked">
          {t.dashboard.repoProjects.linkedBadge(rp.linked_pm_project_name ?? rp.name)}
        </Link>
      ) : (
        <InlineConfirmButton
          className="button pm-button-flush pm-button-quiet"
          confirmLabel={t.dashboard.repoProjects.adoptConfirm}
          disabled={adopt.isPending}
          pending={adopt.isPending}
          onConfirm={() => adopt.mutate()}
        >
          {adopt.isPending ? t.dashboard.repoProjects.adopting : t.dashboard.repoProjects.adopt}
        </InlineConfirmButton>
      )}
      {adopt.isError && (
        <p className="pm-field-error" role="alert">
          {t.dashboard.repoProjects.adoptFailed((adopt.error as Error).message)}
        </p>
      )}
    </li>
  );
}

/** Existing memory-layer projects, adoptable/linkable as PM projects — the
 *  schema bridge (pm_project_repos) already existed and was unused before
 *  this section. Collapsed behind a <details> once the list gets long
 *  (>10 rows): the dashboard's job is calm at-a-glance status, not another
 *  full catalog to scroll through. */
function RepoProjectsSection() {
  const { t } = useLang();
  const [filter, setFilter] = useState("");
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-repo-projects"],
    queryFn: pmApi.listRepoProjects,
  });

  const all = data?.repo_projects ?? [];
  const filtered = useMemo(() => all.filter((rp) => matchesRepoFilter(filter, rp)), [all, filter]);

  if (isPending) {
    return (
      <section className="pm-repo-section" aria-labelledby="pm-repo-h">
        <h2 id="pm-repo-h" className="section-label">{t.dashboard.repoProjects.h2}</h2>
        <SkeletonRows n={2} />
      </section>
    );
  }

  if (error) {
    return (
      <section className="pm-repo-section" aria-labelledby="pm-repo-h">
        <h2 id="pm-repo-h" className="section-label">{t.dashboard.repoProjects.h2}</h2>
        <ErrorState title={t.dashboard.repoProjects.errorTitle} error={error} onRetry={refetch} />
      </section>
    );
  }

  if (all.length === 0) {
    return null;
  }

  const body = (
    <>
      <input
        type="search"
        className="pm-input pm-cat-filter pm-repo-filter"
        placeholder={t.dashboard.repoProjects.searchPlaceholder}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        aria-label={t.dashboard.repoProjects.searchLabel}
      />
      {filtered.length === 0 ? (
        <p className="pm-cat-filter-none">{t.dashboard.repoProjects.filterNone}</p>
      ) : (
        <ul className="pm-repo-list">
          {filtered.map((rp) => (
            <RepoProjectRow key={rp.id} rp={rp} />
          ))}
        </ul>
      )}
    </>
  );

  if (all.length > 10) {
    return (
      <details className="pm-repo-section">
        <summary>{t.dashboard.repoProjects.summary(fmtInt(all.length))}</summary>
        <div className="pm-repo-section-body">{body}</div>
      </details>
    );
  }

  return (
    <section className="pm-repo-section" aria-labelledby="pm-repo-h">
      <h2 id="pm-repo-h" className="section-label">{t.dashboard.repoProjects.h2}</h2>
      {body}
    </section>
  );
}

export function DashboardPage() {
  const { t } = useLang();
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
      {/* No breadcrumb eyebrow here — on the dashboard itself, a single
          "Project Management" crumb would just repeat the H1 right below
          it. Subpages keep PmHeaderBar's breadcrumb; it earns its place
          there as an actual trail (Project Management › Roles, etc.). */}
      <div className="pm-headerbar pm-headerbar-end">
        <LangToggle />
      </div>
      <div className="page-header-row pm-header-row">
        <div>
          <h1 className="page-title">{t.common.projectManagement}</h1>
          <p className="page-subtitle">{t.dashboard.subtitle}</p>
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
        <ErrorState title={t.dashboard.errorTitle} error={error} onRetry={refetch} />
      </section>
    );
  }

  const { projects, counts } = data;

  return (
    <section className="pm-page">
      {header}

      <div className="pm-catalog-links" role="group" aria-label={t.dashboard.catalogGroupLabel}>
        <Link to="/pm/roles" className="pm-catalog-link">
          <IdCard size={15} aria-hidden />
          <span>{t.dashboard.catalogRoles}</span>
          <span className="tabular pm-catalog-count">{fmtInt(counts.roles)}</span>
        </Link>
        <Link to="/pm/members" className="pm-catalog-link">
          <Users size={15} aria-hidden />
          <span>{t.dashboard.catalogMembers}</span>
          <span className="tabular pm-catalog-count">{fmtInt(counts.members)}</span>
        </Link>
        <Link to="/pm/teams" className="pm-catalog-link">
          <Boxes size={15} aria-hidden />
          <span>{t.dashboard.catalogTeams}</span>
          <span className="tabular pm-catalog-count">{fmtInt(counts.teams)}</span>
        </Link>
      </div>

      {projects.length === 0 ? (
        <EmptyState title={t.dashboard.emptyTitle}>
          <p>{t.dashboard.emptyBody}</p>
        </EmptyState>
      ) : (
        <ul className="pm-card-grid">
          {projects.map((p) => (
            <ProjectCard key={p.id} p={p} />
          ))}
        </ul>
      )}

      <RepoProjectsSection />
    </section>
  );
}
