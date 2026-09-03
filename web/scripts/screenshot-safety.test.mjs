import { describe, expect, it } from "vitest";

import { assertScreenshotFixture } from "./screenshot-safety.mjs";

const overview = {
  totals: { conversations: 10, messages: 52, chunks: 22, skills: 5, projects: 5 },
};

const project = {
  project: "acme-web",
  summary: "5 sessions, 26 messages",
  sessionCount: 5,
  messageCount: 26,
  knowledge: [
    {
      content:
        "JWT auth strategy for acme-web: 15-minute access tokens + 7-day refresh tokens stored in httpOnly cookies.",
    },
  ],
};

describe("assertScreenshotFixture", () => {
  it("accepts the bundled synthetic screenshot fixture", () => {
    expect(() => assertScreenshotFixture(overview, project)).not.toThrow();
  });

  it("refuses a plausible live instance before any screenshot is written", () => {
    expect(() =>
      assertScreenshotFixture(
        { ...overview, totals: { ...overview.totals, conversations: 4156 } },
        project,
      ),
    ).toThrow(/refusing to capture/i);
  });

  it("requires the fixture's synthetic knowledge fingerprint", () => {
    expect(() =>
      assertScreenshotFixture(overview, {
        ...project,
        knowledge: [{ content: "A real project happened to use the same name." }],
      }),
    ).toThrow(/refusing to capture/i);
  });
});
