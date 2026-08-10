import type { ReactNode } from "react";
import { createElement } from "react";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { parseFindState, toApiParams, toSearchParams, useFindState } from "./useFindState";

/** Mounts `useFindState` (which needs `useSearchParams`, hence a Router) at `path`. */
function withRouter(path: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(MemoryRouter, { initialEntries: [path] }, children);
  };
}

/**
 * The URL is the state — that is the Phase 2 acceptance bar, and these two
 * pure functions are the whole of it. Until now they were only verified by
 * driving a browser, which catches a broken round-trip but not the edge
 * cases: a hand-edited URL, an out-of-range page, a facet value containing
 * a delimiter.
 */
const parse = (qs: string) => parseFindState(new URLSearchParams(qs));

describe("parseFindState", () => {
  it("returns usable defaults for an empty query string", () => {
    const s = parse("");
    expect(s).toMatchObject({
      q: "",
      kinds: [],
      minConfidence: null,
      hasEmbedding: null,
      mode: "list",
      page: 0,
      perPage: 30,
    });
  });

  it("reads repeated params as multi-select facets", () => {
    expect(parse("kind=memory&kind=message&category=decision").kinds).toEqual([
      "memory",
      "message",
    ]);
  });

  it("only accepts known view modes", () => {
    expect(parse("mode=table").mode).toBe("table");
    expect(parse("mode=timeline").mode).toBe("timeline");
    expect(parse("mode=graph").mode).toBe("graph");
    // A hand-edited or stale URL must not put the UI into an unrenderable mode.
    expect(parse("mode=chart").mode).toBe("list");
    expect(parse("mode=").mode).toBe("list");
  });

  it("clamps hostile paging values instead of trusting them", () => {
    expect(parse("page=-5").page).toBe(0);
    expect(parse("page=abc").page).toBe(0);
    expect(parse("per_page=99999").perPage).toBe(200);
    expect(parse("per_page=notanumber").perPage).toBe(30);
  });

  it("falls back to the default page size for nonsense values, not to 1", () => {
    // `per_page=0` is meaningless input. Falling back to the default beats
    // clamping to 1, which would technically be "valid" and give a
    // single-row page nobody asked for.
    expect(parse("per_page=0").perPage).toBe(30);
    expect(parse("per_page=-10").perPage).toBe(30);
  });

  it("distinguishes an absent tri-state filter from a false one", () => {
    expect(parse("").hasEmbedding).toBeNull();
    expect(parse("has_embedding=false").hasEmbedding).toBe(false);
    expect(parse("has_embedding=true").hasEmbedding).toBe(true);
  });

  it("treats an empty confidence as no constraint, not zero", () => {
    expect(parse("min_confidence=").minConfidence).toBeNull();
    expect(parse("min_confidence=0.6").minConfidence).toBe(0.6);
  });

  it("parses providers from the URL", () => {
    const s = parseFindState(new URLSearchParams("provider=hermes&provider=vibe"));
    expect(s.providers).toEqual(["hermes", "vibe"]);
  });

  it("defaults providers to empty", () => {
    expect(parseFindState(new URLSearchParams("")).providers).toEqual([]);
  });
});

