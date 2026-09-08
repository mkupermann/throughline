import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { projectsApi, request } from "@/lib/api";
import { useAccess } from "@/features/access/AccessGate";
import { useLanguage } from "@/lib/language";
import { t } from "@/lib/ui";
import { projectLabel } from "./ProjectName";

export function CreateProject() {
  const { canEdit } = useAccess();
  useLanguage();
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<{
    project: string;
    display_name: string;
  } | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setError("");
    try {
      const value = await request<{ project: string; display_name: string }>(
        "/projects/create",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: new FormData(form).get("name"),
          }),
        },
      );
      setCreated(value);
      form.reset();
      await cache.invalidateQueries({ queryKey: ["project-library"] });
      await cache.invalidateQueries({ queryKey: ["assignment-projects"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!canEdit) return null;
  return (
    <details className="access-card">
      <summary>{t("Create a project")}</summary>
      <form onSubmit={submit}>
        <label>
          {t("Project name")}
          <input name="name" required maxLength={120} />
        </label>
        <p>
          {t(
            "Create a named place for your work, then assign its conversations. Imported folders remain unchanged.",
          )}
        </p>
        <button className="button" disabled={busy}>
          {t("Create project")}
        </button>
        {error && <p role="alert">{error}</p>}
        {created && (
          <Link to={`/project/${encodeURIComponent(created.project)}`}>
            {t("Open project")}: {created.display_name}
          </Link>
        )}
      </form>
    </details>
  );
}

export function ProjectAssignment({
  conversation,
  current,
  sourcePath,
}: {
  conversation: number;
  current: string;
  sourcePath: string | null;
}) {
  const { canEdit } = useAccess();
  useLanguage();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const projects = useQuery({
    queryKey: ["assignment-projects"],
    queryFn: () => projectsApi.all(),
    enabled: open,
  });
  const changes = useQuery({
    queryKey: ["assignment-history", conversation],
    queryFn: () =>
      request<{
        changes: {
          id: number;
          occurred_at: string;
          actor: string;
          actor_name?: string | null;
          previous_name: string | null;
          assigned_name: string | null;
        }[];
      }>(`/projects/assignment-history/${conversation}`),
    enabled: open,
  });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await request("/projects/assign", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_ids: [conversation],
          project: target === "__source__" ? null : target,
          expected_project: current,
        }),
      });
      await cache.invalidateQueries();
      setOpen(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <details
      className="project-assignment"
      onToggle={(e) => setOpen(e.currentTarget.open)}
      open={open}
    >
      <summary>{t("Project assignment and history")}</summary>
      <p>
        {t("Recorded source folder")}:{" "}
        <code>{sourcePath || t("Not recorded")}</code>
      </p>
      {canEdit && (
        <form onSubmit={submit}>
          <label>
            {t("Assign to project")}{" "}
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              required
            >
              <option value="">{t("Choose a project")}</option>
              <option value="__source__">
                {t("Restore source-folder grouping")}
              </option>
              {projects.data?.projects
                .filter((p) => p.display_name && p.project !== current)
                .map((p) => (
                  <option key={p.project} value={p.project}>
                    {projectLabel(p)}
                  </option>
                ))}
            </select>
          </label>
          <button className="button" disabled={busy || !target}>
            {t("Save assignment")}
          </button>
          <p>
            {t(
              "This correction survives re-import. Earlier project notes stay in their original project and show when their source moved.",
            )}
          </p>
          <Link to="/">
            {t("Create or name a project first if it is not listed.")}
          </Link>
        </form>
      )}
      {(error || projects.error || changes.error) && (
        <p role="alert">
          {error || projects.error?.message || changes.error?.message}
        </p>
      )}
      {changes.data?.changes.length === 0 && (
        <p>{t("No manual assignment changes recorded.")}</p>
      )}
      <ol>
        {changes.data?.changes.map((c) => (
          <li key={c.id}>
            <time dateTime={c.occurred_at}>
              {new Date(c.occurred_at).toLocaleString(undefined, {
                timeZoneName: "short",
              })}
            </time>{" "}
            · {c.actor_name || c.actor}: {c.previous_name || t("Source folder")}{" "}
            → {c.assigned_name || t("Source folder")}
          </li>
        ))}
      </ol>
    </details>
  );
}
