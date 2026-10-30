import * as vscode from "vscode";
import { KEY_NAME } from "./settings";
import { StatusBar } from "./status";

export function activate(context: vscode.ExtensionContext): void {
  const status = new StatusBar(() => Promise.resolve(context.secrets.get(KEY_NAME)));
  context.subscriptions.push(
    status,
    vscode.commands.registerCommand("throughline.setKey", async () => {
      const input = await vscode.window.showInputBox({
        password: true,
        ignoreFocusOut: true,
        prompt: "Throughline agent key (stored in VS Code secret storage)",
      });
      if (input && input.trim()) {
        await context.secrets.store(KEY_NAME, input.trim());
      }
      await status.refresh();
    }),
    vscode.commands.registerCommand("throughline.refreshStatus", () => status.refresh()),
    vscode.commands.registerCommand("throughline.showEmbeddingStatus", () => status.showEmbeddingStatus()),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("throughline")) void status.refresh();
    }),
  );
  void status.refresh();
}

export function deactivate(): void {}
