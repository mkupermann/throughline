/** /pm/teams — Team-Katalog: teams can be created and configured (name,
 *  description, budget) independently of any project, then linked into one
 *  or more projects from the cockpit — mirrors RolesPage/MembersPage.
 *  Saved values round-trip through PATCH /pm/teams/{id}. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { pmApi, type PmTeam } from "@/lib/api";
import { TeamForm, type TeamDraft } from "./CatalogForms";
import { useLang } from "./i18n";
import {
  EmptyState,
  ErrorState,
  PmHeaderBar,
  SkeletonRows,
  fmtInt,
} from "./shared";
import "@/styles/pm.css";

function TeamSummary({ team }: { team: PmTeam }) {
  const { t } = useLang();
  return (
    <div className="pm-cat-summary">
      {team.description && <span className="pm-cat-desc">{team.description}</span>}
      <span className="pm-cat-fact tabular">
        {team.token_budget !== null ? t.catalog.budgetTokens(fmtInt(team.token_budget)) : t.catalog.noBudget}
      </span>
    </div>
  );
}

function TeamRow({ team }: { team: PmTeam }) {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  const patch = useMutation({
    mutationFn: (draft: TeamDraft) => pmApi.patchTeam(team.id, draft),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["pm-teams"] });
      queryClient.invalidateQueries({ queryKey: ["pm-project-teams"] });
    },
  });

  return (
    <li className="pm-cat-row">
      <div className="pm-cat-head">
        <span className="pm-cat-name">{team.name}</span>
        <button
          type="button"
          className="pm-linklike"
          aria-expanded={editing}
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? t.common.close : t.common.edit}
        </button>
      </div>
      {editing ? (
        <TeamForm
          initial={team}
          submitLabel={t.catalog.saveChanges}
          busy={patch.isPending}
          error={patch.isError ? patch.error : null}
          onSubmit={(draft) => patch.mutate(draft)}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <TeamSummary team={team} />
      )}
    </li>
  );
}

export function TeamsPage() {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-teams"],
    queryFn: pmApi.listTeams,
  });

  const create = useMutation({
    mutationFn: (draft: TeamDraft) => pmApi.createTeam(draft),
    onSuccess: () => {
      setCreating(false);
      queryClient.invalidateQueries({ queryKey: ["pm-teams"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
    },
  });

  return (
    <section className="pm-page">
      <header className="page-header">
        <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.breadcrumb.teams }]} />
        <div className="page-header-row pm-header-row">
          <div>
            <h1 className="page-title">{t.teamsPage.h1}</h1>
            <p className="page-subtitle">{t.teamsPage.subtitle}</p>
          </div>
          {!creating && (
            <button
              type="button"
              className="button pm-button-flush"
              onClick={() => setCreating(true)}
            >
              <Plus size={14} aria-hidden />
              {t.teamsPage.create}
            </button>
          )}
        </div>
      </header>

      {creating && (
        <div className="pm-cat-create">
          <TeamForm
            submitLabel={t.teamsPage.create}
            busy={create.isPending}
            error={create.isError ? create.error : null}
            onSubmit={(draft) => create.mutate(draft)}
            onCancel={() => setCreating(false)}
          />
        </div>
      )}

      {isPending ? (
        <SkeletonRows n={3} />
      ) : error ? (
        <ErrorState title={t.teamsPage.errorTitle} error={error} onRetry={refetch} />
      ) : data.teams.length === 0 ? (
        <EmptyState title={t.teamsPage.emptyTitle}>
          <p>{t.teamsPage.emptyBody}</p>
        </EmptyState>
      ) : (
        <ul className="pm-cat-list">
          {data.teams.map((tm) => (
            <TeamRow key={tm.id} team={tm} />
          ))}
        </ul>
      )}
    </section>
  );
}
