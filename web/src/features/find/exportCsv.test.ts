import { describe, expect, it } from "vitest";

import type { FindItem } from "@/lib/api";
import { resultsToCsv } from "./exportCsv";

/**
 * CSV escaping is the kind of thing that looks right until someone's data
 * contains a comma. Memory chunks are free text written by an LLM — quotes,
 * newlines and commas are the normal case, not the edge case.
 */
function item(over: Partial<FindItem> = {}): FindItem {
  return {
    kind: "memory",
    id: 1,
    title: null,
    snippet: "plain",
    project: "alpha",
    occurred_at: "2026-08-10T12:00:00+00:00",
    category: "decision",
    status: "active",
    confidence: 0.9,
    conversation_id: null,
    score: 0.1,
    retrievers: 1,
    ...over,
  };
}

const lines = (csv: string) => csv.replace(/^﻿/, "").split("\n");

describe("resultsToCsv", () => {
  it("starts with a UTF-8 BOM so Excel reads accents correctly", () => {
    expect(resultsToCsv([item()]).startsWith("﻿")).toBe(true);
  });

  it("writes a header even with no rows", () => {
    expect(lines(resultsToCsv([]))[0]).toContain("kind,id,title");
  });

  it("quotes fields containing a comma", () => {
    const csv = lines(resultsToCsv([item({ snippet: "a, b, c" })]))[1];
    expect(csv).toContain('"a, b, c"');
  });

  it("doubles embedded quotes", () => {
    const csv = lines(resultsToCsv([item({ snippet: 'he said "no"' })]))[1];
    expect(csv).toContain('"he said ""no"""');
  });

  it("quotes fields containing a newline rather than breaking the row", () => {
    const csv = resultsToCsv([item({ snippet: "line one\nline two" })]);
    expect(csv).toContain('"line one\nline two"');
    // The quoted newline is inside a field; the file still has one header.
    expect(csv.replace(/^﻿/, "").split("\n")[0]).not.toContain("line");
  });

  it("renders null and undefined as empty, not as the word null", () => {
    const row = lines(resultsToCsv([item({ title: null, confidence: null })]))[1];
    expect(row).not.toContain("null");
    expect(row).not.toContain("undefined");
  });

  it("emits one row per item in order", () => {
    const csv = lines(resultsToCsv([item({ id: 1 }), item({ id: 2 }), item({ id: 3 })]));
    expect(csv).toHaveLength(4);
    expect(csv[1]).toContain(",1,");
    expect(csv[3]).toContain(",3,");
  });

  it("does not leak internal ranking fields into the export", () => {
    const header = lines(resultsToCsv([item()]))[0];
    expect(header).not.toContain("score");
    expect(header).not.toContain("retrievers");
  });
});
