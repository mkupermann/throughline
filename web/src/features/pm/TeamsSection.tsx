/** The signature element: each team rendered as its real mechanics — a
 *  pipeline of seats in team order (Analyst → Executor → Tester, or whatever
 *  roles the team defines), each seat carrying the assigned member, the
 *  effective AI binding and a live status dot; a budget gauge beside it. */

import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Plus } from "lucide-react";

import {
  pmApi,
  type PmAssignment,
  type PmMember,
  type PmRole,
  type PmTask,
  type PmTeam,
} from "@/lib/api";
import { BudgetBar, EmptyState, ErrorState, SkeletonRows, fmtInt } from "./shared";
import { InlineBudget } from "./CockpitPage";

/** The effective AI binding for a seat: assignment override, else the
 *  role's default — the same precedence resolve_assignment applies at
 *  launch time. */
function effectiveAi(role: PmRole, assignment: PmAssignment | undefined): string | null {
  const tool = assignment?.ai_tool ?? role.default_ai_tool;
  const model = assignment?.ai_model ?? role.default_ai_model;
  if (!tool && !model) return null;
  if (tool && model) return `${tool} · ${model}`;
  return tool ?? model;
}

function Seat({
  role,
  index,
  assignment,
  member,
  live,
}: {
  role: PmRole;
  index: number;
  assignment: PmAssignment | undefined;
  member: PmMember | undefined;
  live: boolean;
}) {
  const occupied = assignment !== undefined;
  const ai = effectiveAi(role, assignment);
  return (
    <div
      className={`pm-seat pm-seat-hue-${index % 3}${occupied ? "" : " pm-seat-empty"}`}
      data-live={live && occupied ? "true" : undefined}
    >
      <div className="pm-seat-role">
        {role.name}
        {live && occupied && (
          <span className="pm-live-dot" role="img" aria-label="Task läuft" title="Task läuft" />
        )}
      </div>
      <div className="pm-seat-member">
        {occupied ? member?.name ?? `Mitglied ${assignment.member_id}` : "Nicht besetzt"}
      </div>
      <div className="pm-seat-ai">
        {occupied ? (
          ai ? (
            <code>{ai}</code>
          ) : (
            <span className="pm-seat-ai-none">Kein KI-Werkzeug gesetzt</span>
          )
        ) : (
          <span className="pm-seat-ai-none">In der Matrix zuweisen</span>
        )}
      </div>
    </div>
  );
}

