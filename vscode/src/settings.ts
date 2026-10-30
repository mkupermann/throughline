import * as vscode from "vscode";
import { resolveProject } from "./statusLogic";

export const KEY_NAME = "throughline.agentKey";

function cfg(scope?: vscode.ConfigurationScope) {
  return vscode.workspace.getConfiguration("throughline", scope);
}

export function getUrl(): string {
  return cfg().get<string>("url", "http://127.0.0.1:8788").trim() || "http://127.0.0.1:8788";
}

export function getProject(folder?: vscode.WorkspaceFolder): string | undefined {
  const f = folder ?? vscode.workspace.workspaceFolders?.[0];
  return resolveProject(f?.name, cfg(f?.uri).get<string>("project", ""));
}

export function getBudget(): number {
  return cfg().get<number>("contextBudget", 2000);
}

export function getRank(): "fulltext" | "weighted" {
  return cfg().get<string>("rank", "fulltext") === "weighted" ? "weighted" : "fulltext";
}
