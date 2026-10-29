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

  // `to` was assumed to always be a bare path (true for every NAV target
  // today), so the original implementation did `${to}?${carried}`
  // unconditionally — producing "/curate?tab=queue?provider=hermes" for any
  // caller that navigates to a path already carrying a query or a fragment
  // (e.g. a future command-palette entry). Regression coverage per review.
  it("merges into an existing query string instead of appending a second '?'", () => {
    const sp = new URLSearchParams("provider=hermes");
    expect(carryProviders("/curate?tab=queue", sp)).toBe("/curate?tab=queue&provider=hermes");
  });

  it("preserves a fragment, placing it after the query", () => {
    const sp = new URLSearchParams("provider=hermes");
    expect(carryProviders("/curate#section", sp)).toBe("/curate?provider=hermes#section");
  });

  it("merges an existing query and preserves a fragment together", () => {
    const sp = new URLSearchParams("provider=hermes&provider=vibe");
    expect(carryProviders("/curate?tab=queue#section", sp)).toBe(
      "/curate?tab=queue&provider=hermes&provider=vibe#section",
    );
  });

  it("does not duplicate a provider param already present in `to`", () => {
    // The scope in `sp` is authoritative — a stale `provider=` embedded in
    // `to` itself must not survive alongside it.
    const sp = new URLSearchParams("provider=hermes");
    expect(carryProviders("/curate?provider=codex", sp)).toBe("/curate?provider=hermes");
  });
});
