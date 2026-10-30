export const DEFAULT_URL = "http://127.0.0.1:8788";

/** The trimmed address when it is an http or https URL, otherwise undefined. */
export function validateUrl(value: string): string | undefined {
  const v = value.trim();
  try {
    const u = new URL(v);
    return u.protocol === "http:" || u.protocol === "https:" ? v : undefined;
  } catch {
    return undefined;
  }
}

/** A key of printable ASCII without spaces fits an HTTP header as it is. */
export function validateKey(value: string): boolean {
  return /^[\x21-\x7e]+$/.test(value);
}
