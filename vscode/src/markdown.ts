import type { Citation, ConflictReport, ContextPack, NoteDetail } from "./apiClient";

export function citeText(c: Citation | null | undefined): string {
  if (!c) return "";
  return c.title ? `${c.kind} #${c.id} (${c.title})` : `${c.kind} #${c.id}`;
}

export function packToMarkdown(pack: ContextPack, project?: string): string {
  const lines = ["# Throughline context", ""];
  if (project) lines.push(`Project: ${project}`);
  lines.push(`Tokens: ${pack.tokens_used} / ${pack.budget}`, "");
  for (const it of pack.items) {
    const cite = citeText(it.cite);
    lines.push(`- **${it.section}**: ${it.text.replace(/\s*\n\s*/g, " ")}${cite ? ` [${cite}]` : ""}`);
  }
  if (pack.skipped > 0) lines.push("", `${pack.skipped} item(s) skipped to fit the budget.`);
  return lines.join("\n") + "\n";
}

export function noteToMarkdown(n: NoteDetail): string {
  const lines = [
    `# Note ${n.id}`,
    "",
    `- id: ${n.id}`,
    `- category: ${n.category}`,
    `- project: ${n.project_name ?? "none"}`,
    `- status: ${n.status}`,
    `- valid_from: ${n.valid_from ?? "unknown"}`,
    `- valid_to: ${n.valid_to ?? "open"}`,
    `- created_at: ${n.created_at ?? "unknown"}`,
  ];
  if (n.supersedes !== null && n.supersedes !== undefined) lines.push(`- supersedes: ${n.supersedes}`);
  if (n.superseded_by !== null && n.superseded_by !== undefined) lines.push(`- superseded_by: ${n.superseded_by}`);
  lines.push("", ...n.content.split("\n").map((l) => `> ${l}`));
  return lines.join("\n") + "\n";
}

export function conflictsToMarkdown(r: ConflictReport): string {
  const lines = ["# Throughline conflicts", "", `Checked: ${r.checked}`, ""];
  if (r.conflicts.length === 0) lines.push("No conflicts found.");
  const kinds = [...new Set(r.conflicts.map((c) => c.kind))];
  for (const k of kinds) {
    lines.push(`## ${k}`, "");
    for (const c of r.conflicts.filter((x) => x.kind === k)) {
      lines.push(`- ${c.left_id ?? "?"} vs ${c.right_id ?? "?"}: ${c.detail.replace(/\r?\n/g, " ")}`);
    }
    lines.push("");
  }
  return lines.join("\n").trimEnd() + "\n";
}
