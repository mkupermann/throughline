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
  /** Chunk counts per memory category, already sorted descending by the API. */
  categories: { category: string; n: number }[];
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
    // `...init` spreads first so a caller's own keys (method, body) apply,
    // and `headers` is assigned after the spread so the merged object wins.
    // The other order silently dropped Accept the moment any caller passed a
    // header of its own — which nothing did until /ask needed content-type.
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
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

export interface AskSource {
  n: number;
  kind: string;
  id: number;
  ref: string;
  project: string | null;
  category: string | null;
  conversation_id: number | null;
  distance: number | null;
  excerpt: string;
}

export interface AskResponse {
  question: string;
  answer: string;
  sources: AskSource[];
  cited: number[];
  degraded: string | null;
  backend: string;
  model: string;
  /** True when the model ran on this machine and no excerpt left it. */
  local: boolean;
}

export interface ProjectSummary {
  project: string;
  sessions: number;
  messages: number;
  first_active: string | null;
  last_active: string | null;
  tools: number;
  tool_names: string[];
}

export interface ProjectSession {
  id: number;
  session_id: string;
  title: string | null;
  message_count: number;
  started_at: string | null;
  ended_at: string | null;
  source_tool: string | null;
  model: string | null;
  git_branch: string | null;
  generated_by: string | null;
}

export interface ProjectSessions {
  project: string;
  order: string;
  q: string | null;
  sessions: ProjectSession[];
  total: number;
  offset: number;
  has_more: boolean;
  include_generated: boolean;
  /** Machine-generated sessions withheld from the list but still stored. */
  hidden_generated: number;
}

export const projectsApi = {
  recent: (days = 7) =>
    request<{ days: number; projects: ProjectSummary[] }>(`/projects/recent?days=${days}`),
  sessions: (
    project: string,
    opts: {
      order?: string;
      q?: string;
      limit?: number;
      offset?: number;
      includeGenerated?: boolean;
    } = {},
  ) => {
    const p = new URLSearchParams();
    if (opts.order) p.set("order", opts.order);
    if (opts.q) p.set("q", opts.q);
    if (opts.limit) p.set("limit", String(opts.limit));
    if (opts.offset) p.set("offset", String(opts.offset));
    if (opts.includeGenerated) p.set("include_generated", "true");
    // The name is a path segment and may contain spaces or dots — "The
    // FireScore Website" is a real project here.
    return request<ProjectSessions>(
      `/projects/${encodeURIComponent(project)}/sessions?${p.toString()}`,
    );
  },
};

export const askApi = {
  // POST, not GET: a question is prose, and keeping it out of the URL keeps it
  // out of access logs and browser history — this tool's whole subject is the
  // user's private working history.
  ask: (body: { question: string; top_k?: number; project?: string | null }) =>
    request<AskResponse>("/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
};

export const findApi = {
  search: (params: URLSearchParams) => request<FindResponse>(`/find?${params.toString()}`),
  facets: () => request<Facets>("/find/facets"),
  graph: (sources: [string, number][]) =>
    request<{ nodes: GraphNodeDTO[]; edges: GraphEdgeDTO[] }>("/find/graph", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources }),
    }),
  detail: (kind: string, id: number | string, page?: { offset: number; limit: number }) =>
    request<DetailResponse>(
      page
        ? `/detail/${kind}/${id}?msg_offset=${page.offset}&msg_limit=${page.limit}`
        : `/detail/${kind}/${id}`,
    ),
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

/**
 * The Timeline's kind vocabulary is wider than Find's. `Kind` has six values
 * and drives an exhaustive `Record<Kind, string>` in ResultList.tsx — Find's
 * search endpoint can never return more than that. `/api/timeline` reads
 * nine tables (throughline/queries/timeline.py's `_SOURCES`), three of which
 * — entity, reflection, ingestion — Find never surfaces. Widening `Kind`
 * itself would force every exhaustive switch/map keyed on it to grow branches
 * Find can never hit, so the Timeline gets its own, wider union instead.
 */
export type TimelineKind = Kind | "entity" | "reflection" | "ingestion";

export interface TimelineCell {
  bucket: string;
  /** A provider name, `"unattributed"`, or `"not_tool_specific"` (§5.3). */
  provider: string;
  kind: TimelineKind;
  n: number;
}

export interface TimelineRange {
  since: string;
  until: string;
  bucket: "day" | "week" | "month";
  cells: TimelineCell[];
}

/**
 * One row from `GET /timeline/day/{date}` — verified against
 * `_detail_columns()` and the SELECT built in `day_detail()` in
 * throughline/queries/timeline.py, which every kind's branch of that query
 * aliases to exactly these five columns. This is NOT a `FindItem`: the
 * declaration `items: FindItem[]` that used to sit on `timelineApi.day()`
 * was wrong in the same way `TimelineCell.kind: Kind` was — a shape
 * TypeScript could not check because JSON arrives as strings.
 */
export interface TimelineDayItem {
  id: number;
  kind: TimelineKind;
  /** A provider name, `"unattributed"`, or `"not_tool_specific"` (§5.3). */
  provider: string;
  ts: string;
  title: string;
  /** The conversation this row belongs to, or null when it belongs to none.
   *  Needed because `id` on a message row is the MESSAGE id — routing a
   *  message by its own id would open an unrelated conversation. */
  conversation_id: number | null;
}

export const timelineApi = {
  range: (qs: URLSearchParams) => request<TimelineRange>(`/timeline?${qs}`),
  day: (day: string, qs: URLSearchParams) =>
    request<{ day: string; items: TimelineDayItem[] }>(`/timeline/day/${day}?${qs}`),
};
