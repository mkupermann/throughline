/** Zuordnungs-Matrix: one roles × members grid per team. A filled cell is
 *  the assignment (with its AI override, if set); an empty cell is a
 *  click-to-assign control. Simplified responsibility matrix — full RACI
 *  semantics are out of scope by agreement. */

import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Plus } from "lucide-react";

import { pmApi, type PmAssignment, type PmMember, type PmTeam } from "@/lib/api";
import { EmptyState, ErrorState, SkeletonRows } from "./shared";

function TeamMatrix({
  projectId,
  team,
  assignments,
  members,
}: {
  projectId: number;
  team: PmTeam;
  assignments: PmAssignment[];
  members: PmMember[];
}) {
  const queryClient = useQueryClient();
  const roles = team.roles ?? [];

  const assign = useMutation({
    mutationFn: (cell: { role_id: number; member_id: number }) =>
      pmApi.createAssignment({
        pm_project_id: projectId,
        team_id: team.id,
        role_id: cell.role_id,
        member_id: cell.member_id,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["pm-assignments", projectId] }),
  });

  if (roles.length === 0) return null;

  return (
    <div className="pm-matrix">
      <h3 className="pm-matrix-team">{team.name}</h3>
      <div className="scroll-x">
        <table className="pm-matrix-table">
          <thead>
            <tr>
              <th scope="col" className="pm-matrix-corner">
                <span className="sr-only">Mitglied</span>
              </th>
              {roles.map((r, i) => (
                <th scope="col" key={r.id} className={`pm-matrix-role pm-seat-hue-${i % 3}`}>
                  {r.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <th scope="row" className="pm-matrix-member">
                  {m.name}
                  <span className="pm-matrix-member-type">
                    {m.member_type === "agent" ? "Agent" : "Mensch"}
                  </span>
                </th>
                {roles.map((r) => {
                  const a = assignments.find(
                    (x) => x.team_id === team.id && x.role_id === r.id && x.member_id === m.id,
                  );
                  if (a) {
                    const override =
                      a.ai_tool || a.ai_model
                        ? [a.ai_tool, a.ai_model].filter(Boolean).join(" · ")
                        : null;
                    return (
                      <td key={r.id} className="pm-matrix-cell pm-matrix-cell-set">
                        <span className="pm-matrix-mark">
                          <Check size={13} aria-hidden />
                          <span className="sr-only">
                            {m.name} ist als {r.name} zugewiesen
                          </span>
                          zugewiesen
                        </span>
                        {override && <code className="pm-matrix-override">{override}</code>}
                      </td>
                    );
                  }
                  return (
                    <td key={r.id} className="pm-matrix-cell">
                      <button
                        type="button"
                        className="pm-matrix-assign"
                        disabled={assign.isPending}
                        onClick={() => assign.mutate({ role_id: r.id, member_id: m.id })}
                        title={`${m.name} als ${r.name} zuweisen`}
                      >
                        <Plus size={13} aria-hidden />
                        <span className="sr-only">
                          {m.name} als {r.name} zuweisen
                        </span>
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {assign.isError && (
        <p className="pm-field-error" role="alert">
          Zuweisung fehlgeschlagen: {(assign.error as Error).message}
        </p>
      )}
    </div>
  );
}

export function MatrixSection({
  projectId,
  teams,
  assignments,
  members,
}: {
  projectId: number;
  teams: UseQueryResult<{ teams: PmTeam[] }>;
  assignments: UseQueryResult<{ assignments: PmAssignment[] }>;
  members: UseQueryResult<{ members: PmMember[] }>;
}) {
  const teamList = (teams.data?.teams ?? []).filter((t) => (t.roles ?? []).length > 0);
  const pending = teams.isPending || assignments.isPending || members.isPending;
  const error = assignments.error ?? members.error;

  // Without a team that has roles there is no grid to draw; the Teams
  // section above already carries the call to action for that case.
  if (!pending && !error && teamList.length === 0) return null;

  return (
    <section className="pm-section" aria-labelledby="pm-matrix-h">
      <h2 id="pm-matrix-h" className="section-label">
        Zuordnungs-Matrix
      </h2>

      {pending ? (
        <SkeletonRows n={2} />
      ) : error ? (
        <ErrorState
          title="Zuordnungen können nicht geladen werden"
          error={error}
          onRetry={() => {
            assignments.refetch();
            members.refetch();
          }}
        />
      ) : (members.data?.members ?? []).length === 0 ? (
        <EmptyState title="Noch keine Mitglieder">
          <p>
            Ohne Mitglieder bleibt die Matrix leer. Im Katalog „Mitglieder" anlegen, dann hier
            zuweisen.
          </p>
        </EmptyState>
      ) : (
        teamList.map((team) => (
          <TeamMatrix
            key={team.id}
            projectId={projectId}
            team={team}
            assignments={assignments.data?.assignments ?? []}
            members={members.data?.members ?? []}
          />
        ))
      )}
    </section>
  );
}
