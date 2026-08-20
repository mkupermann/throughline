import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { JobSummary, OperateStatus, ProviderCoverage } from "@/lib/api";
import { ToastProvider } from "@/components/Toaster";
import { OperatePage } from "./OperatePage";

// jsdom has no EventSource; a run that "succeeds" mounts <JobConsole>, which
// opens one. Only its shape matters here, not the stream itself.
class FakeEventSource {
  addEventListener() {}
  removeEventListener() {}
  close() {}
}
vi.stubGlobal("EventSource", FakeEventSource);

/**
 * Task 12: the Operate table that surfaces un-ingested sources — the
 * counterpart to the Overview attention item. Mirrors ProviderBar.test.tsx's
 * approach (mock @/lib/api, render with just enough providers).
 */

function job(name: string, overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    name,
    title: name,
    description: "",
    danger: null,
    running: false,
    job_id: null,
    unavailable: null,
    ...overrides,
  };
}

const baseStatus: OperateStatus = {
  counts: {},
  database: { reachable: true, tables: {}, dbname: "throughline" },
  extensions: { pgvector_usable: true, note: null },
  embedding: {
    backend: "ollama",
    available: true,
    reason: null,
    coverage: { total: 0, embedded: 0 },
    by_model: [],
  },
  generation: { available: true, backend: "ollama", model: "qwen3.5:9b", local: true, detail: "qwen3.5:9b" },
  pending: { extraction: 0, titles: 0 },
  ingestion: [],
  jobs: [job("doctor"), job("ingest_hermes"), job("ingest_vibe")],
  history: [],
};

const providers: ProviderCoverage[] = [
  { name: "hermes", label: "Hermes", chart_slot: 3, on_disk: 33, pending: 33,
    excluded: 0, ingested: 0, last_run: null, status: "not_ingested" },
  { name: "claude_code", label: "Claude Code", chart_slot: 1, on_disk: 265, pending: 1,
    excluded: 138, ingested: 3142, last_run: "2026-08-09T12:00:00", status: "pending" },
  { name: "(unattributed)", label: "(unattributed)", chart_slot: 0, on_disk: 0, pending: 0,
    excluded: 0, ingested: 8, last_run: null, status: "unknown" },
];

const statusFn = vi.fn(async () => baseStatus);
const listFn = vi.fn(async () => ({ providers }));
const runFn = vi.fn(async (name: string) => ({ job_id: "abc123", name, running: true }));

vi.mock("@/lib/api", () => ({
  operateApi: {
    status: () => statusFn(),
    run: (name: string) => runFn(name),
    stop: vi.fn(),
  },
  providersApi: {
    list: () => listFn(),
  },
  // The page now carries the export panel, which asks for its own options
  // on mount. Without this the whole page fails to render.
  exportApi: {
    options: () =>
      Promise.resolve({
        root: "/Users/dev",
        suggested: "/Users/dev/Throughline-Export",
        job: "export-markdown",
        defaults: { includeGenerated: false, redact: false, toolOutput: 0, memory: true },
      }),
    start: vi.fn(),
  },
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/operate"]}>
          <OperatePage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("OperatePage provider coverage table", () => {
  beforeEach(() => {
    statusFn.mockClear();
    listFn.mockClear();
    runFn.mockClear();
  });

  it("lists every provider with an accessible name and column headers", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: /coverage/i });
    expect(within(table).getByRole("columnheader", { name: "Provider" })).toBeTruthy();
    expect(within(table).getByRole("columnheader", { name: "Pending" })).toBeTruthy();
    expect(within(table).getByRole("rowheader", { name: "Hermes" })).toBeTruthy();
    expect(within(table).getByRole("rowheader", { name: "Claude Code" })).toBeTruthy();
  });

  it("shows status as text, not colour alone", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: /coverage/i });
    const hermesRow = within(table).getByRole("rowheader", { name: "Hermes" }).closest("tr")!;
    const claudeRow = within(table)
      .getByRole("rowheader", { name: "Claude Code" })
      .closest("tr")!;
    expect(within(hermesRow).getByText("Not ingested")).toBeTruthy();
    // "Pending" is also a column header, so this must be scoped to the row's
    // status cell to prove the label and not just any occurrence of the word.
    expect(within(claudeRow).getByText("Pending")).toBeTruthy();
  });

  it("gives excluded a tooltip explaining what it means", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: /coverage/i });
    const header = within(table).getByRole("columnheader", { name: "Excluded" });
    expect(header.getAttribute("title")).toMatch(/subagent transcripts/i);
  });

  it("runs the targeted per-provider ingest job, not a bulk ingest", async () => {
    renderPage();
    await screen.findByRole("table", { name: /coverage/i });
    const hermesRow = screen.getByRole("rowheader", { name: "Hermes" }).closest("tr")!;
    await userEvent.click(within(hermesRow).getByRole("button", { name: /ingest/i }));
    expect(runFn).toHaveBeenCalledWith("ingest_hermes");
  });

  it("does not offer an Ingest control for a row with no matching job (unattributed)", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: /coverage/i });
    const row = within(table).getByRole("rowheader", { name: "(unattributed)" }).closest("tr")!;
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("never auto-ingests: nothing is run just by loading the page", async () => {
    renderPage();
    await screen.findByRole("table", { name: /coverage/i });
    expect(runFn).not.toHaveBeenCalled();
  });
});

describe("Environment", () => {
  it("says which model generates and whether it runs here", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      generation: {
        available: true,
        backend: "ollama",
        model: "qwen3.5:9b",
        local: true,
        detail: "qwen3.5:9b",
      },
    });
    renderPage();

    // Which model extracts memory decides whether transcripts leave the
    // machine. The page showed the embedding model and not this one.
    expect(await screen.findByText("ollama/qwen3.5:9b")).toBeTruthy();
    expect(await screen.findByText(/runs locally/i)).toBeTruthy();
  });

  it("marks a remote generation backend as leaving the machine", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      generation: {
        available: true,
        backend: "openai",
        model: "gpt-4o-mini",
        local: false,
        detail: "api.openai.com",
      },
    });
    renderPage();

    expect(await screen.findByText(/leaves this machine/i)).toBeTruthy();
  });

  it("says so when nothing can generate", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      generation: { available: false, backend: "", model: "", local: false, detail: "No model available." },
    });
    renderPage();

    // The row carries the short verdict, the disclosure below it the reason.
    expect(await screen.findByText("no model available")).toBeTruthy();
    expect(await screen.findByText("No model available.")).toBeTruthy();
  });
});
