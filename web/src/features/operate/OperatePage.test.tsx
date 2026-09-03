import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { JobSummary, OperateStatus, PipelineStage, ProviderCoverage } from "@/lib/api";
import { ToastProvider } from "@/components/Toaster";
import { OperatePage } from "./OperatePage";

// jsdom has no EventSource; a run that "succeeds" mounts <JobConsole>, which
// opens one. Only its shape matters here, not the stream itself.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  private listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type: string, data: string) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data }));
    }
  }

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

const providers: ProviderCoverage[] = [
  { name: "hermes", label: "Hermes", chart_slot: 3, on_disk: 33, pending: 33,
    excluded: 0, ingested: 0, last_run: null, status: "not_ingested" },
  { name: "claude_code", label: "Claude Code", chart_slot: 1, on_disk: 265, pending: 1,
    excluded: 138, ingested: 3142, last_run: "2026-08-09T12:00:00", status: "pending" },
  { name: "(unattributed)", label: "(unattributed)", chart_slot: 0, on_disk: 0, pending: 0,
    excluded: 0, ingested: 8, last_run: null, status: "unknown" },
];

function stage(
  key: PipelineStage["key"],
  label: string,
  overrides: Partial<PipelineStage> = {},
): PipelineStage {
  return {
    key,
    label,
    state: "healthy",
    detail: `${label} is current.`,
    last_success: "2026-09-03T08:00:00+00:00",
    blocked_reason: null,
    job_name: key === "discover" ? null : key === "review" ? "audit-extraction" : key,
    job_id: null,
    action_label: label,
    action_href: key === "discover" ? "#provider-coverage" : null,
    ...overrides,
  };
}

const basePipeline: PipelineStage[] = [
  stage("discover", "Discover sources"),
  stage("ingest", "Ingest sessions"),
  stage("extract", "Extract knowledge"),
  stage("embed", "Create embeddings"),
  stage("review", "Review quality"),
];

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
  providers,
  pipeline: basePipeline,
  jobs: [
    job("ingest"),
    job("extract"),
    job("embed"),
    job("audit-extraction"),
    job("doctor"),
    job("ingest_hermes"),
    job("ingest_vibe"),
  ],
  history: [],
};

const statusFn = vi.fn(async () => baseStatus);
const runFn = vi.fn(async (name: string) => ({ job_id: "abc123", name, running: true }));
const stopFn = vi.fn(async (id: string) => ({ stopped: id }));

