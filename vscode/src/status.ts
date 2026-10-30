import * as vscode from "vscode";
import { ApiClient } from "./apiClient";
import { McpClient } from "./mcpClient";
import { describe } from "./errors";
import { getProject, getUrl } from "./settings";
import { StatusState, statusText, stateFromError } from "./statusLogic";

export class StatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;

  constructor(private readonly getKey: () => Promise<string | undefined>) {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 0);
    this.item.command = "throughline.showEmbeddingStatus";
    this.item.text = "Throughline: …";
    this.item.show();
  }

  async refresh(): Promise<void> {
    const url = getUrl();
    let state: StatusState;
    try {
      await new McpClient(url, this.getKey).listProjects();
      state = { kind: "connected", project: getProject() };
    } catch (e) {
      state = stateFromError(e, url);
    }
    this.item.text = statusText(state);
    this.item.tooltip = "Click to show the embedding status";
  }

  async showEmbeddingStatus(): Promise<void> {
    try {
      const h = await new ApiClient(getUrl()).health();
      const parts: string[] = [];
      if (h.embedding_target) parts.push(`Embedding target: ${h.embedding_target}`);
      parts.push(`Missing embeddings: ${h.embedding_missing}`);
      void vscode.window.showInformationMessage(`Throughline. ${parts.join(". ")}`);
    } catch (e) {
      void vscode.window.showWarningMessage(`Throughline: ${describe(e)}`);
    }
  }

  dispose(): void {
    this.item.dispose();
  }
}
