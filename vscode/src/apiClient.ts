import { ThroughlineError, redact } from "./errors";

export interface Why {
  cosine?: number | null;
  lexical?: number | null;
  recency?: number | null;
  reliability?: number | null;
  strength?: number | null;
  provenance?: number | null;
  spread?: number | null;
  grounding?: number | null;
  fulltext?: number | null;
  [key: string]: unknown;
}

export interface Evidence {
  message_id: number;
  excerpt: string;
  created_at: string;
}

export interface RecallHit {
  id: number;
  content: string;
  category: string;
  project_name: string | null;
  status: string;
  valid_from: string;
  valid_to: string | null;
  source_type: string;
  conversation_id: number | null;
  session_title: string | null;
  source_at: string | null;
  superseded_by: number | null;
  supersedes: number | null;
  score: number;
  why: Why;
  relation: string | null;
  evidence: Evidence | null;
}

export interface RecallResult {
  mode: string;
  hits: RecallHit[];
  degraded: string | null;
}

export interface Citation {
  kind: string;
  id: number;
  title?: string | null;
  [key: string]: unknown;
}

export interface PackedItem {
  section: string;
  text: string;
  tokens: number;
  cite: Citation | null;
}

export interface ContextPack {
  text: string;
  items: PackedItem[];
  citations: Citation[];
  tokens_used: number;
  budget: number;
  within_budget: boolean;
  skipped: number;
  estimate: string;
}

export interface NoteDetail {
  id: number;
  content: string;
  category: string;
  project_name: string | null;
  created_at: string | null;
  source_at: string | null;
  valid_from: string | null;
  valid_to: string | null;
  status: string;
  superseded_by: number | null;
  superseded_at: string | null;
  supersedes: number | null;
}

export interface Conflict {
  kind: string;
  project_name: string | null;
  left_id: number | null;
  left_content: string;
  right_id: number | null;
  right_content: string;
  detail: string;
}

export interface ConflictReport {
  conflicts: Conflict[];
  checked: number;
}

export interface HealthRecovery {
  embedding_target: string | null;
  embedding_missing: string;
  [key: string]: unknown;
}

export interface RecallOptions {
  project?: string;
  rank?: "fulltext" | "weighted";
  limit?: number;
}

export interface ContextOptions {
  task?: string;
  budget?: number;
}

/** Joins the base address and a path by string, so a path prefix in the base survives. */
export function join(baseUrl: string, path: string): string {
  return baseUrl.replace(/\/+$/, "") + path;
}

export async function send(url: string, init: RequestInit): Promise<Response> {
  try {
    // A redirect would leave the configured address, so it is an error.
    return await fetch(url, { ...init, redirect: "error" });
  } catch (e) {
    throw new ThroughlineError("unreachable", "Throughline is not reachable", String(e instanceof Error ? e.message : e));
  }
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly key?: () => Promise<string | undefined>,
  ) {}

  recall(q: string, opts: RecallOptions = {}): Promise<RecallResult> {
    const p = new URLSearchParams({ q });
    if (opts.project) p.set("project", opts.project);
    if (opts.rank) p.set("rank", opts.rank);
    if (opts.limit !== undefined) p.set("limit", String(opts.limit));
    return this.get(`/api/recall?${p}`);
  }

  context(project: string, opts: ContextOptions = {}): Promise<ContextPack> {
    const p = new URLSearchParams({ project });
    if (opts.task) p.set("task", opts.task);
    if (opts.budget !== undefined) p.set("budget", String(opts.budget));
    return this.get(`/api/context?${p}`);
  }

  note(id: number): Promise<NoteDetail> {
    return this.get(`/api/memory/${encodeURIComponent(String(id))}`);
  }

  conflicts(): Promise<ConflictReport> {
    return this.get("/api/conflicts");
  }

  health(): Promise<HealthRecovery> {
    return this.get("/api/health-recovery");
  }

  private async get<T>(pathAndQuery: string): Promise<T> {
    const url = join(this.baseUrl, pathAndQuery);
    const headers: Record<string, string> = { Accept: "application/json" };
    // The first call carries no key: a local server needs none. Only a 401 triggers one retry with the key.
    let res = await send(url, { method: "GET", headers });
    let key: string | undefined;
    if (res.status === 401 && this.key) {
      try {
        key = (await this.key())?.trim() || undefined;
      } catch {
        key = undefined; // a secret storage error counts as no key
      }
    }
    try {
      if (key) {
        await res.text();
        res = await send(url, { method: "GET", headers: { ...headers, Authorization: `Bearer ${key}` } });
      }
      return await this.read<T>(res, key !== undefined);
    } catch (e) {
      throw key ? redact(e, key) : e;
    }
  }

  private async read<T>(res: Response, keySent: boolean): Promise<T> {
    const text = await res.text();
    if (!res.ok) {
      let detail = text.slice(0, 300);
      try {
        const j = JSON.parse(text) as { error?: string; detail?: string };
        detail = [j.error, j.detail].filter(Boolean).join(": ") || detail;
      } catch {
        /* keep the raw text */
      }
      if (res.status === 401) {
        throw keySent
          ? new ThroughlineError("unauthorized", 'The agent key was rejected. Run "Throughline: Set Agent Key" with the key of your account.', detail)
          : new ThroughlineError("sign_in_required", 'Sign-in required. Run "Throughline: Set Agent Key" with the key of your account.', detail);
      }
      const kind = res.status === 403 ? "forbidden" : res.status === 400 || res.status === 422 ? "invalid" : "server";
      throw new ThroughlineError(kind, `Throughline answered ${res.status}`, detail);
    }
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new ThroughlineError("protocol", "Throughline sent an answer that is not JSON");
    }
  }
}
