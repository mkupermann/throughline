import * as vscode from "vscode";
import { KEY_NAME } from "./settings";
import { ApiClient } from "./apiClient";
import * as cmd from "./commands";
import { McpClient } from "./mcpClient";
import { getBudget, getProject, getRank, getUrl } from "./settings";
import { StatusBar } from "./status";
import { ThroughlineTree } from "./tree";
import { validateKey } from "./validate";

export function activate(context: vscode.ExtensionContext): void {
  const status = new StatusBar(() => Promise.resolve(context.secrets.get(KEY_NAME)));
  const tree = new ThroughlineTree();
  const ui: cmd.Ui = {
    input: (prompt) => Promise.resolve(vscode.window.showInputBox({ prompt, ignoreFocusOut: true })),
    pick: (items, placeHolder) => Promise.resolve(vscode.window.showQuickPick(items, { placeHolder })),
    info: (m) => void vscode.window.showInformationMessage(m),
    error: (m, actions = []) => {
      void vscode.window.showErrorMessage(m, ...actions.map((a) => a.label)).then((picked) => {
        actions.find((a) => a.label === picked)?.run();
      });
    },
    openDocument: async (content, _title) => {
      const doc = await vscode.workspace.openTextDocument({ language: "markdown", content });
      await vscode.window.showTextDocument(doc);
    },
    openExternal: async (url) => {
      await vscode.env.openExternal(vscode.Uri.parse(url));
    },
    clipboard: (t) => Promise.resolve(vscode.env.clipboard.writeText(t)),
    selection: () => {
      const ed = vscode.window.activeTextEditor;
      return ed ? ed.document.getText(ed.selection) : undefined;
    },
  };
  let scratch: vscode.TextEditor | undefined;
  // Clients are built per call so a changed url setting applies at once.
  const deps = (): cmd.Deps => ({
    api: new ApiClient(getUrl()),
    mcp: new McpClient(getUrl(), () => Promise.resolve(context.secrets.get(KEY_NAME))),
    ui,
    settings: { url: getUrl, project: () => getProject(), budget: getBudget, rank: getRank },
    tree,
    setKey: () => void vscode.commands.executeCommand("throughline.setKey"),
    agents: {
      commands: () => Promise.resolve(vscode.commands.getCommands(true)),
      host: {
        executeCommand: (id, ...args) => Promise.resolve(vscode.commands.executeCommand(id, ...args)),
        openScratchDocument: async (content) => {
          const doc = await vscode.workspace.openTextDocument({ language: "markdown", content });
          scratch = await vscode.window.showTextDocument(doc);
        },
        // Selects through the scratch editor itself, so the selection cannot land in another editor.
        selectAll: async () => {
          if (!scratch) return;
          const doc = scratch.document;
          scratch.selection = new vscode.Selection(doc.positionAt(0), doc.positionAt(doc.getText().length));
        },
        closeScratchDocument: async () => {
          // The untitled scratch document has content, so it is dirty; reverting first avoids a save prompt.
          if (scratch && vscode.window.activeTextEditor?.document === scratch.document) {
            await vscode.commands.executeCommand("workbench.action.revertAndCloseActiveEditor");
          }
          scratch = undefined;
        },
        clipboard: (t) => Promise.resolve(vscode.env.clipboard.writeText(t)),
        info: ui.info,
      },
    },
  });
  context.subscriptions.push(
    status,
    vscode.window.registerTreeDataProvider("throughline.memories", tree),
    vscode.commands.registerCommand("throughline.recall", () => cmd.recall(deps())),
    vscode.commands.registerCommand("throughline.projectContext", () => cmd.projectContext(deps())),
    vscode.commands.registerCommand("throughline.openNote", (id?: number) => cmd.openNote(deps(), id)),
    vscode.commands.registerCommand("throughline.conflicts", () => cmd.conflicts(deps())),
    vscode.commands.registerCommand("throughline.openUi", () => cmd.openUi(deps())),
    vscode.commands.registerCommand("throughline.rememberSelection", () => cmd.rememberSelection(deps())),
    vscode.commands.registerCommand("throughline.quickNote", () => cmd.quickNote(deps())),
    vscode.commands.registerCommand("throughline.copyContext", () => cmd.copyContext(deps())),
    vscode.commands.registerCommand("throughline.sendToAgent", () => cmd.sendToAgent(deps())),
    vscode.commands.registerCommand("throughline.setKey", async () => {
      const input = await vscode.window.showInputBox({
        password: true,
        ignoreFocusOut: true,
        prompt: "Throughline agent key (stored in VS Code secret storage)",
      });
      const key = input?.trim();
      if (key) {
        if (!validateKey(key)) {
          void vscode.window.showErrorMessage("The key may only contain printable ASCII characters.");
          return;
        }
        await context.secrets.store(KEY_NAME, key);
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
