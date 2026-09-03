import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsolePage } from "@/features/console/ConsolePage";
import { CuratePage } from "@/features/curate/CuratePage";
import { DetailPage } from "@/features/detail/DetailPage";
import { FindPage } from "@/features/find/FindPage";
import { OperatePage } from "@/features/operate/OperatePage";
import { OverviewPage } from "@/features/overview/OverviewPage";
import { ProjectPage } from "@/features/projects/ProjectPage";
import { TimelinePage } from "@/features/timeline/TimelinePage";
import { ToastProvider } from "@/components/Toaster";

type FetchReply = Response | Error | Promise<Response>;

const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>();

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderRoute(ui: React.ReactNode, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function reply(paths: Record<string, FetchReply>) {
  fetchMock.mockImplementation(async (input) => {
    const path = String(input);
    const match = Object.entries(paths).find(([prefix]) => path.startsWith(prefix));
    if (!match) throw new Error(`Unexpected request: ${path}`);
    const result = await match[1];
    if (result instanceof Error) throw result;
    return result;
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("route API states", () => {
  it("shows database-unavailable guidance on Overview through the real API client", async () => {
    reply({ "/api/overview": new TypeError("network down") });
    renderRoute(<OverviewPage />);

    expect(await screen.findByRole("heading", { name: "Cannot load the overview" })).toBeTruthy();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/overview");
  });

  it("shows the Find failure state when its search endpoint is unavailable", async () => {
    reply({
      "/api/find/facets": json({ kinds: [], categories: [], statuses: [], projects: [], tags: [] }),
      "/api/find?": new TypeError("network down"),
    });
    renderRoute(<FindPage />, "/find?q=postgres");

    expect(await screen.findByRole("heading", { name: "Search failed" })).toBeTruthy();
    expect(fetchMock.mock.calls.some(([path]) => String(path).startsWith("/api/find?"))).toBe(true);
  });

  it("shows an unavailable state instead of an empty Timeline when its range request fails", async () => {
    reply({
      "/api/providers": json({ providers: [] }),
      "/api/timeline?": new TypeError("network down"),
    });
    renderRoute(<TimelinePage />, "/timeline");

    expect(await screen.findByRole("heading", { name: "Cannot load the timeline" })).toBeTruthy();
    expect(screen.queryByText("No activity in this range.")).toBeNull();
  });

  it("keeps Timeline's loading state distinct from a completed empty range", async () => {
    let release!: (response: Response) => void;
    const pendingRange = new Promise<Response>((resolve) => {
      release = resolve;
    });
    reply({
      "/api/providers": json({ providers: [] }),
      "/api/timeline?": pendingRange,
    });
    renderRoute(<TimelinePage />, "/timeline");

    expect(await screen.findByText("Loading…")).toBeTruthy();
    expect(screen.queryByText("No activity in this range.")).toBeNull();

    release(json({ since: "2026-05-18", until: "2026-08-16", bucket: "day", cells: [] }));
    expect(await screen.findByText("No activity in this range.")).toBeTruthy();
  });

  it("shows a day-detail failure instead of claiming the selected day has no events", async () => {
    reply({
      "/api/providers": json({ providers: [] }),
      "/api/timeline?": json({
        since: "2026-08-16", until: "2026-08-16", bucket: "day",
        cells: [{ bucket: "2026-08-16", provider: "hermes", kind: "conversation", n: 1 }],
      }),
      "/api/timeline/day/2026-08-16?": new TypeError("network down"),
    });
    renderRoute(<TimelinePage />, "/timeline");

    expect(await screen.findByRole("heading", { name: "Cannot load events for this day" })).toBeTruthy();
    expect(screen.queryByText(/No events on 2026-08-16/)).toBeNull();
  });

  it("shows Find's initial loading state before an active query returns", async () => {
    let release!: (response: Response) => void;
    const pendingSearch = new Promise<Response>((resolve) => {
      release = resolve;
    });
    reply({
      "/api/find/facets": json({ kinds: [], categories: [], statuses: [], projects: [], tags: [] }),
      "/api/find?": pendingSearch,
    });
    renderRoute(<FindPage />, "/find?q=postgres");

    expect(await screen.findByText("Searching…")).toBeTruthy();
    release(json({
      query: "postgres", items: [], total: 0, limit: 50, offset: 0,
      modes: ["lexical"], notes: [], backend: { available: true, label: "text" },
    }));
    expect(await screen.findByRole("heading", { name: /No results for “postgres”/ })).toBeTruthy();
  });

  it("shows Curate's unavailable state instead of claiming an empty queue", async () => {
    reply({ "/api/curate/queues": new TypeError("network down") });
    renderRoute(
      <ToastProvider>
        <CuratePage />
      </ToastProvider>,
      "/curate",
    );

    expect(await screen.findByRole("heading", { name: "Cannot load curation queues" })).toBeTruthy();
    expect(screen.queryByText("Nothing in this queue")).toBeNull();
  });

  it("shows the selected zero-count queue as empty", async () => {
    reply({
      "/api/curate/queues": json({
        queues: [
          { name: "contradictions", title: "Contradictions", description: "Potential conflicts", count: 0, severity: "warning", actions: [] },
          { name: "low-confidence", title: "Low confidence", description: "Needs review", count: 0, severity: "info", actions: [] },
        ],
        total: 0,
      }),
      "/api/curate/queue/contradictions": json({
        name: "contradictions", title: "Contradictions", description: "Potential conflicts", count: 0, severity: "warning", actions: [], items: [],
      }),
    });
    renderRoute(
      <ToastProvider>
        <CuratePage />
      </ToastProvider>,
      "/curate",
    );

    expect(await screen.findByRole("heading", { name: "Nothing in this queue" })).toBeTruthy();
    expect(screen.getAllByText("Potential conflicts")).toHaveLength(2);
  });

  it("shows a project-unavailable state instead of an empty history", async () => {
    reply({ "/api/projects/Atlas/sessions?": new TypeError("network down") });
    renderRoute(
      <Routes>
        <Route path="/project/:name" element={<ProjectPage />} />
      </Routes>,
      "/project/Atlas",
    );

    expect(await screen.findByRole("heading", { name: "Could not load project history" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "No sessions yet" })).toBeNull();
  });

  it("shows Operate's degraded pgvector state from the returned status", async () => {
    reply({
      "/api/operate/status": json({
        counts: {},
        database: { reachable: true, tables: {}, dbname: "throughline" },
        extensions: { pgvector_usable: false, note: "pgvector extension is unavailable" },
        embedding: { backend: "ollama", available: false, reason: "Ollama is offline", coverage: { total: 0, embedded: 0 }, by_model: [] },
        pending: { extraction: 0, titles: 0 },
        ingestion: [], providers: [], pipeline: [], jobs: [], history: [],
      }),
    });
    renderRoute(
      <ToastProvider>
        <OperatePage />
      </ToastProvider>,
      "/operate",
    );

    expect(await screen.findByText("pgvector extension is unavailable")).toBeTruthy();
    expect(screen.getByText("Ollama is offline")).toBeTruthy();
  });

  it("shows provider coverage as unavailable when an older status response omits it", async () => {
    reply({
      "/api/operate/status": json({
        counts: {},
        database: { reachable: true, tables: {}, dbname: "throughline" },
        extensions: { pgvector_usable: true, note: null },
        embedding: { backend: "ollama", available: true, reason: null, coverage: { total: 0, embedded: 0 }, by_model: [] },
        pending: { extraction: 0, titles: 0 },
        ingestion: [], pipeline: [], jobs: [], history: [],
      }),
    });
    renderRoute(
      <ToastProvider>
        <OperatePage />
      </ToastProvider>,
      "/operate",
    );

    expect(await screen.findByRole("heading", { name: "Provider coverage unavailable" })).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("keeps Console usable but names an unavailable schema endpoint", async () => {
    reply({ "/api/console/schema": new TypeError("network down") });
    renderRoute(<ConsolePage />, "/console");

    expect(await screen.findByText(/^Schema unavailable:/)).toBeTruthy();
  });

  it("shows Detail's not-found state from the API status", async () => {
    reply({ "/api/detail/memory/404": json({ error: "not_found", detail: "Memory chunk 404 was not found." }, 404) });
    renderRoute(
      <Routes>
        <Route path="/m/:id" element={<DetailPage kind="memory" />} />
      </Routes>,
      "/m/404",
    );

    expect(await screen.findByRole("heading", { name: "Not found" })).toBeTruthy();
    expect(screen.getByText("Memory chunk 404 was not found.")).toBeTruthy();
  });

  it("renders a memory as a memory — content first, human labels, linked source", async () => {
    reply({
      "/api/detail/memory/2740": json({
        kind: "memory",
        record: {
          id: 2740,
          source_type: "conversation",
          source_id: 5668,
          content: "Kein 'RAZOR 1911 TRIBUTE' String",
          category: "error_solution",
          tags: ["criterion-5-fail"],
          confidence: "1.00",
          project_name: null,
          expires_at: null,
          created_at: "2026-08-25T19:03:41.200081+00:00",
          superseded_by: null,
          superseded_at: null,
          status: "active",
          merged_from: [],
          access_count: 0,
          last_accessed: null,
        },
        related: {},
      }),
    });
    renderRoute(
      <Routes>
        <Route path="/m/:id" element={<DetailPage kind="memory" />} />
      </Routes>,
      "/m/2740",
    );

    // The category, humanised, is the page title — not a raw identifier.
    expect(await screen.findByRole("heading", { level: 1, name: "Error solution" })).toBeTruthy();
    expect(screen.queryByText("error_solution")).toBeNull();
    // Content leads.
    expect(screen.getByText("Kein 'RAZOR 1911 TRIBUTE' String")).toBeTruthy();
    // The source conversation is a real link, not a bare id field.
    const sourceLinks = screen.getAllByRole("link", { name: /conversation #5668/i });
    expect(sourceLinks.some((a) => a.getAttribute("href") === "/c/5668")).toBe(true);
    // Breadcrumb + raw JSON escape hatch.
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeTruthy();
    expect(screen.getByText("Raw data")).toBeTruthy();
    // No raw snake_case field labels in the metadata list (the collapsed
    // raw-JSON escape hatch is allowed to contain them — that is its job).
    const labels = Array.from(document.querySelectorAll("dt")).map((d) => d.textContent ?? "");
    expect(labels.length).toBeGreaterThan(0);
    expect(labels.every((l) => !l.includes("_"))).toBe(true);
  });

  it("renders a conversation as a conversation — title, meta, transcript with human roles", async () => {
    reply({
      "/api/detail/conversation/6084": json({
        kind: "conversation",
        record: {
          id: 6084,
          session_id: "421dee03",
          project_path: "C:\\repo",
          project_name: "razor1911-demo-tribute",
          model: null,
          entrypoint: "",
          git_branch: "master",
          started_at: "2026-08-25T20:17:18+00:00",
          ended_at: "2026-08-25T20:18:07+00:00",
          message_count: 2,
          token_count_in: 365390,
          token_count_out: 3552,
          cost_usd: null,
          summary: null,
          tags: [],
          metadata: { title: "Demo review session", source: "vibe", stats: { session_cost: 0.12 } },
        },
        related: {
          messages: [
            { id: 1, role: "user", content: "Please review the demo", created_at: "2026-08-25T20:17:19+00:00" },
            { id: 2, role: "assistant", content: "Reviewing now", created_at: "2026-08-25T20:17:30+00:00" },
          ],
          message_total: 2,
          message_offset: 0,
          message_returned: 2,
          has_more: false,
          chunks: [],
        },
      }),
    });
    renderRoute(
      <Routes>
        <Route path="/c/:id" element={<DetailPage kind="conversation" />} />
      </Routes>,
      "/c/6084",
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Demo review session" })).toBeTruthy();
    // The project is a real link to its ProjectPage.
    const projLinks = screen.getAllByRole("link", { name: "razor1911-demo-tribute" });
    expect(projLinks.some((a) => a.getAttribute("href") === "/project/razor1911-demo-tribute")).toBe(true);
    // Transcript with humanised role labels, not raw column values.
    expect(screen.getByText("Transcript")).toBeTruthy();
    expect(screen.getByText("User")).toBeTruthy();
    expect(screen.getByText("Assistant")).toBeTruthy();
    // Token counts formatted through Intl.
    expect(screen.getByText("365,390")).toBeTruthy();
  });

  it("renders a skill as a skill — name, description, triggers, use stats", async () => {
    reply({
      "/api/detail/skill/1": json({
        kind: "skill",
        record: {
          id: 1,
          name: "sharepoint-video-downloader",
          version: "1.0.0",
          description: "Downloads SharePoint Stream videos.",
          path: "/Users/mk/.claude/skills/sharepoint-video-downloader",
          triggers: ["download video from sharepoint"],
          last_used: null,
          use_count: 0,
          config: { skill_type: "global" },
          created_at: "2026-04-17T13:05:32+00:00",
          updated_at: "2026-06-07T21:00:00+00:00",
          file_created: null,
          file_modified: null,
        },
        related: {},
      }),
    });
    renderRoute(
      <Routes>
        <Route path="/s/:id" element={<DetailPage kind="skill" />} />
      </Routes>,
      "/s/1",
    );

    expect(
      await screen.findByRole("heading", { level: 1, name: "sharepoint-video-downloader" }),
    ).toBeTruthy();
    expect(screen.getByText("Never used")).toBeTruthy();
    expect(screen.getByText("Downloads SharePoint Stream videos.")).toBeTruthy();
    expect(screen.getByText("download video from sharepoint")).toBeTruthy();
    const labels = Array.from(document.querySelectorAll("dt")).map((d) => d.textContent ?? "");
    expect(labels.every((l) => !l.includes("_"))).toBe(true);
  });
});
