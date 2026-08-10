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

/** Build a link to `to` that preserves only the provider scope. */
export function carryProviders(to: string, sp: URLSearchParams): string {
  const active = readProviders(sp);
  if (active.length === 0) return to;
  const carried = new URLSearchParams();
  for (const name of active) carried.append(PROVIDER_PARAM, name);
  return `${to}?${carried.toString()}`;
}
