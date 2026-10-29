import type { FindItem } from "@/lib/api";

/**
 * Export the current result set as CSV.
 *
 * The Streamlit app offered CSV/Excel/PDF on seven pages; dropping export
 * entirely would have been a real capability loss. CSV only, and generated
 * from what is already on screen rather than by re-querying: the browser has
 * the rows, and "export exactly what I am looking at" is the behaviour people
 * actually expect from an export button.
 */
const COLUMNS = [
  "kind",
  "id",
  "title",
  "snippet",
  "project",
  "category",
  "status",
  "confidence",
  "occurred_at",
] as const;

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function resultsToCsv(items: FindItem[]): string {
  const header = COLUMNS.join(",");
  const rows = items.map((item) =>
    COLUMNS.map((c) => cell((item as unknown as Record<string, unknown>)[c])).join(","),
  );
  // Excel needs the BOM to read UTF-8 correctly; without it, accented
  // project names and non-Latin content arrive mangled.
  return "\ufeff" + [header, ...rows].join("\n");
}

export function downloadCsv(items: FindItem[], filename = "throughline-results.csv") {
  const blob = new Blob([resultsToCsv(items)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
