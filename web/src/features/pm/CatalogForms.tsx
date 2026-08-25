/** The full editors for roles and members — every configurable field the
 *  pipeline resolves at launch time: AI binding (roles), skills, instructions,
 *  document paths, token budget. Used for create and edit-in-place alike. */

import { useState } from "react";

import type { PmMember, PmRole } from "@/lib/api";
import { DocListEditor, SkillPicker } from "./shared";

// ── Role ─────────────────────────────────────────────────────────────────

export interface RoleDraft {
  name: string;
  description: string | null;
  default_ai_tool: string | null;
  default_ai_model: string | null;
  skill_refs: number[];
  instructions: string | null;
  document_refs: string[];
  token_budget: number | null;
}

function roleDraftFrom(r?: PmRole): RoleDraft {
  return {
    name: r?.name ?? "",
    description: r?.description ?? null,
    default_ai_tool: r?.default_ai_tool ?? null,
    default_ai_model: r?.default_ai_model ?? null,
    skill_refs: r?.skill_refs ?? [],
    instructions: r?.instructions ?? null,
    document_refs: r?.document_refs ?? [],
    token_budget: r?.token_budget ?? null,
  };
}

const strOrNull = (s: string): string | null => (s.trim() === "" ? null : s);

export function RoleForm({
  initial,
  submitLabel,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  initial?: PmRole;
  submitLabel: string;
  busy: boolean;
  error: unknown;
  onSubmit: (draft: RoleDraft) => void;
  onCancel: () => void;
}) {
  const [d, setD] = useState<RoleDraft>(() => roleDraftFrom(initial));
  const set = <K extends keyof RoleDraft>(k: K, v: RoleDraft[K]) =>
    setD((prev) => ({ ...prev, [k]: v }));

  return (
    <form
      className="pm-editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (d.name.trim()) onSubmit(d);
      }}
    >
      <div className="pm-editor-grid">
        <label className="pm-field">
          <span className="pm-label">Name</span>
          <input
            className="pm-input"
            value={d.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="z. B. Analyst"
            required
          />
        </label>
        <label className="pm-field">
          <span className="pm-label">Beschreibung</span>
          <input
            className="pm-input"
            value={d.description ?? ""}
            onChange={(e) => set("description", strOrNull(e.target.value))}
            placeholder="Wofür diese Rolle zuständig ist"
          />
        </label>
        <label className="pm-field">
          <span className="pm-label">KI-Werkzeug</span>
          <input
            className="pm-input"
            value={d.default_ai_tool ?? ""}
            onChange={(e) => set("default_ai_tool", strOrNull(e.target.value))}
            placeholder="z. B. aider, claude, vibe"
          />
        </label>
        <label className="pm-field">
          <span className="pm-label">KI-Modell</span>
          <input
            className="pm-input"
            value={d.default_ai_model ?? ""}
            onChange={(e) => set("default_ai_model", strOrNull(e.target.value))}
            placeholder="z. B. ollama_chat/qwen3-coder:30b"
          />
        </label>
        <label className="pm-field">
          <span className="pm-label">Token-Budget</span>
          <input
            className="pm-input pm-input-number"
            type="number"
            min={0}
            value={d.token_budget ?? ""}
            onChange={(e) =>
              set("token_budget", e.target.value.trim() === "" ? null : Number(e.target.value))
            }
            placeholder="unbegrenzt"
          />
        </label>
      </div>

      <div className="pm-field">
        <span className="pm-label">Skills</span>
        <SkillPicker value={d.skill_refs} onChange={(ids) => set("skill_refs", ids)} />
      </div>

      <label className="pm-field">
        <span className="pm-label">Anweisungen (Prompt)</span>
        <textarea
          className="pm-input pm-textarea"
          rows={5}
          value={d.instructions ?? ""}
          onChange={(e) => set("instructions", strOrNull(e.target.value))}
          placeholder="Mandat der Rolle — wird dem Agenten beim Start mitgegeben."
        />
      </label>

      <div className="pm-field">
        <span className="pm-label">Dokumente</span>
        <DocListEditor value={d.document_refs} onChange={(docs) => set("document_refs", docs)} />
      </div>

      <div className="pm-editor-actions">
        <button type="submit" className="button" disabled={busy || !d.name.trim()}>
          {busy ? "Speichert…" : submitLabel}
        </button>
        <button type="button" className="button pm-button-quiet" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
      {error != null && (
        <p className="pm-field-error" role="alert">
          Speichern fehlgeschlagen: {(error as Error).message}
        </p>
      )}
    </form>
  );
}

