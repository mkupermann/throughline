import * as vscode from "vscode";
import type { ContextPack, RecallResult } from "./apiClient";
import { shapeContext, shapeRecall, type TreeRow } from "./treeLogic";

export type Item = { root: "context" | "recall" } | { row: TreeRow };

export class ThroughlineTree implements vscode.TreeDataProvider<Item> {
  private readonly emitter = new vscode.EventEmitter<Item | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;
  private context: TreeRow[] = [];
  private recall: TreeRow[] = [];

  setPack(pack: ContextPack): void {
    this.context = shapeContext(pack);
    this.emitter.fire(undefined);
  }

  setRecall(result: RecallResult): void {
    this.recall = shapeRecall(result);
    this.emitter.fire(undefined);
  }

  getTreeItem(item: Item): vscode.TreeItem {
    if ("root" in item) {
      const t = new vscode.TreeItem(item.root === "context" ? "Context" : "Last recall", vscode.TreeItemCollapsibleState.Expanded);
      t.contextValue = item.root;
      return t;
    }
    const t = new vscode.TreeItem(item.row.label, vscode.TreeItemCollapsibleState.None);
    t.description = item.row.description;
    if (item.row.noteId !== undefined) {
      t.command = { command: "throughline.openNote", title: "Open note", arguments: [item.row.noteId] };
    }
    return t;
  }

  getChildren(item?: Item): Item[] {
    if (!item) return [{ root: "context" }, { root: "recall" }];
    if ("row" in item) return [];
    return (item.root === "context" ? this.context : this.recall).map((row) => ({ row }));
  }
}
