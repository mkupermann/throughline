/** Runtime feature flags, injected by the server into index.html.
 *
 * The built SPA is static, so a single committed bundle cannot know at build
 * time whether Project Management is on. `throughline.api.app` stamps
 * `window.__THROUGHLINE_META__` into the shell at serve time; this module
 * reads it synchronously so navigation can decide before first paint — no
 * fetch, no flash of a link the API would 404.
 *
 * When meta is missing (vite dev, unit tests) the default is PM-on so the
 * surface stays reachable while working on it. A production build that lost
 * its meta hides PM rather than advertising routes the API does not serve.
 */

export interface Features {
  pm: boolean;
}

interface Meta {
  features?: Partial<Features>;
}

type GlobalWithMeta = typeof globalThis & { __THROUGHLINE_META__?: Meta };

function readMeta(): Meta {
  return (globalThis as GlobalWithMeta).__THROUGHLINE_META__ ?? {};
}

export function features(): Features {
  const injected = readMeta().features?.pm;
  if (typeof injected === "boolean") {
    return { pm: injected };
  }
  return { pm: import.meta.env.DEV };
}

/** Test helper: replace the injected meta for the rest of the page/test. */
export function setFeatures(next: Features): void {
  (globalThis as GlobalWithMeta).__THROUGHLINE_META__ = {
    ...readMeta(),
    features: next,
  };
}

/** Test helper: drop injected meta so `features()` falls back to its default. */
export function clearFeatures(): void {
  delete (globalThis as GlobalWithMeta).__THROUGHLINE_META__;
}
