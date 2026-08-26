/** /pm/roles — Rollen-Katalog: list with configuration summaries and full
 *  create/edit-in-place editors (AI binding, skills, prompt, documents,
 *  budget). Saved values round-trip through PATCH /pm/roles/{id}. */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { pmApi, type PmRole } from "@/lib/api";
import { RoleForm, type RoleDraft } from "./CatalogForms";
import { useLang } from "./i18n";
import {
  EmptyState,
  ErrorState,
  InlineConfirmButton,
  PmHeaderBar,
  SkeletonRows,
  fmtInt,
  plural,
  useSkills,
} from "./shared";
import "@/styles/pm.css";

/** Client-side name+description substring match, case-insensitive — same
 *  look and feel as the skills search in the role/member editor, over the
 *  much shorter catalog lists themselves. */
function matchesFilter(q: string, name: string, description: string | null): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return name.toLowerCase().includes(needle) || (description ?? "").toLowerCase().includes(needle);
}

function RoleSummary({ role }: { role: PmRole }) {
  const { t } = useLang();
  const ai =
    role.default_ai_tool || role.default_ai_model
      ? [role.default_ai_tool, role.default_ai_model].filter(Boolean).join(" · ")
      : null;
  return (
    <div className="pm-cat-summary">
      {role.description && <span className="pm-cat-desc">{role.description}</span>}
      {ai ? (
        <code className="pm-cat-ai">{ai}</code>
      ) : (
        <span className="pm-cat-none">{t.catalog.noAiTool}</span>
      )}
      <span className="pm-cat-fact tabular">{plural(role.skill_refs.length, t.common.skillOne, t.common.skillMany)}</span>
      <span className="pm-cat-fact tabular">{plural(role.document_refs.length, t.common.documentOne, t.common.documentMany)}</span>
      <span className="pm-cat-fact tabular">
        {role.token_budget !== null ? t.catalog.budgetTokens(fmtInt(role.token_budget)) : t.catalog.noBudget}
      </span>
      <span className="pm-cat-fact">
        {role.instructions ? t.catalog.instructionsSet : t.catalog.noInstructions}
      </span>
    </div>
  );
}

function RoleRow({ role }: { role: PmRole }) {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  const patch = useMutation({
    mutationFn: (draft: RoleDraft) => pmApi.patchRole(role.id, draft),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["pm-roles"] });
      queryClient.invalidateQueries({ queryKey: ["pm-project-teams"] });
    },
  });

  const del = useMutation({
    mutationFn: () => pmApi.deleteRole(role.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pm-roles"] });
      queryClient.invalidateQueries({ queryKey: ["pm-project-teams"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
    },
  });

  return (
    <li className="pm-cat-row">
      <div className="pm-cat-head">
        <span className="pm-cat-name">{role.name}</span>
        <div className="pm-cat-head-actions">
          <button
            type="button"
            className="pm-linklike"
            aria-expanded={editing}
            onClick={() => setEditing((e) => !e)}
          >
            {editing ? t.common.close : t.common.edit}
          </button>
          <InlineConfirmButton
            className="pm-linklike pm-linklike-danger"
            disabled={del.isPending}
            pending={del.isPending}
            onConfirm={() => del.mutate()}
          >
            {del.isPending ? t.catalog.deleting : t.catalog.delete}
          </InlineConfirmButton>
        </div>
      </div>
      {editing ? (
        <RoleForm
          initial={role}
          submitLabel={t.catalog.saveChanges}
          busy={patch.isPending}
          error={patch.isError ? patch.error : null}
          onSubmit={(draft) => patch.mutate(draft)}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <RoleSummary role={role} />
      )}
      {del.isError && (
        <p className="pm-field-error" role="alert">
          {t.catalog.deleteFailed((del.error as Error).message)}
        </p>
      )}
    </li>
  );
}

export function RolesPage() {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("");
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-roles"],
    queryFn: pmApi.listRoles,
  });
  // Warm the skills cache before an editor opens, so the picker is ready.
  useSkills();

  const filtered = useMemo(
    () => (data?.roles ?? []).filter((r) => matchesFilter(filter, r.name, r.description)),
    [data, filter],
  );

  const create = useMutation({
    mutationFn: (draft: RoleDraft) => pmApi.createRole(draft),
    onSuccess: () => {
      setCreating(false);
      queryClient.invalidateQueries({ queryKey: ["pm-roles"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
    },
  });

  return (
    <section className="pm-page">
      <header className="page-header">
        <PmHeaderBar items={[{ label: t.common.projectManagement, to: "/pm" }, { label: t.breadcrumb.roles }]} />
        <div className="page-header-row pm-header-row">
          <div>
            <h1 className="page-title">{t.rolesPage.h1}</h1>
            <p className="page-subtitle">{t.rolesPage.subtitle}</p>
          </div>
          {!creating && (
            <button
              type="button"
              className="button pm-button-flush"
              onClick={() => setCreating(true)}
            >
              <Plus size={14} aria-hidden />
              {t.rolesPage.create}
            </button>
          )}
        </div>
      </header>

      {creating && (
        <div className="pm-cat-create">
          <RoleForm
            submitLabel={t.rolesPage.create}
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
        <ErrorState title={t.rolesPage.errorTitle} error={error} onRetry={refetch} />
      ) : data.roles.length === 0 ? (
        <EmptyState title={t.rolesPage.emptyTitle}>
          <p>{t.rolesPage.emptyBody}</p>
        </EmptyState>
      ) : (
        <>
          <input
            type="search"
            className="pm-input pm-cat-filter"
            placeholder={t.catalog.filterPlaceholder}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label={t.catalog.filterLabel}
          />
          {filtered.length === 0 ? (
            <p className="pm-cat-filter-none">{t.catalog.filterNone}</p>
          ) : (
            <ul className="pm-cat-list">
              {filtered.map((r) => (
                <RoleRow key={r.id} role={r} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
