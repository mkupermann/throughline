import {ProcessAll} from "@/features/operate/ProcessAll";
import { projectLabel } from "./ProjectName";
import { t } from "@/lib/ui";
import { getLang, useLanguage } from "@/lib/language";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowUpRight, FolderOpen, Search } from "lucide-react";
import { carryProviders } from "@/lib/providerScope";
import { projectsApi } from "@/lib/api";
import "./story.css";

export function ProjectLibrary() {
  useLanguage();
  const [term, setTerm] = useState("");
  const [params] = useSearchParams();
  const providers = params.getAll("provider");
  const query = useQuery({
    queryKey: ["project-library", providers],
    queryFn: () => projectsApi.all(providers),
  });
  const projects = [...(query.data?.projects ?? [])]
    .filter((p) =>
      (p.project + " " + (p.display_name ?? "")).toLocaleLowerCase().includes(term.toLocaleLowerCase()),
    )
    .sort((a, b) => (b.last_active ?? "").localeCompare(a.last_active ?? ""));
  return (
    <div className="story">
      <header className="story-header">
        <p className="story-eyebrow">{t("YOUR CONTINUING WORK")}</p>
        <div className="story-heading">
          <div>
            <h1>{t("Projects")}</h1>
            <p className="story-subtitle">
              {t("Your projects’ goals, current state and conversations in one place.")}
            </p>
          </div>
          <FolderOpen size={32} />
        </div>
      </header>
      <ProcessAll />
      <div className="story-controls">
        <label className="searchbar">
          <Search size={16} />
          <input
            aria-label={t("Find a project")}
            placeholder={t("Find a project, including older work…")}
            value={term}
            onChange={(e) => setTerm(e.target.value)}
          />
        </label>
      </div>
      {query.isPending && <p role="status">{t("Loading projects…")}</p>}
      {query.error && (
        <div role="alert">
          <h2>{t("Could not load projects")}</h2>
          <p>{query.error.message}</p>
          <button className="button" onClick={() => void query.refetch()}>{t("Try again")}</button>
        </div>
      )}
      {query.data && (
        <p className="story-muted">
          {projects.length}{t(" projects · All imported history · Sorted by latest session")}</p>
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
              <h2>{projectLabel(p)}</h2>
              {p.name_origin === "model" && <p className="story-muted">{t("AI-suggested name · source conversations inside")}</p>}
              {!p.display_name && <p className="story-muted">{t("Name and project assignment need review")}</p>}
              <p>
                {p.sessions}{t(" Conversations · ")}{p.messages}{t(" messages")}</p>
              <p className="story-muted">
                {p.tool_names?.join(" · ") || t("Tool not recorded")}
              </p>
            </div>
            <div className="story-library-date">
              <ArrowUpRight size={19} />
              <time>
                {p.last_active
                  ? new Date(p.last_active).toLocaleDateString(getLang() === "de" ? "de-DE" : "en-US", {
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
            {term ? t("No matching projects") : t("Your project history starts here")}
          </h2>
          <p>
            {term
              ? t("Try a different project name.")
              : t("Import local sessions to see your work across tools.")}
          </p>
          <Link to="/operate">{t("Open import settings")}</Link>
        </div>
      )}
      <p className="story-footnote">{t("Projects are grouped from working-folder names. Each project lets you inspect and separate its source folders.")}{" "}
        <Link to="/system-overview">{t("System health")}</Link>
      </p>
    </div>
  );
}
