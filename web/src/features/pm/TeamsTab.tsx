// web/src/features/pm/TeamsTab.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { pmApi } from "@/lib/api";

export function TeamsTab({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const { data: teamsData } = useQuery({
    queryKey: ["pm-project-teams", projectId],
    queryFn: () => pmApi.projectTeams(projectId),
  });
  const { data: allTeams } = useQuery({ queryKey: ["pm-teams"], queryFn: pmApi.listTeams });
  const { data: allRoles } = useQuery({ queryKey: ["pm-roles"], queryFn: pmApi.listRoles });
  const { data: allMembers } = useQuery({ queryKey: ["pm-members"], queryFn: pmApi.listMembers });

  const [teamToLink, setTeamToLink] = useState<number | "">("");

  const linkTeam = useMutation({
    mutationFn: (teamId: number) => pmApi.linkProjectTeam(projectId, teamId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pm-project-teams", projectId] }),
  });

  return (
    <div className="pm-teams-tab">
      <div className="pm-form">
        <select value={teamToLink} onChange={(e) => setTeamToLink(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Team hinzufuegen…</option>
          {(allTeams?.teams ?? []).map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <button
          disabled={!teamToLink}
          onClick={() => teamToLink && linkTeam.mutate(teamToLink)}
        >
          Verknuepfen
        </button>
      </div>

      {(teamsData?.teams ?? []).map((team) => (
        <TeamRow
          key={team.id}
          projectId={projectId}
          team={team}
          allRoles={allRoles?.roles ?? []}
          allMembers={allMembers?.members ?? []}
        />
      ))}
    </div>
  );
}

function TeamRow({
  projectId,
  team,
  allRoles,
  allMembers,
}: {
  projectId: number;
  team: { id: number; name: string; roles?: { id: number; name: string }[] };
  allRoles: { id: number; name: string }[];
  allMembers: { id: number; name: string }[];
}) {
  const queryClient = useQueryClient();
  const [roleToLink, setRoleToLink] = useState<number | "">("");
  const [assignRole, setAssignRole] = useState<number | "">("");
  const [assignMember, setAssignMember] = useState<number | "">("");

  const linkRole = useMutation({
    mutationFn: (roleId: number) => pmApi.linkTeamRole(team.id, roleId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pm-project-teams", projectId] }),
  });

  const assign = useMutation({
    mutationFn: () =>
      pmApi.createAssignment({
        pm_project_id: projectId,
        team_id: team.id,
        role_id: assignRole as number,
        member_id: assignMember as number,
      }),
    onSuccess: () => {
      setAssignRole("");
      setAssignMember("");
    },
  });

  return (
    <div className="pm-team-row">
      <h3>{team.name}</h3>
      <div className="pm-form">
        <select value={roleToLink} onChange={(e) => setRoleToLink(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Rolle hinzufuegen…</option>
          {allRoles.map((r) => (
            <option key={r.id} value={r.id}>{r.name}</option>
          ))}
        </select>
        <button disabled={!roleToLink} onClick={() => roleToLink && linkRole.mutate(roleToLink)}>
          Verknuepfen
        </button>
      </div>
      <ul className="pm-list">
        {(team.roles ?? []).map((r) => (
          <li key={r.id} className="pm-list-row">{r.name}</li>
        ))}
      </ul>
      <div className="pm-form">
        <select value={assignRole} onChange={(e) => setAssignRole(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Rolle…</option>
          {(team.roles ?? []).map((r) => (
            <option key={r.id} value={r.id}>{r.name}</option>
          ))}
        </select>
        <select value={assignMember} onChange={(e) => setAssignMember(e.target.value ? Number(e.target.value) : "")}>
          <option value="">Mitglied…</option>
          {allMembers.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
        <button disabled={!assignRole || !assignMember} onClick={() => assign.mutate()}>
          Zuordnen
        </button>
      </div>
    </div>
  );
}
