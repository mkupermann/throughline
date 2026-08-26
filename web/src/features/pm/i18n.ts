/** Bilingual (Deutsch/English) strings for the PM ("Leitstand") surface.
 *
 * A hand-rolled dictionary rather than a library: the PM surface is a
 * handful of pages, and a typed `{ de, en }` object with a `useLang()` hook
 * covers it without adding a dependency. `en` is typed as `typeof de`, so a
 * missing or mismatched key is a compile error, not a runtime gap.
 *
 * Numbers, dates and relative times follow the language of the words around
 * them (see shared.tsx's formatting helpers) — the same rule lib/format.ts
 * applies for the host app's English-only surfaces, now switched per
 * language instead of fixed to one locale.
 */

import { useCallback, useEffect, useState } from "react";

import type { PmProject, PmTaskStatus } from "@/lib/api";

export type Lang = "de" | "en";

const STORAGE_KEY = "pm-lang";

// ── Dictionary ───────────────────────────────────────────────────────────

const de = {
  common: {
    retry: "Erneut versuchen",
    cancel: "Abbrechen",
    save: "Speichern",
    saving: "Speichert…",
    edit: "Bearbeiten",
    close: "Schließen",
    add: "Hinzufügen",
    remove: "Entfernen",
    unlimited: "unbegrenzt",
    tokens: "Tokens",
    path: "Pfad",
    skillOne: "Skill",
    skillMany: "Skills",
    documentOne: "Dokument",
    documentMany: "Dokumente",
    projectManagement: "Project Management",
  },
  status: {
    task: {
      pending: "ausstehend",
      running: "läuft",
      pass: "bestanden",
      fail: "fehlgeschlagen",
      budget_exceeded: "Budget erschöpft",
      crashed: "abgestürzt",
      stopped: "gestoppt",
    } satisfies Record<PmTaskStatus, string>,
    project: {
      active: "aktiv",
      paused: "pausiert",
      completed: "abgeschlossen",
      archived: "archiviert",
    } satisfies Record<PmProject["status"], string>,
  },
  budget: {
    noBudgetSet: "kein Budget gesetzt",
    usedOfLabel: (used: string, budget: string) => `${used} von ${budget} Tokens verbraucht`,
    label: "Budget",
  },
  breadcrumb: {
    project: "Projekt",
    task: "Task",
    roles: "Rollen",
    members: "Mitglieder",
    teams: "Teams",
  },
  skillPicker: {
    loadError: "Skills können nicht geladen werden.",
    removeTitle: "Skill entfernen",
    searchPlaceholder: "Skills durchsuchen…",
    searchLabel: "Skills durchsuchen",
    none: "Keine Skills gefunden.",
    more: (shown: string, total: string) => `${shown} von ${total} Treffern — Suche eingrenzen.`,
  },
  docList: {
    removeAria: (doc: string) => `${doc} entfernen`,
    remove: "Entfernen",
    placeholder: "Pfad zu einem Dokument",
    add: "Hinzufügen",
  },
  dashboard: {
    subtitle: "Virtuelle Teams, Pipelines und Budgets im Blick.",
    createProject: "Projekt anlegen",
    nameLabel: "Name",
    namePlaceholder: "z. B. Demoscene Tribute",
    budgetLabel: "Token-Budget (optional)",
    createSubmit: "Anlegen",
    createFailed: (msg: string) => `Projekt konnte nicht angelegt werden: ${msg}`,
    catalogGroupLabel: "Kataloge",
    catalogRoles: "Rollen",
    catalogMembers: "Mitglieder",
    catalogTeams: "Teams",
    emptyTitle: "Noch keine Projekte",
    emptyBody: "Ein Projekt bündelt Teams, Tasks und Budgets. Lege das erste an, um zu starten.",
    errorTitle: "Übersicht kann nicht geladen werden",
    noTasksYet: "Noch keine Tasks",
    teamOne: "Team",
    teamMany: "Teams",
    activity: (rel: string) => `Aktivität ${rel}`,
  },
  cockpit: {
    breadcrumbFallback: "Projekt",
    notFoundTitle: "Projekt nicht gefunden",
    notFoundBody: "Unter dieser Adresse liegt kein Projekt. Zurück zur Übersicht, um eines auszuwählen.",
    errorTitle: "Projekt kann nicht geladen werden",
    statusLabel: "Status:",
    taskOne: "Task",
    taskMany: "Tasks",
    teamOne: "Team",
    teamMany: "Teams",
    editBudget: "Bearbeiten",
    budgetAriaSuffix: "in Tokens",
  },
  teams: {
    h2: "Teams",
    errorTitle: "Teams können nicht geladen werden",
    emptyTitle: "Noch kein Team verknüpft",
    emptyBody: "Ein Team bringt Rollen und Mitglieder in eine Pipeline. Unten verknüpfen oder anlegen.",
    pipelineEmpty: "Noch keine Rollen im Team — unten eine Rolle verknüpfen, damit die Pipeline Sitze bekommt.",
    taskRunningOne: "Task läuft",
    taskRunningMany: "Tasks laufen",
    liveDotLabel: "Task läuft",
    seat: {
      unassigned: "Nicht besetzt",
      memberFallback: (id: number) => `Mitglied ${id}`,
      noAiTool: "Kein KI-Werkzeug gesetzt",
      pickPlaceholder: "Mitglied zuweisen…",
      pickAria: (role: string) => `Mitglied für ${role} zuweisen`,
      removeTitle: (member: string) => `${member} entfernen`,
      removeAria: (member: string, role: string) => `${member} als ${role} entfernen`,
      assignError: (msg: string) => `Zuweisung fehlgeschlagen: ${msg}`,
      removeError: (msg: string) => `Entfernen fehlgeschlagen: ${msg}`,
    },
    linkRole: {
      srLabel: (team: string) => `Rolle für ${team}`,
      placeholder: "Rolle verknüpfen…",
      submit: "Verknüpfen",
      error: (msg: string) => `Verknüpfen fehlgeschlagen: ${msg}`,
    },
    linkTeam: {
      srExisting: "Bestehendes Team",
      existingPlaceholder: "Team verknüpfen…",
      submit: "Verknüpfen",
      srNewName: "Name des neuen Teams",
      newNamePlaceholder: "Name des neuen Teams",
      createAndLink: "Anlegen und verknüpfen",
      createNew: "Neues Team anlegen",
      cancel: "Abbrechen",
      error: (msg: string) => msg,
    },
  },
  tasksSection: {
    h2: "Tasks",
    teamLabel: "Team",
    teamPlaceholder: "Team wählen…",
    titleLabel: "Titel",
    titlePlaceholder: "Was soll das Team bauen?",
    repoLabel: "Repo-Pfad",
    repoPlaceholder: "C:\\Pfad\\zum\\Repository",
    launch: "Task starten",
    launching: "Startet…",
    launchFailed: (msg: string) => `Start fehlgeschlagen: ${msg}`,
    registerSummary: "Bestehenden Lauf registrieren",
    registerHint: "Übernimmt einen bereits laufenden pipeline.sh-Lauf. Team, Titel und Repo-Pfad oben ausfüllen, dazu die Run-ID (der Ordnername unter",
    runIdLabel: "Run-ID",
    runIdPlaceholder: "z. B. 20260825-184848",
    registerSubmit: "Lauf registrieren",
    registering: "Registriert…",
    registerFailed: (msg: string) => `Registrieren fehlgeschlagen: ${msg}`,
    errorTitle: "Tasks können nicht geladen werden",
    emptyTitle: "Noch keine Tasks",
    emptyBody: "Oben ein Team wählen, Titel und Repo-Pfad angeben und den ersten Task starten.",
    filterGroupLabel: "Nach Status filtern",
    filterAll: (n: string) => `Alle (${n})`,
    noneWithStatus: "Kein Task mit diesem Status.",
    startedAt: (rel: string) => `gestartet ${rel}`,
    endedAt: (rel: string) => `beendet ${rel}`,
  },
  taskPage: {
    breadcrumbFallback: "Task",
    errorTitle: "Task kann nicht geladen werden",
    stop: "Task stoppen",
    stopping: "Stoppt…",
    stopFailed: (msg: string) => `Stoppen fehlgeschlagen: ${msg}`,
    startedAt: (rel: string) => `gestartet ${rel}`,
    endedAt: (rel: string) => `beendet ${rel}`,
    createdAt: (rel: string) => `angelegt ${rel}`,
    runLabel: "Run",
    teamBudgetLabel: "Team-Budget",
    projectBudgetLabel: "Projekt-Budget",
    verdictPass: "BESTANDEN",
    verdictFail: "ABGELEHNT",
    logLoading: "Log lädt",
    logError: "Log-Auszug kann nicht geladen werden (Server meldet einen Fehler für diese Iteration).",
    logCaption: (n: number) => `Letzte 200 Zeilen von executor-${n}.log`,
    logEmpty: "(leer)",
    iteration: (n: number) => `Iteration ${n}`,
    tokensUnknown: "Tokens unbekannt",
    logHide: "Log ausblenden",
    logShow: "Log ansehen",
    specSummary: "Spezifikation (SPEC.md)",
    noteLabel: "Hinweis:",
    errorNoMessage: "Fehler ohne Meldung",
    iterH2: "Iterationen",
    iterErrorTitle: "Iterationen können nicht geladen werden",
    noIterations: "Noch keine Iteration aufgezeichnet",
    noIterationsRunning: " — die Pipeline läuft an, die Seite aktualisiert sich selbst.",
  },
  catalog: {
    noAiTool: "Kein KI-Werkzeug gesetzt",
    noBudget: "Kein Budget",
    budgetTokens: (n: string) => `${n} Tokens Budget`,
    instructionsSet: "Anweisungen gesetzt",
    noInstructions: "Keine Anweisungen",
    saveChanges: "Änderungen speichern",
  },
  rolesPage: {
    h1: "Rollen",
    subtitle: "Eine Rolle bündelt KI-Werkzeug, Skills, Anweisungen und Budget für einen Sitz in der Pipeline.",
    create: "Rolle anlegen",
    errorTitle: "Rollen können nicht geladen werden",
    emptyTitle: "Noch keine Rollen",
    emptyBody: "Rollen definieren die Sitze einer Team-Pipeline — oben die erste anlegen.",
  },
  membersPage: {
    h1: "Mitglieder",
    subtitle: "Menschen und Agenten, die in der Team-Pipeline eine Rolle besetzen.",
    create: "Mitglied anlegen",
    errorTitle: "Mitglieder können nicht geladen werden",
    emptyTitle: "Noch keine Mitglieder",
    emptyBody: "Mitglieder besetzen die Sitze der Pipeline — oben das erste anlegen.",
    typeAgent: "Agent",
    typeHuman: "Mensch",
  },
  teamsPage: {
    h1: "Teams",
    subtitle: "Ein Team bündelt Rollen, Budget und Pipeline — unabhängig von einem Projekt anlegbar, in einem oder mehreren Projekten einsetzbar.",
    create: "Team anlegen",
    errorTitle: "Teams können nicht geladen werden",
    emptyTitle: "Noch keine Teams",
    emptyBody: "Ein Team lässt sich hier anlegen, bevor es einem Projekt zugeordnet wird — oben das erste anlegen.",
    namePlaceholder: "z. B. Kernteam",
  },
  aiPicker: {
    toolNone: "— kein Werkzeug —",
    modelNone: "— kein Modell —",
    storedOption: (v: string) => `(gespeichert: ${v})`,
    loadError: "KI-Katalog kann nicht geladen werden.",
    ollamaUnavailable: "Ollama nicht erreichbar — Modelle können nicht geladen werden.",
    toolUnavailable: (label: string) => `${label} nicht erreichbar — Modelle können nicht geladen werden.`,
  },
  forms: {
    nameLabel: "Name",
    roleNamePlaceholder: "z. B. Analyst",
    memberNamePlaceholder: "z. B. Claude Code",
    teamNamePlaceholder: "z. B. Kernteam",
    descriptionLabel: "Beschreibung",
    descriptionPlaceholder: "Wofür diese Rolle zuständig ist",
    aiToolLabel: "KI-Werkzeug",
    aiModelLabel: "KI-Modell",
    budgetLabel: "Token-Budget",
    skillsLabel: "Skills",
    instructionsLabel: "Anweisungen (Prompt)",
    roleInstructionsPlaceholder: "Mandat der Rolle — wird dem Agenten beim Start mitgegeben.",
    memberInstructionsPlaceholder: "Individuelle Arbeitsweise — wird an das Rollen-Mandat angehängt.",
    documentsLabel: "Dokumente",
    typeLabel: "Typ",
    contactLabel: "Kontakt",
    contactPlaceholder: "E-Mail, Handle o. Ä.",
    cancel: "Abbrechen",
    saveFailed: (msg: string) => `Speichern fehlgeschlagen: ${msg}`,
  },
} satisfies Record<string, unknown>;