// ── Member ───────────────────────────────────────────────────────────────
// Members carry no AI binding of their own — that lives on the role, with a
// per-assignment override in the Zuordnungs-Matrix. Everything else (skills,
// prompt, documents, budget) is configurable here.

export interface MemberDraft {
  name: string;
  member_type: "human" | "agent";
  contact: string;
  skill_refs: number[];
  instructions: string | null;
  document_refs: string[];
  token_budget: number | null;
}

function memberDraftFrom(m?: PmMember): MemberDraft {
  return {
    name: m?.name ?? "",
    member_type: m?.member_type ?? "agent",
    contact: typeof m?.contact_info?.contact === "string" ? (m.contact_info.contact as string) : "",
    skill_refs: m?.skill_refs ?? [],
    instructions: m?.instructions ?? null,
    document_refs: m?.document_refs ?? [],
    token_budget: m?.token_budget ?? null,
  };
}

export function MemberForm({
  initial,
  submitLabel,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  initial?: PmMember;
  submitLabel: string;
  busy: boolean;
  error: unknown;
  onSubmit: (draft: MemberDraft) => void;
  onCancel: () => void;
}) {
  const [d, setD] = useState<MemberDraft>(() => memberDraftFrom(initial));
  const set = <K extends keyof MemberDraft>(k: K, v: MemberDraft[K]) =>
    setD((prev) => ({ ...prev, [k]: v }));

  return (
    <form
      className="pm-editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (d.name.trim()) onSubmit(d);
      }}
    >
      <div className="pm-editor-grid">
        <label className="pm-field">
          <span className="pm-label">Name</span>
          <input
            className="pm-input"
            value={d.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="z. B. Claude Code"
            required
          />
        </label>
        <label className="pm-field">
          <span className="pm-label">Typ</span>
          <select
            className="pm-input"
            value={d.member_type}
            onChange={(e) => set("member_type", e.target.value as "human" | "agent")}
          >
            <option value="agent">Agent</option>
            <option value="human">Mensch</option>
          </select>
        </label>
        <label className="pm-field">
          <span className="pm-label">Kontakt</span>
          <input
            className="pm-input"
            value={d.contact}
            onChange={(e) => set("contact", e.target.value)}
            placeholder="E-Mail, Handle o. Ä."
          />
        </label>
        <label className="pm-field">
          <span className="pm-label">Token-Budget</span>
          <input
            className="pm-input pm-input-number"
            type="number"
            min={0}
            value={d.token_budget ?? ""}
            onChange={(e) =>
              set("token_budget", e.target.value.trim() === "" ? null : Number(e.target.value))
            }
            placeholder="unbegrenzt"
          />
        </label>
      </div>

      <div className="pm-field">
        <span className="pm-label">Skills</span>
        <SkillPicker value={d.skill_refs} onChange={(ids) => set("skill_refs", ids)} />
      </div>

      <label className="pm-field">
        <span className="pm-label">Anweisungen (Prompt)</span>
        <textarea
          className="pm-input pm-textarea"
          rows={5}
          value={d.instructions ?? ""}
          onChange={(e) => set("instructions", strOrNull(e.target.value))}
          placeholder="Individuelle Arbeitsweise — wird an das Rollen-Mandat angehängt."
        />
      </label>

      <div className="pm-field">
        <span className="pm-label">Dokumente</span>
        <DocListEditor value={d.document_refs} onChange={(docs) => set("document_refs", docs)} />
      </div>

      <div className="pm-editor-actions">
        <button type="submit" className="button" disabled={busy || !d.name.trim()}>
          {busy ? "Speichert…" : submitLabel}
        </button>
        <button type="button" className="button pm-button-quiet" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
      {error != null && (
        <p className="pm-field-error" role="alert">
          Speichern fehlgeschlagen: {(error as Error).message}
        </p>
      )}
    </form>
  );
}
