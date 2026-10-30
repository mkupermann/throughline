import type { ContextPack, RecallResult } from "./apiClient";
import { citeText } from "./markdown";

export const CAP = 200;

export interface TreeRow {
  label: string;
  description?: string;
  noteId?: number;
}

function cap(rows: TreeRow[]): TreeRow[] {
  if (rows.length <= CAP) return rows;
  return [...rows.slice(0, CAP), { label: `… and ${rows.length - CAP} more` }];
}

const short = (s: string) => s.replace(/\s+/g, " ").trim().slice(0, 80);

export function shapeContext(pack: ContextPack): TreeRow[] {
  return cap(
    pack.items.map((i) => ({
      label: short(i.text),
      description: citeText(i.cite) || i.section,
      noteId: i.cite && i.cite.kind === "memory" ? i.cite.id : undefined,
    })),
  );
}

export function shapeRecall(r: RecallResult): TreeRow[] {
  return cap(r.hits.map((h) => ({ label: short(h.content), description: h.score.toFixed(2), noteId: h.id })));
}