describe("toSearchParams", () => {
  it("omits defaults so a plain search stays a short URL", () => {
    expect(toSearchParams(parse("q=hello")).toString()).toBe("q=hello");
  });

  it("round-trips every field it serialises", () => {
    const qs =
      "q=pgvector&kind=memory&kind=message&category=decision&project=alpha" +
      "&status=active&tag=db&provider=hermes&min_confidence=0.7&has_embedding=true" +
      "&mode=table&page=2&per_page=50";
    expect(parseFindState(toSearchParams(parse(qs)))).toEqual(parse(qs));
  });

  it("carries the provider scope through, like any other facet", () => {
    // Unlike category/tag/confidence (Find-local, spec §4.2), provider must
    // not be dropped by an ordinary `update()` on this page — otherwise
    // typing a search query while "Hermes" is scoped would silently clear
    // the scope from the URL.
    expect(toSearchParams(parse("provider=hermes&provider=vibe")).getAll("provider")).toEqual([
      "hermes",
      "vibe",
    ]);
  });

  it("round-trips values containing spaces and delimiters", () => {
    const s = parse("");
    const tricky = { ...s, q: "a b&c=d", projects: ["my project", "a,b"] };
    const back = parseFindState(toSearchParams(tricky));
    expect(back.q).toBe("a b&c=d");
    expect(back.projects).toEqual(["my project", "a,b"]);
  });

  it("keeps page 0 out of the URL but preserves later pages", () => {
    expect(toSearchParams({ ...parse(""), page: 0 }).has("page")).toBe(false);
    expect(toSearchParams({ ...parse(""), page: 3 }).get("page")).toBe("3");
  });
});

describe("toApiParams", () => {
  it("converts page/perPage into limit and offset", () => {
    const p = toApiParams(parse("q=x&page=2&per_page=25"));
    expect(p.get("limit")).toBe("25");
    expect(p.get("offset")).toBe("50");
  });

  it("sends offset 0 on the first page", () => {
    expect(toApiParams(parse("q=x")).get("offset")).toBe("0");
  });

  it("forwards each facet value as a repeated param", () => {
    const p = toApiParams(parse("q=x&kind=memory&kind=skill"));
    expect(p.getAll("kind")).toEqual(["memory", "skill"]);
  });

  it("forwards the provider scope, so an active scope actually filters results", () => {
    const p = toApiParams(parse("q=x&provider=hermes&provider=vibe"));
    expect(p.getAll("provider")).toEqual(["hermes", "vibe"]);
  });

  it("omits filters that are not set", () => {
    const p = toApiParams(parse("q=x"));
    expect(p.has("min_confidence")).toBe(false);
    expect(p.has("has_embedding")).toBe(false);
  });

  it("sends has_embedding=false rather than dropping it", () => {
    // Dropping it would silently turn "show me chunks with no embedding"
    // into "show me anything" — the opposite of what was asked.
    expect(toApiParams(parse("q=x&has_embedding=false")).get("has_embedding")).toBe("false");
  });
});

/**
 * `providers` is deliberately excluded from both `clearAll` and
 * `activeFilterCount` — spec §4.2: provider is app-scope (the bar shows it
 * everywhere), category/tag/confidence/etc. are Find-local. If "Clear N" on
 * this page silently blanked the scope, the provider bar mounted in Shell
 * would change state from a control that looks completely unrelated to it.
 * That invariant lived only in a code comment on `FindState.providers`
 * until now — nothing exercised `clearAll`/`activeFilterCount` at all, so a
 * future edit that added `providers: []` to `clearAll`'s patch, or summed
 * `providers.length` into the count, would break the spec with no test
 * failing. Added per code review of Task 9.
 */
describe("clearAll", () => {
  it("leaves the provider scope untouched while clearing Find-local facets", () => {
    const { result } = renderHook(() => useFindState(), {
      wrapper: withRouter("/find?provider=hermes&category=insight&tag=db"),
    });
    expect(result.current.state.providers).toEqual(["hermes"]);

    act(() => {
      result.current.clearAll();
    });

    expect(result.current.state.providers).toEqual(["hermes"]);
    expect(result.current.state.categories).toEqual([]);
    expect(result.current.state.tags).toEqual([]);
  });
});

describe("activeFilterCount", () => {
  it("does not count an active provider scope", () => {
    const { result } = renderHook(() => useFindState(), {
      wrapper: withRouter("/find?provider=hermes&provider=vibe"),
    });
    expect(result.current.activeFilterCount).toBe(0);
  });

  it("still counts Find-local facets normally alongside an active scope", () => {
    const { result } = renderHook(() => useFindState(), {
      wrapper: withRouter("/find?provider=hermes&category=insight&tag=db"),
    });
    expect(result.current.activeFilterCount).toBe(2);
  });
});
