import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * The URL *is* the state.
 *
 * Every facet, the view mode, the page and the sort live in the query string
 * — nothing that changes what you are looking at is held in component state.
 * That is what makes a view a link you can paste into a note, and what makes
 * the browser back button restore the exact prior view instead of an
 * approximation of it.
 */
export type ViewMode = "list" | "table" | "graph" | "ask";

export interface FindState {
  q: string;
  kinds: string[];
  categories: string[];
  projects: string[];
  statuses: string[];
  tags: string[];
  /**
   * App-scope, not Find-local (spec §4.2) — parsed here because Find reads
   * and filters by it like any other facet, but it is deliberately left out
   * of `clearAll`/`activeFilterCount` below: those describe what "Clear N"
   * removes, and clearing filters on this page must not silently drop the
   * scope the provider bar is showing everywhere else.
   */
  providers: string[];
  minConfidence: number | null;
  hasEmbedding: boolean | null;
  mode: ViewMode;
  page: number;
  perPage: number;
}

const DEFAULTS: FindState = {
  q: "",
  kinds: [],
  categories: [],
  projects: [],
  statuses: [],
  tags: [],
  providers: [],
  minConfidence: null,
  hasEmbedding: null,
  mode: "list",
  page: 0,
  perPage: 30,
};

const MULTI = ["kind", "category", "project", "status", "tag", "provider"] as const;

const MAX_PER_PAGE = 200;

/**
 * Coerce a URL number, falling back to `fallback` for anything invalid.
 *
 * The previous inline expression used `Number(x) || default`, which treated
 * `0` as invalid (falsy → default) but `-10` as valid (truthy → clamped to
 * 1). Two nonsense inputs, two different outcomes. Anything not a finite
 * number in range now lands on the same fallback.
 */
function urlNumber(raw: string | null, { min, max, fallback }: {
  min: number;
  max?: number;
  fallback: number;
}): number {
  const n = Number(raw);
  if (raw === null || raw === "" || !Number.isFinite(n) || n < min) return fallback;
  return max === undefined ? Math.floor(n) : Math.min(max, Math.floor(n));
}

export function parseFindState(sp: URLSearchParams): FindState {
  const conf = sp.get("min_confidence");
  const emb = sp.get("has_embedding");
  return {
    q: sp.get("q") ?? DEFAULTS.q,
    kinds: sp.getAll("kind"),
    categories: sp.getAll("category"),
    projects: sp.getAll("project"),
    statuses: sp.getAll("status"),
    tags: sp.getAll("tag"),
    providers: sp.getAll("provider"),
    minConfidence: conf === null || conf === "" ? null : Number(conf),
    hasEmbedding: emb === null ? null : emb === "true",
    mode: (["table", "graph", "ask"] as const).includes(sp.get("mode") as never)
      ? (sp.get("mode") as ViewMode)
      : "list",
    page: urlNumber(sp.get("page"), { min: 0, fallback: 0 }),
    perPage: urlNumber(sp.get("per_page"), { min: 1, max: MAX_PER_PAGE, fallback: DEFAULTS.perPage }),
  };
}

/** Serialise back to a query string, omitting defaults so URLs stay short. */
export function toSearchParams(s: FindState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  s.kinds.forEach((v) => sp.append("kind", v));
  s.categories.forEach((v) => sp.append("category", v));
  s.projects.forEach((v) => sp.append("project", v));
  s.statuses.forEach((v) => sp.append("status", v));
  s.tags.forEach((v) => sp.append("tag", v));
  // Not one of the defaults skipped above — the provider scope must survive
  // every `update()` on this page (e.g. typing a query), not just be
  // readable on first load. See the `providers` field doc on `FindState`.
  s.providers.forEach((v) => sp.append("provider", v));
  if (s.minConfidence !== null) sp.set("min_confidence", String(s.minConfidence));
  if (s.hasEmbedding !== null) sp.set("has_embedding", String(s.hasEmbedding));
  if (s.mode !== "list") sp.set("mode", s.mode);
  if (s.page > 0) sp.set("page", String(s.page));
  if (s.perPage !== DEFAULTS.perPage) sp.set("per_page", String(s.perPage));
  return sp;
}

export function useFindState() {
  const [sp, setSp] = useSearchParams();
  const state = useMemo(() => parseFindState(sp), [sp]);

  const update = useCallback(
    (patch: Partial<FindState>, opts?: { replace?: boolean }) => {
      const next = { ...parseFindState(sp), ...patch };
      // Any change to what is being searched resets paging — otherwise you
      // land on page 4 of a result set that now has one page.
      const pagingKeys: (keyof FindState)[] = [
        "q", "kinds", "categories", "projects", "statuses", "tags", "providers",
        "minConfidence", "hasEmbedding", "perPage",
      ];
      if (patch.page === undefined && pagingKeys.some((k) => k in patch)) next.page = 0;
      setSp(toSearchParams(next), { replace: opts?.replace ?? false });
    },
    [sp, setSp],
  );

  /** Toggle one value inside a multi-select facet. */
  const toggle = useCallback(
    (facet: (typeof MULTI)[number], value: string) => {
      const key = ({
        kind: "kinds", category: "categories", project: "projects", status: "statuses",
        tag: "tags", provider: "providers",
      } as const)[facet];
      const current = state[key];
      update({ [key]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value] } as Partial<FindState>);
    },
    [state, update],
  );

  const clearAll = useCallback(() => {
    update({
      kinds: [], categories: [], projects: [], statuses: [], tags: [],
      minConfidence: null, hasEmbedding: null,
    });
  }, [update]);

  const activeFilterCount =
    state.kinds.length + state.categories.length + state.projects.length +
    state.statuses.length + state.tags.length +
    (state.minConfidence !== null ? 1 : 0) + (state.hasEmbedding !== null ? 1 : 0);

  return { state, update, toggle, clearAll, activeFilterCount };
}

/** Build the API query string from UI state. */
export function toApiParams(s: FindState): URLSearchParams {
  const sp = new URLSearchParams();
  sp.set("q", s.q);
  s.kinds.forEach((v) => sp.append("kind", v));
  s.categories.forEach((v) => sp.append("category", v));
  s.projects.forEach((v) => sp.append("project", v));
  s.statuses.forEach((v) => sp.append("status", v));
  s.tags.forEach((v) => sp.append("tag", v));
  // App-scope, forwarded like any other facet so "I am looking at Hermes"
  // actually filters Find's results, not just the URL that says so.
  s.providers.forEach((v) => sp.append("provider", v));
  if (s.minConfidence !== null) sp.set("min_confidence", String(s.minConfidence));
  if (s.hasEmbedding !== null) sp.set("has_embedding", String(s.hasEmbedding));
  sp.set("limit", String(s.perPage));
  sp.set("offset", String(s.page * s.perPage));
  return sp;
}
