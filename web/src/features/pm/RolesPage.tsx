// web/src/features/pm/RolesPage.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { pmApi } from "@/lib/api";
import "@/styles/pm.css";

// Role names "Executor"/"Tester" (case-insensitive) are what
// pm_launch.launch_task recognizes to route AI bindings into pipeline.sh's
// AI_PIPELINE_EXECUTOR_MODEL/AI_PIPELINE_TESTER_AGENT — flagged in-form so
// the connection isn't invisible to whoever is naming roles.
const ROUTED_ROLE_NAMES = ["executor", "tester"];

export function RolesPage() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["pm-roles"], queryFn: pmApi.listRoles });
  const [name, setName] = useState("");
  const [aiTool, setAiTool] = useState("");
  const [aiModel, setAiModel] = useState("");

  const create = useMutation({
    mutationFn: () =>
      pmApi.createRole({
        name,
        default_ai_tool: aiTool || null,
        default_ai_model: aiModel || null,
      }),
    onSuccess: () => {
      setName("");
      setAiTool("");
      setAiModel("");
      queryClient.invalidateQueries({ queryKey: ["pm-roles"] });
    },
  });

  return (
    <section aria-labelledby="roles-h" className="pm-catalog">
      <h1 id="roles-h">Rollen</h1>
      <form
        className="pm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <input placeholder="Name (z.B. Executor)" value={name} onChange={(e) => setName(e.target.value)} />
        <input placeholder="Standard-Tool (z.B. aider)" value={aiTool} onChange={(e) => setAiTool(e.target.value)} />
        <input placeholder="Standard-Modell" value={aiModel} onChange={(e) => setAiModel(e.target.value)} />
        <button type="submit" disabled={create.isPending}>Rolle anlegen</button>
      </form>
      <ul className="pm-list">
        {(data?.roles ?? []).map((r) => (
          <li key={r.id} className="pm-list-row">
            <span className="pm-list-name">{r.name}</span>
            <span className="pm-list-meta">
              {r.default_ai_tool ?? "—"} / {r.default_ai_model ?? "—"}
            </span>
            {ROUTED_ROLE_NAMES.includes(r.name.trim().toLowerCase()) && (
              <span className="pm-badge" title="Wird automatisch an pipeline.sh weitergereicht">
                pipeline.sh
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
