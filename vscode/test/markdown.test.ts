import { test } from "node:test";
import assert from "node:assert/strict";
import { conflictsToMarkdown, noteToMarkdown, packToMarkdown } from "../src/markdown";
import type { ContextPack, NoteDetail } from "../src/apiClient";

test("pack_markdown_lists_items_with_citations", () => {
  const pack = {
    text: "", citations: [], tokens_used: 120, budget: 2000, within_budget: true, skipped: 0, estimate: "",
    items: [
      { section: "decisions", text: "Use Rust", tokens: 5, cite: { kind: "memory", id: 7 } },
      { section: "notes", text: "plain", tokens: 3, cite: null },
    ],
  } as ContextPack;
  const md = packToMarkdown(pack, "demo");
  assert.match(md, /Project: demo/);
  assert.match(md, /120 \/ 2000/);
  assert.match(md, /- \*\*decisions\*\*: Use Rust \[memory #7\]/);
  assert.match(md, /- \*\*notes\*\*: plain\n/);
});

test("note_markdown_shows_chain_and_validity", () => {
  const n: NoteDetail = {
    id: 5, content: "line one\nline two", category: "decision", project_name: "demo",
    created_at: "2026-01-01", source_at: null, valid_from: "2026-01-02", valid_to: "2026-02-01",
    status: "superseded", superseded_by: 9, superseded_at: null, supersedes: 3,
  };
  const md = noteToMarkdown(n);
  for (const s of ["id: 5", "category: decision", "project: demo", "status: superseded", "valid_from: 2026-01-02", "valid_to: 2026-02-01", "created_at: 2026-01-01", "supersedes: 3", "superseded_by: 9", "> line one\n> line two"]) {
    assert.ok(md.includes(s), s);
  }
  const plain = noteToMarkdown({ ...n, supersedes: null, superseded_by: null });
  assert.ok(!plain.includes("supersedes") && !plain.includes("superseded_by"));
});

test("conflicts_group_by_kind", () => {
  const md = conflictsToMarkdown({
    checked: 4,
    conflicts: [
      { kind: "a", project_name: null, left_id: 1, left_content: "", right_id: 2, right_content: "", detail: "d1" },
      { kind: "b", project_name: null, left_id: 3, left_content: "", right_id: 4, right_content: "", detail: "d2" },
    ],
  });
  assert.match(md, /## a\n\n- 1 vs 2: d1/);
  assert.match(md, /## b\n\n- 3 vs 4: d2/);
});
