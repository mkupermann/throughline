import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/Toaster";
import type { AuditStatus, CurateItem, QueueSummary } from "@/lib/api";
import { CuratePage } from "./CuratePage";

// Which queue Curate opens on. The default was the literal "low-confidence",
// so on a healthy database the surface opened on an empty queue reading
// "Nothing in this queue" while hundreds of items waited two tabs away — a
// worklist showing an empty list looks exactly like one with no work in it.

function summary(name: string, title: string, count: number): QueueSummary {
  return { name, title, description: `${title} description`, count, severity: "info", actions: [] };
}

const queues = vi.fn(async () => ({ queues: [] as QueueSummary[], total: 0 }));
const act = vi.fn(
  async (_body: { action: string; ids: number[]; value?: number }) => ({
    changed: 2,
    undo_token: null,
    message: "done",
    affected_ids: [] as number[],
  }),
);
const queue = vi.fn(async (name: string) => ({
  ...summary(name, name, 0),
  // Typed explicitly: an empty literal infers as `never[]`, so a test that
  // supplies real items later fails to compile against its own fixture.
  items: [] as CurateItem[],
}));
const auditStatus = vi.fn(async (): Promise<AuditStatus> => ({
  last_run: null,
  job: {
    name: "audit-extraction",
    title: "Run drift audit",
    description: "Check sampled memory against its source conversations.",
    danger: null,
    running: false,
    job_id: null,
    unavailable: null,
  },
}));
const runAudit = vi.fn(async (_name: string) => ({
  job_id: "audit-job-1",
  name: "audit-extraction",
  running: true,
}));

vi.mock("@/lib/api", () => ({
  curateApi: {
    queues: () => queues(),
    queue: (name: string) => queue(name),
    act: (b: { action: string; ids: number[]; value?: number }) => act(b),
    create: vi.fn(),
    audit: () => auditStatus(),
    categories: async () => ({ categories: [] }),
  },
  operateApi: {
    run: (name: string) => runAudit(name),
  },
  providersApi: { list: async () => ({ providers: [] }) },
}));

vi.mock("@/features/operate/JobConsole", () => ({
  JobConsole: ({ onFinished }: { onFinished: (result: { ok: boolean }) => void }) => (
    <>
      <button type="button" onClick={() => onFinished({ ok: true })}>Finish audit job</button>
      <button type="button" onClick={() => onFinished({ ok: false })}>Fail audit job</button>
    </>
  ),
}));

function renderAt(path = "/curate") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <CuratePage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("CuratePage default queue", () => {
  beforeEach(() => {
    queues.mockClear();
    queue.mockClear();
  });

  it("opens the first queue that holds something", async () => {
    queues.mockResolvedValue({
      queues: [
        summary("contradictions", "Contradictions", 0),
        summary("low-confidence", "Low confidence", 0),
        summary("never-accessed", "Never accessed", 474),
      ],
      total: 474,
    });

    renderAt();

    await waitFor(() => expect(queue).toHaveBeenCalledWith("never-accessed"));
    expect(queue).not.toHaveBeenCalledWith("low-confidence");
  });

  it("respects an explicit ?queue= even when that queue is empty", async () => {
    // A bookmarked or shared link must land where it says, including on an
    // empty queue the user deliberately asked to see.
    queues.mockResolvedValue({
      queues: [
        summary("low-confidence", "Low confidence", 0),
        summary("never-accessed", "Never accessed", 474),
      ],
      total: 474,
    });

    renderAt("/curate?queue=low-confidence");

    await waitFor(() => expect(queue).toHaveBeenCalledWith("low-confidence"));
    expect(queue).not.toHaveBeenCalledWith("never-accessed");
  });

  it("falls back to the first queue when every queue is empty", async () => {
    // Nothing to curate is the good case; showing the first queue's empty
    // state is then correct rather than misleading.
    queues.mockResolvedValue({
      queues: [
        summary("contradictions", "Contradictions", 0),
        summary("low-confidence", "Low confidence", 0),
      ],
      total: 0,
    });

    renderAt();

    await waitFor(() => expect(queue).toHaveBeenCalledWith("contradictions"));
    expect(await screen.findByText(/Nothing in this queue/i)).toBeTruthy();
  });
});

describe("CuratePage destructive actions", () => {
  beforeEach(() => {
    queues.mockClear();
    queue.mockClear();
  });

  async function withItems() {
    queues.mockResolvedValue({
      queues: [summary("low-confidence", "Low confidence", 2)],
      total: 2,
    });
    queue.mockResolvedValue({
      ...summary("low-confidence", "Low confidence", 2),
      // The action buttons come from the queue's own `actions` list.
      actions: ["forget", "raise_confidence"],
      items: [
        { id: 1, content: "first", category: "pattern", confidence: 0.2 },
        { id: 2, content: "second", category: "pattern", confidence: 0.3 },
      ],
    });
    renderAt();
    await screen.findByText("first");
  }

  it("asks before forgetting, and says how many", async () => {
    // Undo exists, but it lives in a toast that scrolls away — a poor place
    // to keep the only way back from a bulk action nobody was warned about.
    await withItems();
    await userEvent.click(screen.getByRole("checkbox", { name: /select all/i }));
    await userEvent.click(screen.getByRole("button", { name: /^forget$/i }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/forget 2 chunks\?/i)).toBeTruthy();
    expect(act).not.toHaveBeenCalled();
  });

  it("cancelling changes nothing", async () => {
    await withItems();
    await userEvent.click(screen.getByRole("checkbox", { name: /select all/i }));
    await userEvent.click(screen.getByRole("button", { name: /^forget$/i }));
    await userEvent.click(await screen.findByRole("button", { name: /cancel/i }));

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(act).not.toHaveBeenCalled();
  });

  it("confirming sends exactly the selected ids", async () => {
    await withItems();
    await userEvent.click(screen.getByRole("checkbox", { name: /select all/i }));
    await userEvent.click(screen.getByRole("button", { name: /^forget$/i }));
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /forget 2/i }));

    expect(act).toHaveBeenCalledWith(expect.objectContaining({ action: "forget", ids: [1, 2] }));
  });

  it("does not gate an adjustment", async () => {
    // Confirming harmless actions teaches the reader to click through the
    // ones that are not.
    await withItems();
    await userEvent.click(screen.getByRole("checkbox", { name: /select all/i }));
    await userEvent.click(screen.getByRole("button", { name: /set confidence/i }));

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(act).toHaveBeenCalled();
  });
});

