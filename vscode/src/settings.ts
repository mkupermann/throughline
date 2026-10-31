import * as vscode from "vscode";
import { resolveProject } from "./statusLogic";
import { DEFAULT_URL, validateUrl } from "./validate";
import { rankFromSetting, type Rank } from "./apiClient";

export const KEY_NAME = "throughline.agentKey";

function cfg(scope?: vscode.ConfigurationScope) {
  return vscode.workspace.getConfiguration("throughline", scope);
}

let warnedFor: string | undefined;

/** The configured address if it is http or https, otherwise the default, with one warning per bad value. */
export function getUrl(): string {
  const raw = cfg().get<string>("url", DEFAULT_URL).trim();
  if (!raw) return DEFAULT_URL;
  const url = validateUrl(raw);
  if (url) return url;
  if (warnedFor !== raw) {
    warnedFor = raw;
    void vscode.window.showWarningMessage(`Throughline: throughline.url must be an http or https address. Using ${DEFAULT_URL}.`);
  }
  return DEFAULT_URL;
}

export function getProject(folder?: vscode.WorkspaceFolder): string | undefined {
  const f = folder ?? vscode.workspace.workspaceFolders?.[0];
  return resolveProject(f?.name, cfg(f?.uri).get<string>("project", ""));
}

export function getBudget(): number {
  return cfg().get<number>("contextBudget", 2000);
}

/** The configured ranking; `undefined` sends none, so the server's default applies. */
export function getRank(): Rank | undefined {
  return rankFromSetting(cfg().get<string>("rank", "default"));
}