const en: typeof de = {
  common: {
    retry: "Retry",
    cancel: "Cancel",
    save: "Save",
    saving: "Saving…",
    edit: "Edit",
    close: "Close",
    add: "Add",
    remove: "Remove",
    unlimited: "unlimited",
    tokens: "tokens",
    path: "Path",
    skillOne: "skill",
    skillMany: "skills",
    documentOne: "document",
    documentMany: "documents",
    projectManagement: "Project Management",
  },
  status: {
    task: {
      pending: "pending",
      running: "running",
      pass: "passed",
      fail: "failed",
      budget_exceeded: "budget exhausted",
      crashed: "crashed",
      stopped: "stopped",
    },
    project: {
      active: "active",
      paused: "paused",
      completed: "completed",
      archived: "archived",
    },
  },
  budget: {
    noBudgetSet: "no budget set",
    usedOfLabel: (used: string, budget: string) => `${used} of ${budget} tokens used`,
    label: "Budget",
  },
  breadcrumb: {
    project: "Project",
    task: "Task",
    roles: "Roles",
    members: "Members",
    teams: "Teams",
  },
  skillPicker: {
    loadError: "Skills cannot be loaded.",
    removeTitle: "Remove skill",
    searchPlaceholder: "Search skills…",
    searchLabel: "Search skills",
    none: "No skills found.",
    more: (shown: string, total: string) => `${shown} of ${total} matches — narrow the search.`,
  },
  docList: {
    removeAria: (doc: string) => `Remove ${doc}`,
    remove: "Remove",
    placeholder: "Path to a document",
    add: "Add",
  },
  dashboard: {
    subtitle: "Virtual teams, pipelines and budgets at a glance.",
    createProject: "Create project",
    nameLabel: "Name",
    namePlaceholder: "e.g. Demoscene Tribute",
    budgetLabel: "Token budget (optional)",
    createSubmit: "Create",
    createFailed: (msg: string) => `Project could not be created: ${msg}`,
    catalogGroupLabel: "Catalogs",
    catalogRoles: "Roles",
    catalogMembers: "Members",
    catalogTeams: "Teams",
    emptyTitle: "No projects yet",
    emptyBody: "A project bundles teams, tasks and budgets. Create the first one to get started.",
    errorTitle: "Overview cannot be loaded",
    noTasksYet: "No tasks yet",
    teamOne: "team",
    teamMany: "teams",
    activity: (rel: string) => `Activity ${rel}`,
  },
  cockpit: {
    breadcrumbFallback: "Project",
    notFoundTitle: "Project not found",
    notFoundBody: "No project lives at this address. Go back to the overview to pick one.",
    errorTitle: "Project cannot be loaded",
    statusLabel: "Status:",
    taskOne: "task",
    taskMany: "tasks",
    teamOne: "team",
    teamMany: "teams",
    editBudget: "Edit",
    budgetAriaSuffix: "in tokens",
  },
  teams: {
    h2: "Teams",
    errorTitle: "Teams cannot be loaded",
    emptyTitle: "No team linked yet",
    emptyBody: "A team brings roles and members into a pipeline. Link or create one below.",
    pipelineEmpty: "No roles in this team yet — link a role below to give the pipeline seats.",
    taskRunningOne: "task running",
    taskRunningMany: "tasks running",
    liveDotLabel: "Task running",
    seat: {
      unassigned: "Unassigned",
      memberFallback: (id: number) => `Member ${id}`,
      noAiTool: "No AI tool set",
      pickPlaceholder: "Assign member…",
      pickAria: (role: string) => `Assign a member to ${role}`,
      removeTitle: (member: string) => `Remove ${member}`,
      removeAria: (member: string, role: string) => `Remove ${member} as ${role}`,
      assignError: (msg: string) => `Assignment failed: ${msg}`,
      removeError: (msg: string) => `Removal failed: ${msg}`,
    },
    linkRole: {
      srLabel: (team: string) => `Role for ${team}`,
      placeholder: "Link a role…",
      submit: "Link",
      error: (msg: string) => `Linking failed: ${msg}`,
    },
    linkTeam: {
      srExisting: "Existing team",
      existingPlaceholder: "Link a team…",
      submit: "Link",
      srNewName: "Name of the new team",
      newNamePlaceholder: "Name of the new team",
      createAndLink: "Create and link",
      createNew: "Create new team",
      cancel: "Cancel",
      error: (msg: string) => msg,
    },
  },
  tasksSection: {
    h2: "Tasks",
    teamLabel: "Team",
    teamPlaceholder: "Choose a team…",
    titleLabel: "Title",
    titlePlaceholder: "What should the team build?",
    repoLabel: "Repo path",
    repoPlaceholder: "C:\\path\\to\\repository",
    launch: "Start task",
    launching: "Starting…",
    launchFailed: (msg: string) => `Start failed: ${msg}`,
    registerSummary: "Register an existing run",
    registerHint: "Adopts an already-running pipeline.sh run. Fill in team, title and repo path above, plus the run ID (the folder name under",
    runIdLabel: "Run ID",
    runIdPlaceholder: "e.g. 20260825-184848",
    registerSubmit: "Register run",
    registering: "Registering…",
    registerFailed: (msg: string) => `Registration failed: ${msg}`,
    errorTitle: "Tasks cannot be loaded",
    emptyTitle: "No tasks yet",
    emptyBody: "Choose a team above, give a title and repo path, and start the first task.",
    filterGroupLabel: "Filter by status",
    filterAll: (n: string) => `All (${n})`,
    noneWithStatus: "No task with this status.",
    startedAt: (rel: string) => `started ${rel}`,
    endedAt: (rel: string) => `ended ${rel}`,
  },
  taskPage: {
    breadcrumbFallback: "Task",
    errorTitle: "Task cannot be loaded",
    stop: "Stop task",
    stopping: "Stopping…",
    stopFailed: (msg: string) => `Stopping failed: ${msg}`,
    startedAt: (rel: string) => `started ${rel}`,
    endedAt: (rel: string) => `ended ${rel}`,
    createdAt: (rel: string) => `created ${rel}`,
    runLabel: "Run",
    teamBudgetLabel: "Team budget",
    projectBudgetLabel: "Project budget",
    verdictPass: "PASSED",
    verdictFail: "REJECTED",
    logLoading: "Log loading",
    logError: "Log excerpt cannot be loaded (the server reports an error for this iteration).",
    logCaption: (n: number) => `Last 200 lines of executor-${n}.log`,
    logEmpty: "(empty)",
    iteration: (n: number) => `Iteration ${n}`,
    tokensUnknown: "Tokens unknown",
    logHide: "Hide log",
    logShow: "Show log",
    specSummary: "Specification (SPEC.md)",
    noteLabel: "Note:",
    errorNoMessage: "Error with no message",
    iterH2: "Iterations",
    iterErrorTitle: "Iterations cannot be loaded",
    noIterations: "No iteration recorded yet",
    noIterationsRunning: " — the pipeline is starting up, this page updates itself.",
  },
  catalog: {
    noAiTool: "No AI tool set",
    noBudget: "No budget",
    budgetTokens: (n: string) => `Budget: ${n} tokens`,
    instructionsSet: "Instructions set",
    noInstructions: "No instructions",
    saveChanges: "Save changes",
  },
  rolesPage: {
    h1: "Roles",
    subtitle: "A role bundles AI tool, skills, instructions and budget for one seat in the pipeline.",
    create: "Create role",
    errorTitle: "Roles cannot be loaded",
    emptyTitle: "No roles yet",
    emptyBody: "Roles define a team pipeline's seats — create the first one above.",
  },
  membersPage: {
    h1: "Members",
    subtitle: "People and agents who fill a role in a team pipeline.",
    create: "Create member",
    errorTitle: "Members cannot be loaded",
    emptyTitle: "No members yet",
    emptyBody: "Members fill the pipeline's seats — create the first one above.",
    typeAgent: "Agent",
    typeHuman: "Human",
  },
  teamsPage: {
    h1: "Teams",
    subtitle: "A team bundles roles, budget and pipeline — creatable independently of a project, usable in one or several projects.",
    create: "Create team",
    errorTitle: "Teams cannot be loaded",
    emptyTitle: "No teams yet",
    emptyBody: "A team can be created here before it is linked to a project — create the first one above.",
    namePlaceholder: "e.g. Core team",
  },
  aiPicker: {
    toolNone: "— no tool —",
    modelNone: "— no model —",
    storedOption: (v: string) => `(saved: ${v})`,
    loadError: "AI catalog cannot be loaded.",
    ollamaUnavailable: "Ollama is unreachable — models cannot be loaded.",
    toolUnavailable: (label: string) => `${label} is unreachable — models cannot be loaded.`,
  },
  forms: {
    nameLabel: "Name",
    roleNamePlaceholder: "e.g. Analyst",
    memberNamePlaceholder: "e.g. Claude Code",
    teamNamePlaceholder: "e.g. Core team",
    descriptionLabel: "Description",
    descriptionPlaceholder: "What this role is responsible for",
    aiToolLabel: "AI tool",
    aiModelLabel: "AI model",
    budgetLabel: "Token budget",
    skillsLabel: "Skills",
    instructionsLabel: "Instructions (prompt)",
    roleInstructionsPlaceholder: "The role's mandate — passed to the agent when it starts.",
    memberInstructionsPlaceholder: "Individual working style — appended to the role's mandate.",
    documentsLabel: "Documents",
    typeLabel: "Type",
    contactLabel: "Contact",
    contactPlaceholder: "Email, handle, etc.",
    cancel: "Cancel",
    saveFailed: (msg: string) => `Save failed: ${msg}`,
  },
};

