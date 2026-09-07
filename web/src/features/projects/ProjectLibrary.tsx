import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowUpRight, FolderOpen, Search } from "lucide-react";
import { carryProviders } from "@/lib/providerScope";
import { projectsApi } from "@/lib/api";
import "./story.css";

export function ProjectLibrary() {
  const [term, setTerm] = useState("");
  const [params] = useSearchParams();
  const providers = params.getAll("provider");
  const query = useQuery({
    queryKey: ["project-library", providers],
    queryFn: () => projectsApi.all(providers),
  });
  const projects = [...(query.data?.projects ?? [])]
    .filter((p) =>
      p.project.toLocaleLowerCase().includes(term.toLocaleLowerCase()),
    )
    .sort((a, b) => (b.last_active ?? "").localeCompare(a.last_active ?? ""));
  return (
    <div className="story">
      <header className="story-header">
        <p className="story-eyebrow">YOUR CONTINUING WORK</p>
        <div className="story-heading">
          <div>
            <h1>Projects</h1>
            <p className="story-subtitle">
              Return to the question. Follow what changed. Keep moving.
            </p>
          </div>
          <FolderOpen size={32} />
        </div>
      </header>
      <div className="story-controls">
        <label className="searchbar">
          <Search size={16} />
          <input
            aria-label="Find a project"
            placeholder="Find a project, including older work…"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
          />
        </label>
      </div>
      {query.isPending && <p role="status">Loading projects…</p>}
      {query.error && (
        <div role="alert">
          <h2>Could not load projects</h2>
          <p>{query.error.message}</p>
          <button className="button" onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      )}
      {query.data && (
        <p className="story-muted">
          {projects.length} projects · All imported history · Sorted by latest
          session
        </p>
      )}
      <div className="story-library">
        {projects.map((p) => (
          <Link
            key={p.project}
            to={carryProviders(
              `/project/${encodeURIComponent(p.project)}`,
              params,
            )}
          >
            <div>
              <h2>{p.project}</h2>
              <p>
                {p.sessions} sessions · {p.messages} messages
              </p>
              <p className="story-muted">
                {p.tool_names?.join(" · ") || "Tool not recorded"}
              </p>
            </div>
            <div className="story-library-date">
              <ArrowUpRight size={19} />
              <time>
                {p.last_active
                  ? new Date(p.last_active).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                    })
                  : ""}
              </time>
            </div>
          </Link>
        ))}
      </div>
      {query.data && !projects.length && (
        <div className="empty-state">
          <h2>
            {term ? "No matching projects" : "Your project history starts here"}
          </h2>
          <p>
            {term
              ? "Try a different project name."
              : "Import local sessions to see your work across tools."}
          </p>
          <Link to="/operate">Open import settings</Link>
        </div>
      )}
      <p className="story-footnote">
        Projects are grouped from working-folder names. Each project lets you
        inspect and separate its source folders.{" "}
        <Link to="/system-overview">System health</Link>
      </p>
    </div>
  );
}
