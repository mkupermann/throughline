// web/src/features/pm/ProjectDetailPage.tsx
import { useState } from "react";
import { useParams } from "react-router-dom";

import { TeamsTab } from "./TeamsTab";
import { TasksTab } from "./TasksTab"; // from Task 17
import "@/styles/pm.css";

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const [tab, setTab] = useState<"teams" | "tasks">("tasks");

  return (
    <section className="pm-project-detail">
      <nav className="pm-tabs">
        <button className={tab === "tasks" ? "active" : ""} onClick={() => setTab("tasks")}>Tasks</button>
        <button className={tab === "teams" ? "active" : ""} onClick={() => setTab("teams")}>Teams</button>
      </nav>
      {tab === "teams" ? <TeamsTab projectId={projectId} /> : <TasksTab projectId={projectId} />}
    </section>
  );
}
