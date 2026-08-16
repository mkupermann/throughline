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

  it("shows a real empty state when the queue index has no queues", async () => {
    reply({ "/api/curate/queues": json({ queues: [], total: 0 }) });
    renderRoute(
      <ToastProvider>
        <CuratePage />
      </ToastProvider>,
      "/curate",
    );

    expect(await screen.findByRole("heading", { name: "Nothing to curate" })).toBeTruthy();
    expect(screen.queryByText("Nothing in this queue")).toBeNull();
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
      "/api/providers": json({ providers: [] }),
      "/api/operate/status": json({
        counts: {},
        database: { reachable: true, tables: {}, dbname: "throughline" },
        extensions: { pgvector_usable: false, note: "pgvector extension is unavailable" },
        embedding: { backend: "ollama", available: false, reason: "Ollama is offline", coverage: { total: 0, embedded: 0 }, by_model: [] },
        pending: { extraction: 0, titles: 0 },
        ingestion: [], jobs: [], history: [],
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
});
