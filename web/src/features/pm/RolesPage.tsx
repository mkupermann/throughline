/** /pm/roles — Rollen-Katalog: list with configuration summaries and full
 *  create/edit-in-place editors (AI binding, skills, prompt, documents,
 *  budget). Saved values round-trip through PATCH /pm/roles/{id}. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { pmApi, type PmRole } from "@/lib/api";
import { RoleForm, type RoleDraft } from "./CatalogForms";
import {
  Breadcrumbs,
  EmptyState,
  ErrorState,
  SkeletonRows,
  fmtInt,
  plural,
  useSkills,
} from "./shared";
import "@/styles/pm.css";

function RoleSummary({ role }: { role: PmRole }) {
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
        <span className="pm-cat-none">Kein KI-Werkzeug gesetzt</span>
      )}
      <span className="pm-cat-fact tabular">{plural(role.skill_refs.length, "Skill", "Skills")}</span>
      <span className="pm-cat-fact tabular">{plural(role.document_refs.length, "Dokument", "Dokumente")}</span>
      <span className="pm-cat-fact tabular">
        {role.token_budget !== null ? `${fmtInt(role.token_budget)} Tokens Budget` : "Kein Budget"}
      </span>
      <span className="pm-cat-fact">
        {role.instructions ? "Anweisungen gesetzt" : "Keine Anweisungen"}
      </span>
    </div>
  );
}

function RoleRow({ role }: { role: PmRole }) {
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

  return (
    <li className="pm-cat-row">
      <div className="pm-cat-head">
        <span className="pm-cat-name">{role.name}</span>
        <button
          type="button"
          className="pm-linklike"
          aria-expanded={editing}
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? "Schließen" : "Bearbeiten"}
        </button>
      </div>
      {editing ? (
        <RoleForm
          initial={role}
          submitLabel="Änderungen speichern"
          busy={patch.isPending}
          error={patch.isError ? patch.error : null}
          onSubmit={(draft) => patch.mutate(draft)}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <RoleSummary role={role} />
      )}
    </li>
  );
}

export function RolesPage() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-roles"],
    queryFn: pmApi.listRoles,
  });
  // Warm the skills cache before an editor opens, so the picker is ready.
  useSkills();

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
        <Breadcrumbs items={[{ label: "Project Management", to: "/pm" }, { label: "Rollen" }]} />
        <div className="page-header-row pm-header-row">
          <div>
            <h1 className="page-title">Rollen</h1>
            <p className="page-subtitle">
              Eine Rolle bündelt KI-Werkzeug, Skills, Anweisungen und Budget für einen Sitz in der
              Pipeline.
            </p>
          </div>
          {!creating && (
            <button
              type="button"
              className="button pm-button-flush"
              onClick={() => setCreating(true)}
            >
              <Plus size={14} aria-hidden />
              Rolle anlegen
            </button>
          )}
        </div>
      </header>

      {creating && (
        <div className="pm-cat-create">
          <RoleForm
            submitLabel="Rolle anlegen"
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
        <ErrorState title="Rollen können nicht geladen werden" error={error} onRetry={refetch} />
      ) : data.roles.length === 0 ? (
        <EmptyState title="Noch keine Rollen">
          <p>Rollen definieren die Sitze einer Team-Pipeline — oben die erste anlegen.</p>
        </EmptyState>
      ) : (
        <ul className="pm-cat-list">
          {data.roles.map((r) => (
            <RoleRow key={r.id} role={r} />
          ))}
        </ul>
      )}
    </section>
  );
}
