// web/src/features/pm/ProjectsOverviewPage.tsx
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { pmApi } from "@/lib/api";
import "@/styles/pm.css";

export function ProjectsOverviewPage() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["pm-projects"], queryFn: pmApi.listProjects });
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: () => pmApi.createProject({ name }),
    onSuccess: () => {
      setName("");
      queryClient.invalidateQueries({ queryKey: ["pm-projects"] });
    },
  });

  return (
    <section aria-labelledby="pm-projects-h" className="pm-catalog">
      <h1 id="pm-projects-h">Projekte</h1>
      <form
        className="pm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <input placeholder="Projektname" value={name} onChange={(e) => setName(e.target.value)} />
        <button type="submit" disabled={create.isPending}>Projekt anlegen</button>
      </form>
      <ul className="pm-card-grid">
        {(data?.projects ?? []).map((p) => (
          <li key={p.id}>
            <Link to={`/pm/projects/${p.id}`} className="pm-card">
              <span className="pm-card-name">{p.name}</span>
              <span className={`pm-status pm-status-${p.status}`}>{p.status}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
