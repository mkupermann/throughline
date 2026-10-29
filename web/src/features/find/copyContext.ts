import type { AskResponse, AskSource, FindItem } from "@/lib/api";

function oneLine(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function label(value: string): string {
  const words = value.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Markdown intended for another AI tool, built from a strict field allowlist. */
export function contextForFindItem(item: FindItem): string {
  const heading = oneLine(item.title || item.category || `${label(item.kind)} ${item.id}`);
  const lines = [`## ${heading}`, "", `Type: ${label(item.kind)}`];
  if (item.project) lines.push(`Project: ${oneLine(item.project)}`);
  if (item.category) {
    lines.push(`${item.kind === "message" ? "Role" : "Category"}: ${oneLine(item.category)}`);
  }
  if (item.occurred_at) lines.push(`Date: ${item.occurred_at}`);
  if (item.snippet?.trim()) lines.push("", item.snippet.trim());
  const source = item.kind === "project"
    ? `Throughline project ${oneLine(item.title || item.project || String(item.id))}`
    : `Throughline ${item.kind} #${item.id}`;
  lines.push("", `Source: ${source}`);
  return lines.join("\n");
}

function sourceMarkdown(source: AskSource): string {
  const lines = [`[${source.n}] ${oneLine(source.ref)}`];
  if (source.project) lines.push(`Project: ${oneLine(source.project)}`);
  if (source.category) {
    lines.push(`${source.kind === "message" ? "Role" : "Category"}: ${oneLine(source.category)}`);
  }
  if (source.excerpt.trim()) lines.push(source.excerpt.trim());
  return lines.join("\n");
}

/** A grounded answer plus only the evidence needed to reuse or verify it. */
export function contextForAnswer(response: AskResponse): string {
  const cited = new Set(response.cited);
  const sources = cited.size
    ? response.sources.filter((source) => cited.has(source.n))
    : response.sources;
  const sections = [
    `## Question\n${response.question.trim()}`,
    `## Answer\n${response.answer.trim()}`,
  ];
  if (cited.size === 0) {
    sections.push("Verification: Unverified. This answer cites no stored record.");
  }
  if (sources.length) {
    const heading = cited.size ? "Sources" : "Retrieved records";
    sections.push(`## ${heading}\n${sources.map(sourceMarkdown).join("\n\n")}`);
  }
  return sections.join("\n\n");
}
