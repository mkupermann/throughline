import { useAccess } from "@/features/access/AccessGate";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { projectsApi } from "@/lib/api";
import { t } from "@/lib/ui";

export function projectLabel(project: {
  project: string;
  display_name?: string | null;
  context_label?: string | null;
}) {
  return (
    project.display_name ||
    project.context_label ||
    t("Name pending — no readable source excerpt")
  );
}

export function ProjectName({
  project,
  name,
  folders,
  origin,
  sources = [],
  curated = false,
}: {
  project: string;
  name?: string | null;
  folders: number;
  origin?: string;
  sources?: number[];
  curated?: boolean;
}) {
  const { canEdit } = useAccess();
  const client = useQueryClient();
  const [draft, setDraft] = useState(name ?? "");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="project-naming">
      {origin === "model" && (
        <p>
          {t("AI-suggested name based on source excerpts. Review or edit it.")}{" "}
          {sources.map((id) => (
            <a key={id} href={`/c/${id}`}>
              {" "}
              #{id}
            </a>
          ))}
        </p>
      )}
      <p>
        {name
          ? t("Display name saved separately from imported folders.")
          : t(
              "Name not set. This is an imported folder group, not a confirmed project assignment.",
            )}
      </p>
      {name && !curated && (
        <p>
          {t(
            "Naming does not confirm that every conversation belongs to the same project.",
          )}
        </p>
      )}
      <p>
        {curated ? (
          t("User-created project")
        ) : (
          <>
            {t("Source folder name")}: <code>{project}</code>
          </>
        )}{" "}
        · {folders} {t("source folders")}
      </p>
      {canEdit &&
        (!editing ? (
          <button
            className="button"
            onClick={() => {
              setDraft(name ?? "");
              setEditing(true);
            }}
          >
            {t(name ? "Edit project name" : "Name this project")}
          </button>
        ) : (
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setSaving(true);
              setError("");
              try {
                await projectsApi.rename(project, draft.trim());
                await Promise.all([
                  client.invalidateQueries({ queryKey: ["project-library"] }),
                  client.invalidateQueries({ queryKey: ["story"] }),
                ]);
                setEditing(false);
              } catch (e) {
                setError(
                  e instanceof Error
                    ? e.message
                    : t("Could not save project name"),
                );
              } finally {
                setSaving(false);
              }
            }}
          >
            <label>
              {t("Project name")}{" "}
              <input
                autoFocus
                maxLength={120}
                required
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
            </label>
            <p>
              {t(
                "Renaming changes the display name. Conversation assignments remain unchanged.",
              )}
            </p>
            <button className="button" disabled={saving || !draft.trim()}>
              {t(saving ? "Saving…" : "Save name")}
            </button>{" "}
            <button
              type="button"
              className="button"
              disabled={saving}
              onClick={() => setEditing(false)}
            >
              {t("Cancel")}
            </button>
          </form>
        ))}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
