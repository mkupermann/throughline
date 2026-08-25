/** /pm/members — Mitglieder-Katalog: list with configuration summaries and
 *  full create/edit-in-place editors (type, contact, skills, prompt,
 *  documents, budget). Saved values round-trip through PATCH /pm/members. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { pmApi, type PmMember } from "@/lib/api";
import { MemberForm, type MemberDraft } from "./CatalogForms";
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

function bodyFrom(draft: MemberDraft) {
  return {
    name: draft.name,
    member_type: draft.member_type,
    contact_info: draft.contact.trim() ? { contact: draft.contact.trim() } : {},
    skill_refs: draft.skill_refs,
    instructions: draft.instructions,
    document_refs: draft.document_refs,
    token_budget: draft.token_budget,
  };
}

function MemberSummary({ member }: { member: PmMember }) {
  const contact =
    typeof member.contact_info?.contact === "string" ? (member.contact_info.contact as string) : null;
  return (
    <div className="pm-cat-summary">
      <span className="pm-cat-fact">{member.member_type === "agent" ? "Agent" : "Mensch"}</span>
      {contact && <span className="pm-cat-desc">{contact}</span>}
      <span className="pm-cat-fact tabular">{plural(member.skill_refs.length, "Skill", "Skills")}</span>
      <span className="pm-cat-fact tabular">{plural(member.document_refs.length, "Dokument", "Dokumente")}</span>
      <span className="pm-cat-fact tabular">
        {member.token_budget !== null
          ? `${fmtInt(member.token_budget)} Tokens Budget`
          : "Kein Budget"}
      </span>
      <span className="pm-cat-fact">
        {member.instructions ? "Anweisungen gesetzt" : "Keine Anweisungen"}
      </span>
    </div>
  );
}

function MemberRow({ member }: { member: PmMember }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  const patch = useMutation({
    mutationFn: (draft: MemberDraft) => pmApi.patchMember(member.id, bodyFrom(draft)),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["pm-members"] });
    },
  });

  return (
    <li className="pm-cat-row">
      <div className="pm-cat-head">
        <span className="pm-cat-name">{member.name}</span>
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
        <MemberForm
          initial={member}
          submitLabel="Änderungen speichern"
          busy={patch.isPending}
          error={patch.isError ? patch.error : null}
          onSubmit={(draft) => patch.mutate(draft)}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <MemberSummary member={member} />
      )}
    </li>
  );
}

export function MembersPage() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ["pm-members"],
    queryFn: pmApi.listMembers,
  });
  useSkills();

  const create = useMutation({
    mutationFn: (draft: MemberDraft) => pmApi.createMember(bodyFrom(draft)),
    onSuccess: () => {
      setCreating(false);
      queryClient.invalidateQueries({ queryKey: ["pm-members"] });
      queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
    },
  });

  return (
    <section className="pm-page">
      <header className="page-header">
        <Breadcrumbs
          items={[{ label: "Project Management", to: "/pm" }, { label: "Mitglieder" }]}
        />
        <div className="page-header-row pm-header-row">
          <div>
            <h1 className="page-title">Mitglieder</h1>
            <p className="page-subtitle">
              Menschen und Agenten, die in der Zuordnungs-Matrix eine Rolle besetzen.
            </p>
          </div>
          {!creating && (
            <button
              type="button"
              className="button pm-button-flush"
              onClick={() => setCreating(true)}
            >
              <Plus size={14} aria-hidden />
              Mitglied anlegen
            </button>
          )}
        </div>
      </header>

      {creating && (
        <div className="pm-cat-create">
          <MemberForm
            submitLabel="Mitglied anlegen"
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
        <ErrorState
          title="Mitglieder können nicht geladen werden"
          error={error}
          onRetry={refetch}
        />
      ) : data.members.length === 0 ? (
        <EmptyState title="Noch keine Mitglieder">
          <p>Mitglieder besetzen die Sitze der Pipeline — oben das erste anlegen.</p>
        </EmptyState>
      ) : (
        <ul className="pm-cat-list">
          {data.members.map((m) => (
            <MemberRow key={m.id} member={m} />
          ))}
        </ul>
      )}
    </section>
  );
}