vi.mock("@/lib/api", () => ({
  operateApi: {
    status: () => statusFn(),
    run: (name: string) => runFn(name),
    stop: (id: string) => stopFn(id),
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
    FakeEventSource.instances = [];
    statusFn.mockClear();
    statusFn.mockResolvedValue(baseStatus);
    runFn.mockClear();
    stopFn.mockClear();
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

describe("Knowledge pipeline", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    statusFn.mockResolvedValue(baseStatus);
    runFn.mockClear();
    stopFn.mockClear();
  });

  it("shows the five stages in their operating order", async () => {
    renderPage();
    const pipeline = await screen.findByRole("list", { name: "Knowledge pipeline" });
    const steps = within(pipeline).getAllByRole("listitem");

    expect(steps.map((step) => within(step).getByRole("heading", { level: 3 }).textContent)).toEqual([
      "Discover sources",
      "Ingest sessions",
      "Extract knowledge",
      "Create embeddings",
      "Review quality",
    ]);
  });

  it("makes the next due action explicit", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      pipeline: basePipeline.map((item) =>
        item.key === "ingest"
          ? stage("ingest", "Ingest sessions", {
              state: "due",
              detail: "2 session files are waiting to import.",
              action_label: "Ingest sessions",
            })
          : item,
      ),
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Ingest sessions" }));
    expect(runFn).toHaveBeenCalledWith("ingest");
  });

  it("shows why a stage is blocked and disables its action", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      pipeline: basePipeline.map((item) =>
        item.key === "embed"
          ? stage("embed", "Create embeddings", {
              state: "blocked",
              detail: "Create embeddings cannot run in the current environment.",
              blocked_reason: "Ollama is not running.",
            })
          : item,
      ),
    });
    renderPage();

    expect(await screen.findByText("Ollama is not running.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Create embeddings" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("offers a retry after a failed stage", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      pipeline: basePipeline.map((item) =>
        item.key === "extract"
          ? stage("extract", "Extract knowledge", {
              state: "failed",
              detail: "The last extract knowledge run failed.",
              blocked_reason: "Last run exited with code 2.",
              action_label: "Retry extract knowledge",
            })
          : item,
      ),
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Retry extract knowledge" }));
    expect(runFn).toHaveBeenCalledWith("extract");
  });

  it("can stop a running pipeline stage", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      pipeline: basePipeline.map((item) =>
        item.key === "ingest"
          ? stage("ingest", "Ingest sessions", {
              state: "running",
              detail: "Ingest sessions is running now.",
              job_id: "run-1",
            })
          : item,
      ),
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Stop ingest sessions" }));
    expect(stopFn).toHaveBeenCalledWith("run-1");
  });

  it("keeps low-frequency jobs in a collapsed secondary section", async () => {
    renderPage();
    const summary = await screen.findByText("Advanced maintenance");
    const details = summary.closest("details")!;

    expect(details.open).toBe(false);
    expect(within(details).getByRole("heading", { name: "doctor" })).toBeTruthy();
    expect(within(details).queryByRole("heading", { name: "ingest" })).toBeNull();
  });

  it("offers Stop immediately after an advanced job starts", async () => {
    const user = userEvent.setup();
    renderPage();
    const summary = await screen.findByText("Advanced maintenance");
    await user.click(summary);
    const details = summary.closest("details")!;

    await user.click(within(details).getByRole("button", { name: "Run" }));

    expect(await within(details).findByRole("button", { name: "Stop" })).toBeTruthy();
    expect(runFn).toHaveBeenCalledWith("doctor");
  });

  it("tracks a provider-specific ingest through the pipeline and announces completion", async () => {
    const user = userEvent.setup();
    renderPage();
    const table = await screen.findByRole("table", { name: /coverage/i });
    const hermesRow = within(table).getByRole("rowheader", { name: "Hermes" }).closest("tr")!;

    await user.click(within(hermesRow).getByRole("button", { name: /ingest/i }));

    expect(await screen.findByRole("button", { name: "Stop ingest sessions" })).toBeTruthy();
    expect(screen.getByText("ingest_hermes started.")).toBeTruthy();
    expect(FakeEventSource.instances[0]?.url).toBe("/api/operate/job/abc123/stream");

    FakeEventSource.instances[0].emit("done", "exit=0 duration=1.0s");

    expect(await screen.findByText("ingest_hermes completed. Pipeline status refreshed.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Stop ingest sessions" })).toBeNull();
  });

  it("keeps all jobs available when an older backend has no pipeline payload", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      pipeline: undefined as unknown as PipelineStage[],
    });
    renderPage();

    const summary = await screen.findByText("Available jobs");
    const details = summary.closest("details")!;
    expect(details.open).toBe(true);
    expect(within(details).getByRole("heading", { name: "ingest" })).toBeTruthy();
    expect(within(details).getByRole("heading", { name: "extract" })).toBeTruthy();
    expect(within(details).getByRole("heading", { name: "embed" })).toBeTruthy();
  });

  it("does not duplicate a pipeline recovery job in advanced maintenance", async () => {
    statusFn.mockResolvedValue({
      ...baseStatus,
      pipeline: basePipeline.map((item) =>
        item.key === "discover"
          ? stage("discover", "Discover sources", {
              state: "failed",
              job_name: "doctor",
              action_label: "Run diagnostics",
              action_href: null,
            })
          : item,
      ),
    });
    renderPage();

    expect(await screen.findByRole("button", { name: "Run diagnostics" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "doctor" })).toBeNull();
    expect(screen.queryByText("Advanced maintenance")).toBeNull();
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
