import { describe, expect, it } from "vitest";

import {
  PROVIDER_PARAM,
  carryProviders,
  readProviders,
  withProviders,
} from "./providerScope";

describe("provider scope", () => {
  it("reads repeated params", () => {
    const sp = new URLSearchParams("provider=hermes&provider=vibe");
    expect(readProviders(sp)).toEqual(["hermes", "vibe"]);
  });

  it("is empty when absent", () => {
    expect(readProviders(new URLSearchParams(""))).toEqual([]);
  });

  it("replaces rather than appends", () => {
    const sp = new URLSearchParams("provider=hermes&q=x");
    const next = withProviders(sp, ["vibe"]);
    expect(next.getAll(PROVIDER_PARAM)).toEqual(["vibe"]);
    expect(next.get("q")).toBe("x");
  });

  it("clears the param entirely when the selection is empty", () => {
    const sp = new URLSearchParams("provider=hermes");
    expect(withProviders(sp, []).toString()).toBe("");
  });

  it("carries provider across navigation but nothing else", () => {
    // Spec §4.2: provider is app-scope; category/tag/confidence stay Find-local.
    const sp = new URLSearchParams("provider=hermes&category=insight&q=zebra");
    const to = carryProviders("/curate", sp);
    expect(to).toBe("/curate?provider=hermes");
  });

  it("returns a bare path when no provider is active", () => {
    expect(carryProviders("/curate", new URLSearchParams("q=x"))).toBe("/curate");
  });

  it("carries several providers", () => {
    const sp = new URLSearchParams("provider=hermes&provider=vibe");
    expect(carryProviders("/operate", sp)).toBe("/operate?provider=hermes&provider=vibe");
  });
});