export type Dict = typeof de;

const DICT: Record<Lang, Dict> = { de, en };

// ── Language state ──────────────────────────────────────────────────────
// Module-level, with a tiny pub/sub: every component using useLang() re-
// renders when the language changes anywhere, without a context provider.

function readStored(): Lang {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "de" || v === "en") return v;
  } catch {
    // localStorage unavailable (private mode, disabled storage, …) — fall
    // back to the default silently.
  }
  return "de";
}

let currentLang: Lang | null = null;
const listeners = new Set<(l: Lang) => void>();

function getLang(): Lang {
  if (currentLang === null) currentLang = readStored();
  return currentLang;
}

function setGlobalLang(l: Lang) {
  currentLang = l;
  try {
    localStorage.setItem(STORAGE_KEY, l);
  } catch {
    // Best effort — the in-memory state still switches for this session.
  }
  listeners.forEach((fn) => fn(l));
}

export { getLang };

export function useLang(): { lang: Lang; t: Dict; setLang: (l: Lang) => void; toggle: () => void } {
  const [lang, setLangState] = useState<Lang>(getLang);

  useEffect(() => {
    const listener = (l: Lang) => setLangState(l);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const setLang = useCallback((l: Lang) => setGlobalLang(l), []);
  const toggle = useCallback(() => setGlobalLang(lang === "de" ? "en" : "de"), [lang]);

  return { lang, t: DICT[lang], setLang, toggle };
}
