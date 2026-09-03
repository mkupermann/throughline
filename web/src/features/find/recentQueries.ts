export const RECENT_QUERIES_KEY = "throughline.find.recent-queries.v1";
export const MAX_RECENT_QUERIES = 8;

export type QueryIntent = "find" | "ask";

export interface RecentQuery {
  query: string;
  intent: QueryIntent;
  usedAt: string;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isRecentQuery(value: unknown): value is RecentQuery {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<RecentQuery>;
  return (
    typeof item.query === "string" &&
    (item.intent === "find" || item.intent === "ask") &&
    typeof item.usedAt === "string"
  );
}

export function readRecentQueries(storage: StorageLike | null = browserStorage()): RecentQuery[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(RECENT_QUERIES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter(isRecentQuery).slice(0, MAX_RECENT_QUERIES)
      : [];
  } catch {
    return [];
  }
}

export function rememberQuery(
  rawQuery: string,
  intent: QueryIntent,
  storage: StorageLike | null = browserStorage(),
  usedAt = new Date().toISOString(),
): RecentQuery[] {
  const query = rawQuery.trim();
  if (!query || !storage) return readRecentQueries(storage);
  const key = query.toLocaleLowerCase();
  const recent = readRecentQueries(storage).filter(
    (item) => item.query.toLocaleLowerCase() !== key,
  );
  const next = [{ query, intent, usedAt }, ...recent].slice(0, MAX_RECENT_QUERIES);
  try {
    storage.setItem(RECENT_QUERIES_KEY, JSON.stringify(next));
  } catch {
    // Private browsing and hardened profiles may reject local storage.
  }
  return next;
}
