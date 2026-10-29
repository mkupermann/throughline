import { describe, expect, it } from "vitest";

import type { AskResponse, FindItem } from "@/lib/api";
import { contextForAnswer, contextForFindItem } from "./copyContext";

describe("contextForFindItem", () => {
  it("builds focused Markdown from a public field allowlist", () => {
    const item: FindItem = {
      kind: "memory",
      id: 7,
      title: "Stable ordering",
      snippet: "Load every page before reversing the transcript.",
      project: "atlas",
      occurred_at: "2026-01-02T09:00:00Z",
      category: "decision",
      status: "active",
      confidence: 0.95,
      conversation_id: 12,
      score: 0.812345,
      retrievers: 2,
    };

    const markdown = contextForFindItem(item);

    expect(markdown).toContain("## Stable ordering");
    expect(markdown).toContain("Type: Memory");
    expect(markdown).toContain("Project: atlas");
    expect(markdown).toContain("Category: decision");
    expect(markdown).toContain("Load every page before reversing the transcript.");
    expect(markdown).toContain("Source: Throughline memory #7");
    expect(markdown).not.toContain("0.812345");
    expect(markdown).not.toContain("retrievers");
    expect(markdown).not.toContain("conversation_id");
    expect(markdown).not.toContain("undefined");
  });

  it("labels message metadata as a role instead of a category", () => {
    const item = {
      kind: "message",
      id: 8,
      title: "Database discussion",
      snippet: "Keep vectors beside their source records.",
      project: "atlas",
      occurred_at: null,
      category: "assistant",
      status: null,
      confidence: null,
      conversation_id: 12,
      score: 0.7,
      retrievers: 1,
    } satisfies FindItem;

    const markdown = contextForFindItem(item);

    expect(markdown).toContain("Role: assistant");
    expect(markdown).not.toContain("Category: assistant");
  });

  it("uses the routed project name instead of an unstable synthetic id", () => {
    const item = {
      kind: "project",
      id: 0,
      title: "atlas",
      snippet: null,
      project: "atlas",
      occurred_at: null,
      category: null,
      status: null,
      confidence: null,
      conversation_id: null,
      score: 0.7,
      retrievers: 1,
    } satisfies FindItem;

    expect(contextForFindItem(item)).toContain("Source: Throughline project atlas");
    expect(contextForFindItem(item)).not.toContain("project #0");
  });
});

describe("contextForAnswer", () => {
  it("keeps the answer and cited evidence without backend internals", () => {
    const response: AskResponse = {
      question: "Why did we change the ordering?",
      answer: "A partial reverse looked complete [1].",
      sources: [
        {
          n: 1,
          kind: "memory_chunk",
          id: 7,
          ref: "Decision in atlas",
          project: "atlas",
          category: "decision",
          conversation_id: 12,
          distance: 0.12345,
          excerpt: "Load every page before reversing.",
        },
        {
          n: 2,
          kind: "message",
          id: 8,
          ref: "Uncited message",
          project: "atlas",
          category: null,
          conversation_id: 12,
          distance: 0.5,
          excerpt: "This source was not cited.",
        },
      ],
      cited: [1],
      degraded: null,
      backend: "remote-provider",
      model: "private-model-name",
      local: false,
    };

    const markdown = contextForAnswer(response);

    expect(markdown).toContain("## Question\nWhy did we change the ordering?");
    expect(markdown).toContain("## Answer\nA partial reverse looked complete [1].");
    expect(markdown).toContain("[1] Decision in atlas");
    expect(markdown).toContain("Load every page before reversing.");
    expect(markdown).not.toContain("Uncited message");
    expect(markdown).not.toContain("remote-provider");
    expect(markdown).not.toContain("private-model-name");
    expect(markdown).not.toContain("0.12345");
  });

  it("marks an uncited answer as unverified and calls its records retrieved", () => {
    const response: AskResponse = {
      question: "Why PostgreSQL?",
      answer: "It keeps related records together.",
      sources: [
        {
          n: 1,
          kind: "message",
          id: 8,
          ref: "Database discussion",
          project: "atlas",
          category: "assistant",
          conversation_id: 12,
          distance: 0.5,
          excerpt: "Keep vectors beside source records.",
        },
      ],
      cited: [],
      degraded: null,
      backend: "ollama",
      model: "local-model",
      local: true,
    };

    const markdown = contextForAnswer(response);

    expect(markdown).toContain("Verification: Unverified");
    expect(markdown).toContain("## Retrieved records");
    expect(markdown).toContain("Role: assistant");
    expect(markdown).not.toContain("## Sources");
  });
});
