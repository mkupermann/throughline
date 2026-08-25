// web/src/features/pm/MembersPage.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { pmApi } from "@/lib/api";
import "@/styles/pm.css";

export function MembersPage() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["pm-members"], queryFn: pmApi.listMembers });
  const [name, setName] = useState("");
  const [type, setType] = useState<"human" | "agent">("agent");

  const create = useMutation({
    mutationFn: () => pmApi.createMember({ name, member_type: type }),
    onSuccess: () => {
      setName("");
      queryClient.invalidateQueries({ queryKey: ["pm-members"] });
    },
  });

  return (
    <section aria-labelledby="members-h" className="pm-catalog">
      <h1 id="members-h">Mitglieder</h1>
      <form
        className="pm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <select value={type} onChange={(e) => setType(e.target.value as "human" | "agent")}>
          <option value="agent">KI-Agent</option>
          <option value="human">Mensch</option>
        </select>
        <button type="submit" disabled={create.isPending}>Mitglied anlegen</button>
      </form>
      <ul className="pm-list">
        {(data?.members ?? []).map((m) => (
          <li key={m.id} className="pm-list-row">
            <span className="pm-list-name">{m.name}</span>
            <span className="pm-list-meta">{m.member_type === "human" ? "Mensch" : "KI-Agent"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
