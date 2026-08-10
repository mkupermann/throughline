/** Typed client for the Throughline API.
 *
 * Hand-written for Phase 1 while the surface is one endpoint. Phase 2
 * replaces this with a client generated from /api/openapi.json so the
 * contract cannot drift silently.
 */

export type Severity = "critical" | "warning" | "info";
export type Verdict = "ok" | "degraded" | "broken";

export interface AttentionItem {
  id: string;
  severity: Severity;
  title: string;
  detail: string;
  count: number | null;
  action: string | null;
  action_label: string | null;
}

export interface Overview {
  headline: { label: string; value: number; sublabel: string };
  verdict: Verdict;
  verdict_reason: string;
  attention: AttentionItem[];
  activity: { day: string; n: number }[];
  totals: Record<string, number>;
}

/** An API error carrying the server's structured payload, not just a status. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly hint?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
  } catch (cause) {
    // Server not running at all — distinct from a server that answered.
    throw new ApiError(
      0,
      "unreachable",
      "Cannot reach the Throughline server.",
      "Is `throughline serve` still running?",
    );
  }

  if (!res.ok) {
    let code = "http_error";
    let detail = res.statusText;
    let hint: string | undefined;
    try {
      const body = await res.json();
      code = body.error ?? code;
      detail = body.detail ?? detail;
      hint = body.hint;
    } catch {
      /* non-JSON error body — keep the status text */
    }
    throw new ApiError(res.status, code, detail, hint);
  }

  return (await res.json()) as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/health"),
  overview: () => request<Overview>("/overview"),
};


// ── Providers ────────────────────────────────────────────────────────────

export interface ProviderCoverage {
  name: string;
  label: string;
  chart_slot: number;
  on_disk: number;
  pending: number;
  excluded: number;
  ingested: number;
  last_run: string | null;
  status: "ok" | "pending" | "not_ingested" | "no_data" | "unknown";
}

export const providersApi = {
  list: () => request<{ providers: ProviderCoverage[] }>("/providers"),
};


// ── Find ─────────────────────────────────────────────────────────────────

export type Kind = "conversation" | "message" | "memory" | "skill" | "project" | "prompt";

export interface FindItem {
  kind: Kind;
  id: number;
  title: string | null;
  snippet: string | null;
  project: string | null;
  occurred_at: string | null;
  category: string | null;
  status: string | null;
  confidence: number | null;
  conversation_id: number | null;
  score: number;
  retrievers: number;
}

export interface FindResponse {
  query: string;
  items: FindItem[];
  total: number;
  limit: number;
  offset: number;
  modes: string[];
  notes: string[];
  backend: { available: boolean; label: string };
}

export interface FacetValue {
  value: string;
  n: number;
}

export interface Facets {
  kinds: FacetValue[];
  categories: FacetValue[];
  statuses: FacetValue[];
  projects: FacetValue[];
  tags: FacetValue[];
}

export interface DetailResponse {
  kind: string;
  record: Record<string, unknown>;
  related: Record<string, Record<string, unknown>[]>;
}

export interface GraphNodeDTO {
  id: number;
  name: string;
  entity_type: string;
  project_name: string | null;
  mention_count: number;
  confidence: number | null;
  hits_in_results: number;
}
export interface GraphEdgeDTO {
  from_entity: number;
  to_entity: number;
  relation_type: string;
  confidence: number | null;
}

export const findApi = {
  search: (params: URLSearchParams) => request<FindResponse>(`/find?${params.toString()}`),
  facets: () => request<Facets>("/find/facets"),
  graph: (sources: [string, number][]) =>
    request<{ nodes: GraphNodeDTO[]; edges: GraphEdgeDTO[] }>("/find/graph", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources }),
    }),
  detail: (kind: string, id: number | string) =>
    request<DetailResponse>(`/detail/${kind}/${id}`),
  projectByName: (name: string) =>
    request<DetailResponse>(`/detail/project/by-name/${encodeURIComponent(name)}`),
};


// ── Curate ───────────────────────────────────────────────────────────────

