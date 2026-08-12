import { describe, expect, it } from "vitest";

import { formatCompact, formatCount } from "./format";

// Numbers must agree with the language of the labels beside them. These
// helpers used `new Intl.NumberFormat()` with no locale, which follows the
// browser: on a German-configured machine the English interface read
// "3.330 conversations", which an English reader parses as three-point-three.
// The separator meant the opposite of what it said, with nothing on screen to
// disambiguate.

describe("formatCount", () => {
  it("groups thousands the way the English labels expect", () => {
    expect(formatCount(3330)).toBe("3,330");
    expect(formatCount(37255)).toBe("37,255");
    expect(formatCount(1000000)).toBe("1,000,000");
  });

  it("does not group below a thousand", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(999)).toBe("999");
  });

  it("renders absent values as an em dash rather than zero", () => {
    // "0 items" and "we don't know" are different facts, and a worklist that
    // shows the wrong one sends the reader somewhere pointless.
    expect(formatCount(null)).toBe("—");
    expect(formatCount(undefined)).toBe("—");
    expect(formatCount(NaN)).toBe("—");
  });
});

describe("formatCompact", () => {
  it("keeps full precision below ten thousand", () => {
    expect(formatCompact(9999)).toBe("9,999");
  });

  it("compacts larger numbers in the same locale", () => {
    expect(formatCompact(37255)).toBe("37.3K");
    expect(formatCompact(1200000)).toBe("1.2M");
  });

  it("renders absent values as an em dash", () => {
    expect(formatCompact(null)).toBe("—");
  });
});
