import { detectAgents, sendToAgent as deliver, type Agent, type AgentHost } from "./agents";
import type { ApiClient, ContextPack, RecallResult } from "./apiClient";
import { describe, ThroughlineError } from "./errors";
import { conflictsToMarkdown, noteToMarkdown, packToMarkdown } from "./markdown";

export const CATEGORIES = [
  "decision",
  "preference",
  "pattern",
  "insight",
  "error_solution",
  "project_context",
  "workflow",
  "contact",
];

export const SET_KEY = "Set Agent Key";

export interface Ui {
  input(prompt: string): Promise<string | undefined>;
  pick(items: string[], placeholder: string): Promise<string | undefined>;
  info(msg: string): void;
  error(msg: string, actions?: { label: string; run: () => void }[]): void;
  openDocument(markdown: string, title: string): Promise<void>;
  openExternal(url: string): Promise<void>;
  clipboard(text: string): Promise<void>;
  selection(): string | undefined;
}

export interface McpLike {
  call(tool: string, args: object): Promise<string>;
  listProjects(): Promise<string[]>;
}

export interface Settings {
  url(): string;
  project(): string | undefined;
  budget(): number;
  rank(): "fulltext" | "weighted";
}

export interface Sink {
  setPack(p: ContextPack): void;
  setRecall(r: RecallResult): void;
}

export interface Deps {
  api: Pick<ApiClient, "recall" | "context" | "note" | "conflicts">;
  mcp: McpLike;
  ui: Ui;
  settings: Settings;
  tree?: Sink;
  setKey?: () => void;
  agents?: { host: AgentHost; commands(): Promise<string[]> };
}

export function clampBudget(n: number): number {
  if (!Number.isFinite(n)) return 2000;
  return Math.min(32768, Math.max(64, Math.round(n)));
}

function fail(d: Deps, e: unknown): void {
  if (e instanceof ThroughlineError && e.kind === "no_key") {
    d.ui.error(describe(e), [{ label: SET_KEY, run: () => d.setKey?.() }]);
  } else {
    d.ui.error(describe(e));
  }
}

async function guarded(d: Deps, f: () => Promise<void>): Promise<void> {
  try {
    await f();
  } catch (e) {
    fail(d, e);
  }
}

/** The configured project, or a choice made by the user. Undefined when cancelled. */
export async function chooseProject(d: Deps, viaMcp = false): Promise<string | undefined> {
  const p = d.settings.project();
  if (p) return p;
  // Display commands never touch /mcp; only agent commands may list projects there.
  let names: string[] = [];
  if (viaMcp) {
    try {
      names = await d.mcp.listProjects();
    } catch (e) {
      // A missing key is the user's to fix; do not hide it behind a name prompt.
      if (e instanceof ThroughlineError && e.kind === "no_key") throw e;
      names = [];
    }
  }
  const chosen = names.length ? await d.ui.pick(names, "Choose a Throughline project") : await d.ui.input("Throughline project name");
  return chosen?.trim() || undefined;
}

export function recall(d: Deps, query?: string): Promise<void> {
  return guarded(d, async () => {
    const q = (query ?? (await d.ui.input("Recall: what do you want to find?")))?.trim();
    if (!q) return;
    const project = d.settings.project();
    const r = await d.api.recall(q, { project, rank: d.settings.rank() });
    d.tree?.setRecall(r);
    d.ui.info(`${r.hits.length} note(s) found. See the Throughline view.`);
  });
}

export function projectContext(d: Deps): Promise<void> {
  return guarded(d, async () => {
    const project = await chooseProject(d);
    if (!project) return;
    const pack = await d.api.context(project, { budget: clampBudget(d.settings.budget()) });
    d.tree?.setPack(pack);
    await d.ui.openDocument(packToMarkdown(pack, project), `Throughline context: ${project}`);
  });
}

export function openNote(d: Deps, id?: number | string): Promise<void> {
  return guarded(d, async () => {
    const raw = id ?? (await d.ui.input("Note id"));
    if (raw === undefined) return;
    const s = String(raw).trim();
    if (!/^\d+$/.test(s)) {
      d.ui.error("A note id is a whole number");
      return;
    }
    const note = await d.api.note(Number(s));
    await d.ui.openDocument(noteToMarkdown(note), `Throughline note ${note.id}`);
  });
}

export function conflicts(d: Deps): Promise<void> {
  return guarded(d, async () => {
    const r = await d.api.conflicts();
    await d.ui.openDocument(conflictsToMarkdown(r), "Throughline conflicts");
  });
}

export function openUi(d: Deps): Promise<void> {
  return guarded(d, () => d.ui.openExternal(d.settings.url()));
}

async function remember(d: Deps, content: string): Promise<void> {
  const category = await d.ui.pick(CATEGORIES, "Category");
  if (!category) return;
  const project = await chooseProject(d, true);
  if (!project) return;
  await d.mcp.call("write", { content, category, project });
  d.ui.info("Saved to Throughline.");
}

export function rememberSelection(d: Deps): Promise<void> {
  return guarded(d, async () => {
    const sel = d.ui.selection();
    if (!sel || !sel.trim()) {
      d.ui.error("Select text first");
      return;
    }
    await remember(d, sel);
  });
}

export function quickNote(d: Deps): Promise<void> {
  return guarded(d, async () => {
    const text = (await d.ui.input("Note text"))?.trim();
    if (!text) return;
    await remember(d, text);
  });
}

/** Fetches the context pack over /mcp (counts as agent use). Undefined when the project choice is cancelled. */
async function fetchPack(d: Deps): Promise<string | undefined> {
  const project = await chooseProject(d, true);
  if (!project) return undefined;
  const task = (await d.ui.input("Task (optional)"))?.trim();
  const args: { project: string; task?: string; budget: number } = { project, budget: clampBudget(d.settings.budget()) };
  if (task) args.task = task;
  return d.mcp.call("project_context", args);
}

export function copyContext(d: Deps): Promise<void> {
  return guarded(d, async () => {
    const text = await fetchPack(d);
    if (text === undefined) return;
    await d.ui.clipboard(text);
    d.ui.info("Context copied to the clipboard.");
  });
}

const LABELS: Record<Agent, string> = { cline: "Cline", continue: "Continue", clipboard: "Clipboard" };

export function sendToAgent(d: Deps): Promise<void> {
  return guarded(d, async () => {
    if (!d.agents) return;
    const text = await fetchPack(d);
    if (text === undefined) return;
    const found = detectAgents(await d.agents.commands());
    const real = found.filter((a) => a !== "clipboard");
    let agent: Agent = "clipboard";
    if (real.length === 1) agent = real[0];
    else if (real.length > 1) {
      const picked = await d.ui.pick(found.map((a) => LABELS[a]), "Send the context to");
      if (!picked) return;
      const match = found.find((a) => LABELS[a] === picked);
      if (!match) return;
      agent = match;
    }
    const result = await deliver(agent, text, d.agents.host);
    // The "copied" paths already told the user through the host.
    if (result === "sent") d.ui.info("Context sent to Cline.");
  });
}