function LinkRoleForm({ team, linkedRoleIds }: { team: PmTeam; linkedRoleIds: number[] }) {
  const queryClient = useQueryClient();
  const [roleId, setRoleId] = useState<number | "">("");
  const roles = useQuery({ queryKey: ["pm-roles"], queryFn: pmApi.listRoles });

  const link = useMutation({
    mutationFn: (rid: number) => pmApi.linkTeamRole(team.id, rid),
    onSuccess: () => {
      setRoleId("");
      queryClient.invalidateQueries({ queryKey: ["pm-project-teams"] });
    },
  });

  const candidates = (roles.data?.roles ?? []).filter((r) => !linkedRoleIds.includes(r.id));
  if (roles.isPending || candidates.length === 0) return null;

  return (
    <form
      className="pm-linkrole"
      onSubmit={(e) => {
        e.preventDefault();
        if (roleId !== "") link.mutate(roleId);
      }}
    >
      <label className="pm-field pm-field-inline">
        <span className="sr-only">Rolle für {team.name}</span>
        <select
          className="pm-input"
          value={roleId}
          onChange={(e) => setRoleId(e.target.value ? Number(e.target.value) : "")}
        >
          <option value="">Rolle verknüpfen…</option>
          {candidates.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className="button pm-button-flush"
        disabled={roleId === "" || link.isPending}
      >
        Verknüpfen
      </button>
      {link.isError && (
        <span className="pm-field-error" role="alert">
          Verknüpfen fehlgeschlagen: {(link.error as Error).message}
        </span>
      )}
    </form>
  );
}

function PipelineRow({
  team,
  assignments,
  members,
  tasks,
}: {
  team: PmTeam;
  assignments: PmAssignment[];
  members: PmMember[];
  tasks: PmTask[];
}) {
  const queryClient = useQueryClient();
  const roles = team.roles ?? [];
  const teamTasks = tasks.filter((t) => t.team_id === team.id);
  const running = teamTasks.filter((t) => t.status === "running").length;
  const tokensUsed = teamTasks.reduce((sum, t) => sum + t.tokens_used, 0);
  const memberById = new Map(members.map((m) => [m.id, m]));

  const patchTeam = useMutation({
    mutationFn: (token_budget: number | null) => pmApi.patchTeam(team.id, { token_budget }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pm-project-teams"] }),
  });

  return (
    <article className="pm-pipeline" aria-label={`Team ${team.name}`}>
      <header className="pm-pipeline-head">
        <h3 className="pm-pipeline-name">{team.name}</h3>
        {running > 0 && (
          <span className="pm-status pm-status-running">
            {fmtInt(running)} {running === 1 ? "Task läuft" : "Tasks laufen"}
          </span>
        )}
        <div className="pm-pipeline-budget">
          <InlineBudget
            value={team.token_budget}
            saving={patchTeam.isPending}
            onSave={(b) => patchTeam.mutate(b)}
          />
        </div>
      </header>

      {team.token_budget !== null && (
        <div className="pm-pipeline-gauge">
          <BudgetBar used={tokensUsed} budget={team.token_budget} />
        </div>
      )}

      {roles.length === 0 ? (
        <p className="pm-pipeline-empty">
          Noch keine Rollen im Team — unten eine Rolle verknüpfen, damit die Pipeline Sitze bekommt.
        </p>
      ) : (
        <div className="pm-pipeline-seats">
          {roles.map((role, i) => {
            const assignment = assignments.find(
              (a) => a.team_id === team.id && a.role_id === role.id,
            );
            return (
              <div key={role.id} className="pm-seat-slot">
                {i > 0 && (
                  <span className="pm-seat-arrow" aria-hidden>
                    <ArrowRight size={14} />
                  </span>
                )}
                <Seat
                  role={role}
                  index={i}
                  assignment={assignment}
                  member={assignment ? memberById.get(assignment.member_id) : undefined}
                  live={running > 0}
                />
              </div>
            );
          })}
        </div>
      )}

      <footer className="pm-pipeline-foot">
        <LinkRoleForm team={team} linkedRoleIds={roles.map((r) => r.id)} />
      </footer>
    </article>
  );
}

function LinkTeamForm({ projectId, linkedTeamIds }: { projectId: number; linkedTeamIds: number[] }) {
  const queryClient = useQueryClient();
  const allTeams = useQuery({ queryKey: ["pm-teams"], queryFn: pmApi.listTeams });
  const [teamId, setTeamId] = useState<number | "">("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["pm-project-teams", projectId] });
    queryClient.invalidateQueries({ queryKey: ["pm-teams"] });
    queryClient.invalidateQueries({ queryKey: ["pm-overview"] });
  };

  const link = useMutation({
    mutationFn: (tid: number) => pmApi.linkProjectTeam(projectId, tid),
    onSuccess: () => {
      setTeamId("");
      invalidate();
    },
  });

  const createAndLink = useMutation({
    mutationFn: async () => {
      const team = await pmApi.createTeam({ name: newName.trim() });
      await pmApi.linkProjectTeam(projectId, team.id);
    },
    onSuccess: () => {
      setNewName("");
      setCreating(false);
      invalidate();
    },
  });

  const candidates = (allTeams.data?.teams ?? []).filter((t) => !linkedTeamIds.includes(t.id));

  return (
    <div className="pm-linkteam">
      {candidates.length > 0 && (
        <form
          className="pm-linkrole"
          onSubmit={(e) => {
            e.preventDefault();
            if (teamId !== "") link.mutate(teamId);
          }}
        >
          <label className="pm-field pm-field-inline">
            <span className="sr-only">Bestehendes Team</span>
            <select
              className="pm-input"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">Team verknüpfen…</option>
              {candidates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            className="button pm-button-flush"
            disabled={teamId === "" || link.isPending}
          >
            Verknüpfen
          </button>
        </form>
      )}

      {creating ? (
        <form
          className="pm-linkrole"
          onSubmit={(e) => {
            e.preventDefault();
            if (newName.trim()) createAndLink.mutate();
          }}
        >
          <label className="pm-field pm-field-inline">
            <span className="sr-only">Name des neuen Teams</span>
            <input
              className="pm-input"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Name des neuen Teams"
              autoFocus
            />
          </label>
          <button
            type="submit"
            className="button pm-button-flush"
            disabled={!newName.trim() || createAndLink.isPending}
          >
            Anlegen und verknüpfen
          </button>
          <button
            type="button"
            className="button pm-button-flush pm-button-quiet"
            onClick={() => setCreating(false)}
          >
            Abbrechen
          </button>
          {createAndLink.isError && (
            <span className="pm-field-error" role="alert">
              {(createAndLink.error as Error).message}
            </span>
          )}
        </form>
      ) : (
        <button type="button" className="button pm-button-flush" onClick={() => setCreating(true)}>
          <Plus size={14} aria-hidden />
          Neues Team anlegen
        </button>
      )}
    </div>
  );
}

export function TeamsSection({
  projectId,
  teams,
  assignments,
  members,
  tasks,
}: {
  projectId: number;
  teams: UseQueryResult<{ teams: PmTeam[] }>;
  assignments: UseQueryResult<{ assignments: PmAssignment[] }>;
  members: UseQueryResult<{ members: PmMember[] }>;
  tasks: PmTask[];
}) {
  const list = teams.data?.teams ?? [];

  return (
    <section className="pm-section" aria-labelledby="pm-teams-h">
      <h2 id="pm-teams-h" className="section-label">
        Teams
      </h2>

      {teams.isPending ? (
        <SkeletonRows n={2} />
      ) : teams.error ? (
        <ErrorState
          title="Teams können nicht geladen werden"
          error={teams.error}
          onRetry={teams.refetch}
        />
      ) : list.length === 0 ? (
        <EmptyState title="Noch kein Team verknüpft">
          <p>Ein Team bringt Rollen und Mitglieder in eine Pipeline. Unten verknüpfen oder anlegen.</p>
        </EmptyState>
      ) : (
        <div className="pm-pipeline-list">
          {list.map((team) => (
            <PipelineRow
              key={team.id}
              team={team}
              assignments={assignments.data?.assignments ?? []}
              members={members.data?.members ?? []}
              tasks={tasks}
            />
          ))}
        </div>
      )}

      <LinkTeamForm projectId={projectId} linkedTeamIds={list.map((t) => t.id)} />
    </section>
  );
}