describe("CuratePage drift audit", () => {
  beforeEach(() => {
    auditStatus.mockReset();
    runAudit.mockReset();
    auditStatus.mockResolvedValue({
      last_run: null,
      job: {
        name: "audit-extraction",
        title: "Run drift audit",
        description: "Check sampled memory against its source conversations.",
        danger: null,
        running: false,
        job_id: null,
        unavailable: null,
      },
    });
    runAudit.mockResolvedValue({
      job_id: "audit-job-1",
      name: "audit-extraction",
      running: true,
    });
    queues.mockResolvedValue({
      queues: [summary("drift", "Drift audit hits", 0)],
      total: 0,
    });
    queue.mockResolvedValue({
      ...summary("drift", "Drift audit hits", 0),
      items: [],
    });
  });

  it("starts a visible drift audit and refreshes Review when it finishes", async () => {
    const user = userEvent.setup();
    renderAt();

    expect(await screen.findByText("No drift audit has run yet.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Run drift audit" }));
    expect(runAudit).toHaveBeenCalledWith("audit-extraction");
    await user.click(await screen.findByRole("button", { name: "Finish audit job" }));
    await waitFor(() => expect(auditStatus.mock.calls.length).toBeGreaterThan(1));
  });

  it("shows the last sample and its actionable findings", async () => {
    auditStatus.mockResolvedValue({
      last_run: {
        id: 9,
        created_at: "2026-09-01T10:00:00Z",
        sampled: 20,
        drifted: 2,
        state: "findings",
      },
      job: {
        name: "audit-extraction",
        title: "Run drift audit",
        description: "Check sampled memory against its source conversations.",
        danger: null,
        running: false,
        job_id: null,
        unavailable: null,
      },
    });
    renderAt();

    expect(await screen.findByText("2 of 20 sampled chunks need review.")).toBeTruthy();
  });

  it("asks for a safe rerun when a legacy audit cannot identify its findings", async () => {
    auditStatus.mockResolvedValue({
      last_run: {
        id: 8,
        created_at: "2026-08-31T10:00:00Z",
        sampled: 20,
        drifted: 2,
        state: "findings",
        findings_available: false,
      },
      job: {
        name: "audit-extraction",
        title: "Run drift audit",
        description: "Check sampled memory against its source conversations.",
        danger: null,
        running: false,
        job_id: null,
        unavailable: null,
      },
    });
    renderAt();

    expect(
      await screen.findByText(
        "The last legacy audit found 2 possible issues. Run again to identify them safely.",
      ),
    ).toBeTruthy();
  });

  it("explains a completed audit with no eligible samples", async () => {
    auditStatus.mockResolvedValue({
      last_run: {
        id: 10,
        created_at: "2026-09-01T10:00:00Z",
        sampled: 0,
        drifted: 0,
        state: "no-samples",
      },
      job: {
        name: "audit-extraction",
        title: "Run drift audit",
        description: "Check sampled memory against its source conversations.",
        danger: null,
        running: false,
        job_id: null,
        unavailable: null,
      },
    });
    renderAt();

    expect(await screen.findByText("No eligible source-linked memory was available.")).toBeTruthy();
  });

  it("disables the audit when the server reports a blocker", async () => {
    auditStatus.mockResolvedValue({
      last_run: null,
      job: {
        name: "audit-extraction",
        title: "Run drift audit",
        description: "Check sampled memory against its source conversations.",
        danger: null,
        running: false,
        job_id: null,
        unavailable: "PostgreSQL is unavailable.",
      },
    });
    renderAt();

    const button = await screen.findByRole("button", { name: "Run drift audit" });
    expect(await screen.findByText("PostgreSQL is unavailable.")).toBeTruthy();
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("reports a failed start without hiding the audit controls", async () => {
    runAudit.mockRejectedValue(new Error("Audit could not start."));
    const user = userEvent.setup();
    renderAt();
    await screen.findByText("No drift audit has run yet.");

    await user.click(screen.getByRole("button", { name: "Run drift audit" }));

    expect(await screen.findByText("Audit could not start.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run drift audit" })).toBeTruthy();
  });

  it("does not claim Review is current when the audit process fails", async () => {
    const user = userEvent.setup();
    renderAt();
    await screen.findByText("No drift audit has run yet.");
    await user.click(screen.getByRole("button", { name: "Run drift audit" }));

    await user.click(await screen.findByRole("button", { name: "Fail audit job" }));

    expect(await screen.findByText("Drift audit failed. Review was not updated.")).toBeTruthy();
    expect(screen.queryByText("Drift audit finished. Review is up to date.")).toBeNull();
  });
});
