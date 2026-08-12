/**
 * Provider is app-scope; the other facets are Find-local.
 *
 * The bar and the Find facet are two renderings of ONE piece of state — this
 * URL parameter — not two states to keep in sync. That is what removes the
 * risk of the two controls disagreeing, and it is why "I am looking at
 * Hermes" survives navigating from Find to Curate while `category` does not.
 */
export const PROVIDER_PARAM = "provider";

export function readProviders(sp: URLSearchParams): string[] {
  return sp.getAll(PROVIDER_PARAM);
}

export function withProviders(sp: URLSearchParams, next: string[]): URLSearchParams {
  const out = new URLSearchParams(sp);
  out.delete(PROVIDER_PARAM);
  for (const name of next) out.append(PROVIDER_PARAM, name);
  return out;
}

/**
 * Build a link to `to` that carries the provider scope, merged into
 * whatever query string and fragment `to` already has.
 *
 * Every NAV target today is a bare path, so `${to}?${carried}` used to work
 * — but that breaks the moment a caller (a future command-palette entry, a
 * deep link) navigates to a path that already has its own `?...` or
 * `#...`, producing a malformed `?tab=queue?provider=hermes`. Parse and
 * reassemble instead of assuming `to` is bare.
 */
export function carryProviders(to: string, sp: URLSearchParams): string {
  const active = readProviders(sp);
  if (active.length === 0) return to;

  const hashIndex = to.indexOf("#");
  const hash = hashIndex === -1 ? "" : to.slice(hashIndex);
  const withoutHash = hashIndex === -1 ? to : to.slice(0, hashIndex);

  const queryIndex = withoutHash.indexOf("?");
  const path = queryIndex === -1 ? withoutHash : withoutHash.slice(0, queryIndex);
  const existingQuery = queryIndex === -1 ? "" : withoutHash.slice(queryIndex + 1);

  // The scope in `sp` is authoritative — drop any provider param already
  // embedded in `to` rather than carrying two conflicting sources of truth.
  const merged = new URLSearchParams(existingQuery);
  merged.delete(PROVIDER_PARAM);
  for (const name of active) merged.append(PROVIDER_PARAM, name);

  return `${path}?${merged.toString()}${hash}`;
}
