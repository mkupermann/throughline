import { describe, expect, it } from "vitest";

import {
  MAX_RECENT_QUERIES,
  RECENT_QUERIES_KEY,
  readRecentQueries,
  rememberQuery,
  type StorageLike,
} from "./recentQueries";

function memoryStorage(): StorageLike & { raw: Map<string, string> } {
  const raw = new Map<string, string>();
  return {
    raw,
    getItem: (key) => raw.get(key) ?? null,
    setItem: (key, value) => raw.set(key, value),
  };
}

describe("recentQueries", () => {
  it("stores only the query, intent, and timestamp", () => {
    const storage = memoryStorage();
    rememberQuery("  Why PostgreSQL?  ", "ask", storage, "2026-01-02T09:00:00Z");

    expect(JSON.parse(storage.raw.get(RECENT_QUERIES_KEY) ?? "null")).toEqual([
      {
        query: "Why PostgreSQL?",
        intent: "ask",
        usedAt: "2026-01-02T09:00:00Z",
      },
    ]);
  });

  it("moves duplicates to the front and keeps a bounded history", () => {
    const storage = memoryStorage();
    for (let index = 0; index < MAX_RECENT_QUERIES + 3; index += 1) {
      rememberQuery(`query ${index}`, "find", storage, `2026-01-${String(index + 1).padStart(2, "0")}T09:00:00Z`);
    }
    rememberQuery("query 5", "ask", storage, "2026-02-01T09:00:00Z");

    const recent = readRecentQueries(storage);
    expect(recent).toHaveLength(MAX_RECENT_QUERIES);
    expect(recent[0]).toEqual({
      query: "query 5",
      intent: "ask",
      usedAt: "2026-02-01T09:00:00Z",
    });
    expect(recent.filter((item) => item.query === "query 5")).toHaveLength(1);
  });

  it("survives blocked, malformed, and unavailable local storage", () => {
    const blocked: StorageLike = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const malformed = memoryStorage();
    malformed.raw.set(RECENT_QUERIES_KEY, "not json");

    expect(readRecentQueries(blocked)).toEqual([]);
    expect(() => rememberQuery("safe", "find", blocked, "2026-01-01T00:00:00Z")).not.toThrow();
    expect(readRecentQueries(malformed)).toEqual([]);
  });
});