export interface QueueSummary {
  name: string;
  title: string;
  description: string;
  count: number;
  severity: string;
  actions: string[];
}

export interface CurateItem {
  id: number;
  category?: string | null;
  content?: string | null;
  confidence?: number | null;
  project_name?: string | null;
  created_at?: string | null;
  status?: string | null;
  access_count?: number | null;
  expires_at?: string | null;
  reasoning?: string | null;
  action_taken?: string | null;
  superseded_by?: number | null;
}

export interface ActResult {
  changed: number;
  undo_token: string | null;
  message: string;
  affected_ids: number[];
}

export const curateApi = {
  queues: () => request<{ queues: QueueSummary[]; total: number }>("/curate/queues"),
  queue: (name: string) =>
    request<QueueSummary & { items: CurateItem[] }>(`/curate/queue/${name}`),
  act: (body: { action: string; ids: number[]; reason?: string; value?: number }) =>
    request<ActResult>("/curate/act", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // One key per submission, so a retried or double-clicked request
        // applies once rather than stacking two inverses.
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify(body),
    }),
  createChunk: (body: {
    content: string;
    category: string;
    project_name?: string | null;
    tags?: string[];
    confidence?: number;
  }) =>
    request<{ id: number; undo_token: string | null; message: string }>("/curate/chunk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  categories: () => request<{ categories: string[] }>("/curate/categories"),
  undo: (token: string) =>
    request<{ changed: number; message: string }>("/curate/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }),
};

// ── Operate ──────────────────────────────────────────────────────────────

export interface JobSummary {
  name: string;
  title: string;
  description: string;
  danger: string | null;
  running: boolean;
  job_id: string | null;
  unavailable: string | null;
}

export interface OperateStatus {
  counts: Record<string, number>;
  database: Record<string, unknown> & { reachable: boolean; tables: Record<string, number> };
  extensions: { pgvector_usable: boolean; note: string | null };
  embedding: {
    backend: string;
    available: boolean;
    reason: string | null;
    coverage: { total: number; embedded: number };
    by_model: Record<string, unknown>[];
  };
  pending: { extraction: number; titles: number };
  ingestion: Record<string, unknown>[];
  jobs: JobSummary[];
  history: Record<string, unknown>[];
}

export const operateApi = {
  status: () => request<OperateStatus>("/operate/status"),
  run: (name: string) =>
    request<{ job_id: string; name: string; running: boolean }>(`/operate/run/${name}`, {
      method: "POST",
    }),
  stop: (jobId: string) =>
    request<{ stopped: string }>(`/operate/stop/${jobId}`, { method: "POST" }),
  job: (jobId: string) =>
    request<{ id: string; running: boolean; returncode: number | null; lines: string[]; duration_s: number }>(
      `/operate/job/${jobId}`,
    ),
};


// ── Console ──────────────────────────────────────────────────────────────

export interface ConsoleResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  duration_ms: number;
  notices: string[];
  error: string | null;
  error_hint: string | null;
}

export interface SchemaTable {
  name: string;
  columns: { name: string; type: string }[];
}

export const consoleApi = {
  query: (sql: string, maxRows = 1000) =>
    request<ConsoleResult>("/console/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql, max_rows: maxRows }),
    }),
  schema: () =>
    request<{
      tables: SchemaTable[];
      enums: { name: string; values: string[] }[];
      snippets: { title: string; sql: string }[];
    }>("/console/schema"),
};


// ── Timeline ─────────────────────────────────────────────────────────────

export interface TimelineCell {
  bucket: string;
  /** A provider name, `"unattributed"`, or `"not_tool_specific"` (§5.3). */
  provider: string;
  kind: Kind;
  n: number;
}

export interface TimelineRange {
  since: string;
  until: string;
  bucket: "day" | "week" | "month";
  cells: TimelineCell[];
}

export const timelineApi = {
  range: (qs: URLSearchParams) => request<TimelineRange>(`/timeline?${qs}`),
  day: (day: string, qs: URLSearchParams) =>
    request<{ day: string; items: FindItem[] }>(`/timeline/day/${day}?${qs}`),
};
