import { describe, expect, it } from "vitest";

import { parseJobCompletion } from "./JobConsole";

describe("parseJobCompletion", () => {
  it("recognises a successful job", () => {
    expect(parseJobCompletion("exit=0 duration=4.2s")).toEqual({
      ok: true,
      returncode: 0,
      summary: "exit=0 duration=4.2s",
    });
  });

  it("treats a non-zero or missing exit code as a failure", () => {
    expect(parseJobCompletion("exit=3 duration=1.0s").ok).toBe(false);
    expect(parseJobCompletion("exit=None duration=0.0s error=missing binary")).toMatchObject({
      ok: false,
      returncode: null,
    });
  });
});
