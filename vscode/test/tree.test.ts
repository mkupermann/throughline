import { test } from "node:test";
import assert from "node:assert/strict";
import { shapeContext, shapeRecall } from "../src/treeLogic";
import type { ContextPack, RecallResult } from "../src/apiClient";

const pack = (n: number) =>
  ({ items: Array.from({ length: n }, (_, i) => ({ section: "s", text: `item ${i} ` + "x".repeat(200), tokens: 1, cite: { kind: "memory", id: i + 1 } })) }) as unknown as ContextPack;

test("tree_caps_children_at_200", () => {
  const rows = shapeContext(pack(250));
  assert.equal(rows.length, 201);
  assert.equal(rows[200].label, "… and 50 more");
  assert.equal(rows[0].label.length, 80);
  assert.equal(rows[0].noteId, 1);
  assert.equal(shapeContext(pack(200)).length, 200);
});

test("large_pack_is_capped_in_the_tree", () => {
  const hits = Array.from({ length: 1000 }, (_, i) => ({ id: i, content: "c", score: 1 }));
  const rows = shapeRecall({ mode: "m", degraded: null, hits } as unknown as RecallResult);
  assert.equal(rows.length, 201);
  assert.equal(rows[200].label, "… and 800 more");
  assert.equal(rows[200].noteId, undefined);
});
