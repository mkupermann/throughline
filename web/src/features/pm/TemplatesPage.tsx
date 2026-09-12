import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  Layers,
  Plus,
  Search,
  Users,
  UserRound,
  X,
} from "lucide-react";
import { request } from "@/lib/api";
import { useLanguage } from "@/lib/language";
import "./templates.css";
import { TEMPLATE_CATEGORIES } from "./templateCategories";

type Kind = "project" | "team" | "role";
export type TeamTemplate = {
  id: number;
  kind: Kind;
  name: string;
  description: string;
  version: number;
  content: Record<string, unknown>;
  archived?: boolean;
};
const api = {
  list: () => request<{ templates: TeamTemplate[] }>("/pm/templates"),
  save: (
    value: Omit<TeamTemplate, "id" | "version">,
    id?: number,
    version?: number,
  ) =>
    request<TeamTemplate>(`/pm/templates${id ? `/${id}` : ""}`, {
      method: id ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        id
          ? {
              name: value.name,
              description: value.description,
              content: value.content,
              expected_version: version,
            }
          : value,
      ),
    }),
  use: (template: TeamTemplate, name: string) =>
    request<{ kind: Kind; id: number }>(
      `/pm/templates/${template.id}/instantiate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, version: template.version }),
      },
    ),
};
const fields: Record<Kind, [string, string, string][]> = {
  project: [
    ["objective", "Objective", "Ziel"],
    ["deliverables", "Deliverables", "Ergebnisse"],
    ["stages", "Stages", "Phasen"],
    ["acceptance_criteria", "Acceptance criteria", "Abnahmekriterien"],
  ],
  team: [
    ["workflow", "Collaboration sequence", "Zusammenarbeit"],
    ["review_policy", "Review requirements", "Prüfkriterien"],
  ],
  role: [
    [
      "instructions",
      "Responsibilities and instructions",
      "Verantwortung und Anweisungen",
    ],
    ["expected_output", "Expected output", "Erwartete Ausgabe"],
    ["allowed_tools", "Allowed tools", "Erlaubte Werkzeuge"],
  ],
};
const icons = { project: Layers, team: Users, role: UserRound };
export function TemplatesPage() {
  const { lang } = useLanguage();
  const de = lang === "de";
  const tr = (en: string, german: string) => (de ? german : en);
  const kinds: Record<Kind, string> = {
    project: tr("Project templates", "Projektvorlagen"),
    team: tr("Team templates", "Teamvorlagen"),
    role: tr("Role templates", "Rollenvorlagen"),
  };
  const [kind, setKind] = useState<Kind>("project");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const categoryName = (value: unknown) => {
    const found = TEMPLATE_CATEGORIES.find(
      ([id]) => id === (value ?? "general"),
    );
    return found ? tr(found[1], found[2]) : String(value);
  };
  const [selected, setSelected] = useState<TeamTemplate | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState<Record<string, unknown>>({});
  const [instanceName, setInstanceName] = useState("");
  const [created, setCreated] = useState<{ kind: Kind; id: number } | null>(
    null,
  );
  const heading = useRef<HTMLHeadingElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["pm-templates"], queryFn: api.list });
  const save = useMutation({
    mutationFn: () =>
      api.save(
        { kind, name: name.trim(), description, content },
        selected?.id,
        selected?.version,
      ),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ["pm-templates"] });
      setSelected(item);
      setEditing(false);
      setInstanceName(item.name);
    },
  });
  const instantiate = useMutation({
    mutationFn: () => api.use(selected!, instanceName.trim()),
    onSuccess: (result) => {
      setCreated(result);
      qc.invalidateQueries({ queryKey: ["pm-overview"] });
      qc.invalidateQueries({ queryKey: ["pm-projects"] });
      qc.invalidateQueries({ queryKey: ["pm-teams"] });
      qc.invalidateQueries({ queryKey: ["pm-roles"] });
    },
  });
  const open = (item: TeamTemplate | null, edit = false) => {
    trigger.current = document.activeElement as HTMLElement;
    save.reset();
    instantiate.reset();
    setCreated(null);
    setSelected(item);
    setEditing(edit);
    setName(item?.name ?? "");
    setDescription(item?.description ?? "");
    setContent(item?.content ?? (category === "all" ? {} : { category }));
    setInstanceName(item?.name ?? "");
  };
  const close = () => {
    setSelected(null);
    setEditing(false);
    trigger.current?.focus();
  };
  useEffect(() => {
    if (selected || editing) heading.current?.focus();
  }, [selected, editing]);
  const items = (query.data?.templates ?? []).filter(
    (item) =>
      !item.archived &&
      item.kind === kind &&
      (category === "all" ||
        (item.content.category ?? "general") === category) &&
      `${item.name} ${item.description} ${categoryName(item.content.category)}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const references = (content.role_templates ?? []) as {
    id: number;
    version: number;
  }[];
  const teamReference = content.team_template as
    { id: number; version: number } | undefined;
  const referenceLabel = (ref: { id: number; version: number }) =>
    `${query.data?.templates.find((item) => item.id === ref.id)?.name ?? `#${ref.id}`} · v${ref.version}`;
  const busy = save.isPending || instantiate.isPending;
  return (
    <section className="templates-page">
      <nav
        className="ops-tabs"
        aria-label={tr("AI team operations", "KI-Teamsteuerung")}
      >
        <Link to="/pm">{tr("Overview", "Übersicht")}</Link>
        <Link to="/pm/templates" aria-current="page">
          {tr("Templates", "Vorlagen")}
        </Link>
        <Link to="/pm#resources">{tr("Resources", "Ressourcen")}</Link>
      </nav>
      <header className="templates-header">
        <div>
          <p className="templates-eyebrow">
            {tr("AI TEAM OPERATIONS", "KI-TEAMSTEUERUNG")}
          </p>
          <h1>{tr("Templates", "Vorlagen")}</h1>
          <p>
            {tr(
              "Reusable projects, teams and roles. Make each one your own.",
              "Wiederverwendbare Projekte, Teams und Rollen. Anpassbar an deine Arbeit.",
            )}
          </p>
        </div>
        <button
          className="button button-primary"
          disabled={busy}
          onClick={() => open(null, true)}
        >
          <Plus size={16} aria-hidden />
          {tr("Create template", "Vorlage erstellen")}
        </button>
      </header>
      <div className="templates-layout">
        <aside
          className="templates-categories"
          aria-label={tr("Template type", "Vorlagentyp")}
        >
          {(Object.keys(kinds) as Kind[]).map((k) => {
            const Icon = icons[k];
            return (
              <button
                key={k}
                disabled={busy}
                aria-pressed={kind === k}
                onClick={() => {
                  setKind(k);
                  close();
                }}
              >
                <Icon size={18} aria-hidden />
                <span>{kinds[k]}</span>
                <span className="template-count">
                  {
                    (query.data?.templates ?? []).filter(
                      (item) => !item.archived && item.kind === k,
                    ).length
                  }
                </span>
              </button>
            );
          })}
          <p>
            {tr(
              "Templates are starting points. Your saved projects stay independent when a template changes.",
              "Vorlagen sind Ausgangspunkte. Gespeicherte Projekte bleiben bei Vorlagenänderungen unabhängig.",
            )}
          </p>
        </aside>
        <div className="templates-library">
          <div className="templates-toolbar">
            <h2>{kinds[kind]}</h2>
            <label className="template-search">
              <Search size={16} aria-hidden />
              <span className="sr-only">
                {tr("Search templates", "Vorlagen suchen")}
              </span>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={tr("Search templates…", "Vorlagen suchen…")}
              />
            </label>
          </div>
          <label className="template-category-filter">
            {tr("Category", "Kategorie")}
            <select
              disabled={busy}
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                close();
              }}
            >
              <option value="all">
                {tr("All categories", "Alle Kategorien")}
              </option>
              {TEMPLATE_CATEGORIES.map(([id, en, german]) => (
                <option key={id} value={id}>
                  {tr(en, german)}
                </option>
              ))}
            </select>
          </label>
          <p role="status" className="templates-result">
            {query.isPending
              ? tr("Loading templates…", "Vorlagen werden geladen…")
              : `${items.length} ${tr("templates", "Vorlagen")}`}
          </p>
          {query.isError && (
            <div role="alert">
              <p>
                {tr(
                  "Templates could not be loaded.",
                  "Vorlagen konnten nicht geladen werden.",
                )}
              </p>
              <button className="button" onClick={() => query.refetch()}>
                {tr("Try again", "Erneut versuchen")}
              </button>
            </div>
          )}
          {!query.isPending && !query.isError && items.length === 0 && (
            <div className="templates-empty">
              <Layers size={30} aria-hidden />
              <h3>
                {search
                  ? tr("No matching templates", "Keine passenden Vorlagen")
                  : tr(
                      "Your next workflow starts here",
                      "Dein nächster Workflow beginnt hier",
                    )}
              </h3>
              <p>
                {search
                  ? tr(
                      "Try a different search or template type.",
                      "Versuche eine andere Suche oder einen anderen Vorlagentyp.",
                    )
                  : tr(
                      "Create a reusable starting point for your team.",
                      "Erstelle einen wiederverwendbaren Ausgangspunkt für dein Team.",
                    )}
              </p>
            </div>
          )}
          <div className="templates-grid">
            {items.map((item) => {
              const Icon = icons[item.kind];
              return (
                <button
                  className="template-card"
                  disabled={busy}
                  key={item.id}
                  onClick={() => open(item)}
                >
                  <div className="template-card-top">
                    <span className="template-icon">
                      <Icon size={22} aria-hidden />
                    </span>
                    <span>
                      {categoryName(item.content.category)} · v{item.version}
                    </span>
                  </div>
                  <h3>{item.name}</h3>
                  <p>{item.description}</p>
                  <span className="template-card-footer">
                    {tr("Preview template", "Vorlage ansehen")}
                    <ArrowRight size={16} aria-hidden />
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      {(selected || editing) && (
        <section
          className="template-detail"
          aria-labelledby="template-detail-title"
          onKeyDown={(e) => {
            if (e.key === "Escape" && !busy) close();
          }}
        >
          <header>
            <div>
              <p className="templates-eyebrow">
                {kinds[kind]}
                {selected ? ` · v${selected.version}` : ""}
              </p>
              <h2 id="template-detail-title" ref={heading} tabIndex={-1}>
                {editing
                  ? tr("Make it your own", "Individuell gestalten")
                  : selected?.name}
              </h2>
            </div>
            <button
              className="icon-button"
              disabled={busy}
              aria-label={tr("Close preview", "Vorschau schließen")}
              onClick={close}
            >
              <X size={20} />
            </button>
          </header>
          {editing ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate();
              }}
            >
              <label>
                {tr("Template name", "Vorlagenname")}
                <input
                  disabled={busy}
                  required
                  maxLength={160}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label>
                {tr("Description", "Beschreibung")}
                <textarea
                  aria-label={tr("Description", "Beschreibung")}
                  disabled={busy}
                  value={description}
                  maxLength={2000}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
              <label>
                {tr("Template category", "Vorlagenkategorie")}
                <select
                  disabled={busy}
                  value={String(content.category ?? "general")}
                  onChange={(e) =>
                    setContent({ ...content, category: e.target.value })
                  }
                >
                  {TEMPLATE_CATEGORIES.map(([id, en, german]) => (
                    <option key={id} value={id}>
                      {tr(en, german)}
                    </option>
                  ))}
                </select>
              </label>
              {fields[kind].map(([key, en, german]) => (
                <label key={key}>
                  {tr(en, german)}
                  <textarea
                    aria-label={tr(en, german)}
                    disabled={busy}
                    value={String(content[key] ?? "")}
                    onChange={(e) =>
                      setContent({ ...content, [key]: e.target.value })
                    }
                  />
                </label>
              ))}
              {kind === "project" && (
                <label>
                  {tr("Team template", "Teamvorlage")}
                  <select
                    disabled={busy}
                    value={
                      teamReference
                        ? `${teamReference.id}:${teamReference.version}`
                        : ""
                    }
                    onChange={(e) => {
                      const next = { ...content };
                      if (e.target.value) {
                        const [id, version] = e.target.value
                          .split(":")
                          .map(Number);
                        next.team_template = { id, version };
                      } else delete next.team_template;
                      setContent(next);
                    }}
                  >
                    <option value="">
                      {tr("Configure team later", "Team später konfigurieren")}
                    </option>
                    {teamReference && (
                      <option
                        value={`${teamReference.id}:${teamReference.version}`}
                      >
                        {referenceLabel(teamReference)} ·{" "}
                        {tr("pinned", "festgelegt")}
                      </option>
                    )}
                    {query.data?.templates
                      .filter(
                        (item) =>
                          item.kind === "team" &&
                          !item.archived &&
                          (item.id !== teamReference?.id ||
                            item.version !== teamReference?.version),
                      )
                      .map((item) => (
                        <option
                          key={item.id}
                          value={`${item.id}:${item.version}`}
                        >
                          {item.name} · v{item.version}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              {kind === "team" && (
                <fieldset disabled={busy}>
                  <legend>{tr("Role templates", "Rollenvorlagen")}</legend>
                  {query.data?.templates
                    .filter(
                      (item) =>
                        item.kind === "role" &&
                        (!item.archived ||
                          references.some((ref) => ref.id === item.id)),
                    )
                    .map((item) => {
                      const pinned = references.find(
                        (ref) => ref.id === item.id,
                      );
                      return (
                        <div key={item.id}>
                          <label className="template-role-option">
                            <input
                              type="checkbox"
                              checked={!!pinned}
                              onChange={(e) =>
                                setContent({
                                  ...content,
                                  role_templates: e.target.checked
                                    ? [
                                        ...references,
                                        { id: item.id, version: item.version },
                                      ]
                                    : references.filter(
                                        (ref) => ref.id !== item.id,
                                      ),
                                })
                              }
                            />
                            {item.name} · v{pinned?.version ?? item.version}
                            {item.archived
                              ? tr(" · archived", " · archiviert")
                              : ""}
                          </label>
                          {pinned &&
                            pinned.version !== item.version &&
                            !item.archived && (
                              <button
                                type="button"
                                className="button"
                                onClick={() =>
                                  setContent({
                                    ...content,
                                    role_templates: references.map((ref) =>
                                      ref.id === item.id
                                        ? { ...ref, version: item.version }
                                        : ref,
                                    ),
                                  })
                                }
                              >
                                {tr(
                                  "Use latest version",
                                  "Neueste Version verwenden",
                                )}{" "}
                                · v{item.version}
                              </button>
                            )}
                        </div>
                      );
                    })}
                </fieldset>
              )}
              <button
                className="button button-primary"
                disabled={busy || !name.trim()}
              >
                {save.isPending
                  ? tr("Saving…", "Speichern…")
                  : tr("Save template", "Vorlage speichern")}
              </button>
            </form>
          ) : (
            <>
              <p>{selected?.description}</p>
              <dl>
                {fields[kind].map(([key, en, german]) =>
                  selected?.content[key] ? (
                    <div key={key}>
                      <dt>{tr(en, german)}</dt>
                      <dd>{String(selected.content[key])}</dd>
                    </div>
                  ) : null,
                )}
              </dl>
              {selected?.content.team_template != null && (
                <p>
                  {tr("Team", "Team")}:{" "}
                  {referenceLabel(
                    selected.content.team_template as {
                      id: number;
                      version: number;
                    },
                  )}
                </p>
              )}
              {Array.isArray(selected?.content.role_templates) && (
                <p>
                  {tr("Roles", "Rollen")}:{" "}
                  {(
                    selected.content.role_templates as {
                      id: number;
                      version: number;
                    }[]
                  )
                    .map(referenceLabel)
                    .join(" → ")}
                </p>
              )}
              <button
                className="button"
                disabled={busy}
                onClick={() => {
                  setEditing(true);
                  setName(selected!.name);
                  setDescription(selected!.description);
                  setContent(selected!.content);
                }}
              >
                {tr("Edit template", "Vorlage bearbeiten")}
              </button>
              <form
                className="template-use"
                onSubmit={(e) => {
                  e.preventDefault();
                  instantiate.mutate();
                }}
              >
                <h3>
                  {tr("Start from this template", "Mit dieser Vorlage starten")}
                </h3>
                <p>
                  {tr(
                    "Creates independent, editable resources and a saved brief. Assign members and providers before execution. Workflow and review requirements are instructions, not enforced automation. No agents are launched.",
                    "Erstellt unabhängige, bearbeitbare Ressourcen mit Auftrag. Weise vor der Ausführung Mitglieder und Anbieter zu. Ablauf und Prüfkriterien sind Anweisungen, keine erzwungene Automatisierung. Es werden keine Agenten gestartet.",
                  )}
                </p>
                <label>
                  {tr("Instance name", "Instanzname")}
                  <input
                    disabled={busy}
                    required
                    maxLength={160}
                    value={instanceName}
                    onChange={(e) => setInstanceName(e.target.value)}
                  />
                </label>
                <button
                  className="button button-primary"
                  disabled={busy || !instanceName.trim() || !!created}
                >
                  {instantiate.isPending
                    ? tr("Creating…", "Erstellen…")
                    : kind === "project"
                      ? tr(
                          "Create project from template",
                          "Projekt aus Vorlage erstellen",
                        )
                      : kind === "team"
                        ? tr(
                            "Create team from template",
                            "Team aus Vorlage erstellen",
                          )
                        : tr(
                            "Add role from template",
                            "Rolle aus Vorlage hinzufügen",
                          )}
                </button>
              </form>
            </>
          )}
          {(save.isError || instantiate.isError) && (
            <p role="alert">{(save.error ?? instantiate.error)?.message}</p>
          )}
          {created && (
            <div className="template-success" role="status">
              <Check size={18} aria-hidden />
              <span>
                {tr("Created and saved.", "Erstellt und gespeichert.")}{" "}
                <Link
                  to={
                    created.kind === "project"
                      ? `/pm/projects/${created.id}`
                      : created.kind === "team"
                        ? "/pm/teams"
                        : "/pm/roles"
                  }
                >
                  {tr("Open", "Öffnen")} →
                </Link>
              </span>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
