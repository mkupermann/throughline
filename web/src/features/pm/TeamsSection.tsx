/** The signature element: each team rendered as its real mechanics — a
 *  pipeline of seats in team order (Analyst → Executor → Tester, or whatever
 *  roles the team defines), each seat carrying the assigned member, the
 *  effective AI binding and a live status dot; a budget gauge beside it.
 *
 *  Assignment lives here too: an unoccupied seat offers a compact member
 *  select that assigns on pick; an occupied seat gets a subtle remove
 *  affordance. No separate Zuordnungs-Matrix — the pipeline row is the one
 *  place assignments are made and unmade. */

import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Plus, X } from "lucide-react";

import {
  pmApi,
  type PmAssignment,
  type PmMember,
  type PmRole,
  type PmTask,
  type PmTeam,
} from "@/lib/api";
import { useLang } from "./i18n";
import { BudgetBar, EmptyState, ErrorState, InlineConfirmButton, SkeletonRows, plural } from "./shared";
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
  team,
  projectId,
  assignment,
  member,
  members,
  live,
}: {
  role: PmRole;
  index: number;
  team: PmTeam;
  projectId: number;
  assignment: PmAssignment | undefined;
  member: PmMember | undefined;
  members: PmMember[];
  live: boolean;
}) {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const occupied = assignment !== undefined;
  const ai = effectiveAi(role, assignment);
  const memberName = occupied ? member?.name ?? t.teams.seat.memberFallback(assignment!.member_id) : null;

  const assign = useMutation({
    mutationFn: (memberId: number) =>
      pmApi.createAssignment({
        pm_project_id: projectId,
        team_id: team.id,
        role_id: role.id,
        member_id: memberId,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pm-assignments", projectId] }),
  });

  const unassign = useMutation({
    mutationFn: (assignmentId: number) => pmApi.deleteAssignment(assignmentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pm-assignments", projectId] }),
  });

  return (
    <div
      className={`pm-seat pm-seat-hue-${index % 3}${occupied ? "" : " pm-seat-empty"}`}
      data-live={live && occupied ? "true" : undefined}
    >
      <div className="pm-seat-role">
        {role.name}
        {live && occupied && (
          <span className="pm-live-dot" role="img" aria-label={t.teams.liveDotLabel} title={t.teams.liveDotLabel} />
        )}
      </div>
      <div className="pm-seat-member-row">
        <span className="pm-seat-member">{occupied ? memberName : t.teams.seat.unassigned}</span>
        {occupied && (
          <InlineConfirmButton
            className="pm-seat-unassign"
            disabled={unassign.isPending}
            pending={unassign.isPending}
            title={t.teams.seat.removeTitle(memberName ?? "")}
            ariaLabel={t.teams.seat.removeAria(memberName ?? "", role.name)}
            onConfirm={() => unassign.mutate(assignment!.id)}
          >
            <X size={11} aria-hidden />
          </InlineConfirmButton>
        )}
      </div>
      <div className="pm-seat-ai">
        {ai ? <code>{ai}</code> : <span className="pm-seat-ai-none">{t.teams.seat.noAiTool}</span>}
      </div>
      {!occupied && (
        <div className="pm-seat-assign">
          <select
            className="pm-input pm-input-compact"
            value=""
            disabled={assign.isPending || members.length === 0}
            aria-label={t.teams.seat.pickAria(role.name)}
            onChange={(e) => {
              const v = e.target.value;
              if (v) assign.mutate(Number(v));
            }}
          >
            <option value="" disabled>
              {t.teams.seat.pickPlaceholder}
            </option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {assign.isError && (
        <p className="pm-field-error" role="alert">
          {t.teams.seat.assignError((assign.error as Error).message)}
        </p>
      )}
      {unassign.isError && (
        <p className="pm-field-error" role="alert">
          {t.teams.seat.removeError((unassign.error as Error).message)}
        </p>
      )}
    </div>
  );
}

function LinkRoleForm({ team, linkedRoleIds }: { team: PmTeam; linkedRoleIds: number[] }) {
  const { t } = useLang();
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
        <span className="sr-only">{t.teams.linkRole.srLabel(team.name)}</span>
        <select
          className="pm-input"
          value={roleId}
          onChange={(e) => setRoleId(e.target.value ? Number(e.target.value) : "")}
        >
          <option value="">{t.teams.linkRole.placeholder}</option>
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
        {t.teams.linkRole.submit}
      </button>
      {link.isError && (
        <span className="pm-field-error" role="alert">
          {t.teams.linkRole.error((link.error as Error).message)}
        </span>
      )}
    </form>
  );
}

function PipelineRow({
  team,
  projectId,
  assignments,
  members,
  tasks,
}: {
  team: PmTeam;
  projectId: number;
  assignments: PmAssignment[];
  members: PmMember[];
  tasks: PmTask[];
}) {
  const { t } = useLang();
  const queryClient = useQueryClient();
  const roles = team.roles ?? [];
  const teamTasks = tasks.filter((tk) => tk.team_id === team.id);
  const running = teamTasks.filter((tk) => tk.status === "running").length;
  const tokensUsed = teamTasks.reduce((sum, tk) => sum + tk.tokens_used, 0);
  const memberById = new Map(members.map((m) => [m.id, m]));

  const patchTeam = useMutation({
    mutationFn: (token_budget: number | null) => pmApi.patchTeam(team.id, { token_budget }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pm-project-teams"] }),
  });

  return (
    <article className="pm-pipeline" aria-label={team.name}>
      <header className="pm-pipeline-head">
        <h3 className="pm-pipeline-name">{team.name}</h3>
        {running > 0 && (
          <span className="pm-status pm-status-running">
            {plural(running, t.teams.taskRunningOne, t.teams.taskRunningMany)}
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
        <p className="pm-pipeline-empty">{t.teams.pipelineEmpty}</p>
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
                  team={team}
                  projectId={projectId}
                  assignment={assignment}
                  member={assignment ? memberById.get(assignment.member_id) : undefined}
                  members={members}
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
  const { t } = useLang();
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

  const candidates = (allTeams.data?.teams ?? []).filter((tm) => !linkedTeamIds.includes(tm.id));

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
            <span className="sr-only">{t.teams.linkTeam.srExisting}</span>
            <select
              className="pm-input"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">{t.teams.linkTeam.existingPlaceholder}</option>
              {candidates.map((tm) => (
                <option key={tm.id} value={tm.id}>
                  {tm.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            className="button pm-button-flush"
            disabled={teamId === "" || link.isPending}
          >
            {t.teams.linkTeam.submit}
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
            <span className="sr-only">{t.teams.linkTeam.srNewName}</span>
            <input
              className="pm-input"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t.teams.linkTeam.newNamePlaceholder}
              autoFocus
            />
          </label>
          <button
            type="submit"
            className="button pm-button-flush"
            disabled={!newName.trim() || createAndLink.isPending}
          >
            {t.teams.linkTeam.createAndLink}
          </button>
          <button
            type="button"
            className="button pm-button-flush pm-button-quiet"
            onClick={() => setCreating(false)}
          >
            {t.teams.linkTeam.cancel}
          </button>
          {createAndLink.isError && (
            <span className="pm-field-error" role="alert">
              {t.teams.linkTeam.error((createAndLink.error as Error).message)}
            </span>
          )}
        </form>
      ) : (
        <button type="button" className="button pm-button-flush" onClick={() => setCreating(true)}>
          <Plus size={14} aria-hidden />
          {t.teams.linkTeam.createNew}
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
  const { t } = useLang();
  const list = teams.data?.teams ?? [];

  return (
    <section className="pm-section" aria-labelledby="pm-teams-h">
      <h2 id="pm-teams-h" className="section-label">
        {t.teams.h2}
      </h2>

      {teams.isPending ? (
        <SkeletonRows n={2} />
      ) : teams.error ? (
        <ErrorState
          title={t.teams.errorTitle}
          error={teams.error}
          onRetry={teams.refetch}
        />
      ) : list.length === 0 ? (
        <EmptyState title={t.teams.emptyTitle}>
          <p>{t.teams.emptyBody}</p>
        </EmptyState>
      ) : (
        <div className="pm-pipeline-list">
          {list.map((team) => (
            <PipelineRow
              key={team.id}
              team={team}
              projectId={projectId}
              assignments={assignments.data?.assignments ?? []}
              members={members.data?.members ?? []}
              tasks={tasks}
            />
          ))}
        </div>
      )}

      <LinkTeamForm projectId={projectId} linkedTeamIds={list.map((tm) => tm.id)} />
    </section>
  );
}
