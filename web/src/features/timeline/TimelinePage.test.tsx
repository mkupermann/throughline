import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { TimelineRange, TimelineDayItem } from "@/lib/api";
import { TimelinePage } from "./TimelinePage";

// Typed with its real arguments (rather than `(...a: unknown[]) => ...`) so
// `.mock.calls.at(-1)?.[0]` below is a `URLSearchParams`, not an element of
// an inferred zero-length tuple. The explicit `Promise<TimelineRange>`
// return type (rather than letting `bucket: "day"` narrow to that literal)
// is what lets `mockResolvedValueOnce` below hand back a "month" bucket.
const range = vi.fn(async (_qs: URLSearchParams): Promise<TimelineRange> => ({
  since: "2026-01-01",
  until: "2026-03-31",
  bucket: "day",
  cells: [
    { bucket: "2026-01-05", provider: "claude_code", kind: "conversation", n: 12 },
    { bucket: "2026-01-05", provider: "hermes", kind: "conversation", n: 3 },
    { bucket: "2026-01-05", provider: "not_tool_specific", kind: "skill", n: 5 },
    { bucket: "2026-01-05", provider: "unattributed", kind: "conversation", n: 1 },
    { bucket: "2026-02-01", provider: "not_tool_specific", kind: "skill", n: 2 },
  ],
}));

const day = vi.fn(async (
  _day: string,
  _qs: URLSearchParams,
): Promise<{ day: string; items: TimelineDayItem[] }> => ({
  day: "2026-01-05",
  items: [],
}));

// Provider labels come from the same /providers endpoint ProviderBar reads
// (shared queryKey, see TimelinePage's providersData) — the registry lives
// server-side (throughline/providers.py), not duplicated in this file.
const providersList = vi.fn(async () => ({
  providers: [
    { name: "claude_code", label: "Claude Code", chart_slot: 1, on_disk: 0, pending: 0,
      excluded: 0, ingested: 0, last_run: null, status: "ok" as const },
    { name: "hermes", label: "Hermes", chart_slot: 3, on_disk: 0, pending: 0,
      excluded: 0, ingested: 0, last_run: null, status: "ok" as const },
  ],
}));

vi.mock("@/lib/api", () => ({
  timelineApi: {
    range: (qs: URLSearchParams) => range(qs),
    day: (d: string, qs: URLSearchParams) => day(d, qs),
  },
  providersApi: {
    list: () => providersList(),
  },
}));

function renderAt(path = "/timeline") {
  // TimelinePage calls useQuery, which needs a QueryClientProvider ancestor —
  // App.tsx supplies one at the root; this recreates just enough of that for
  // an isolated render (same pattern as ProviderBar.test.tsx).
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <TimelinePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TimelinePage", () => {
  it("renders one lane per provider present in the range, labelled from the registry", async () => {
    // Fix: lane labels used to bypass the registry and show the raw
    // source_tool value ("claude_code") while the provider chips showed the
    // registry label ("Claude Code") for the exact same provider.
    renderAt();
    expect(await screen.findByText("Claude Code")).toBeTruthy();
    expect(screen.getByText("Hermes")).toBeTruthy();
    expect(screen.queryByText("claude_code")).toBeNull();
  });

  it("gives non-provider sources their own lane rather than dropping them", async () => {
    // Spec §5.3: all eight of the old Calendar's sources stay reachable.
    // not_tool_specific has no registry entry, so it keeps its own label.
    renderAt();
    expect(await screen.findByText(/not tool-specific/i)).toBeTruthy();
  });

  it("requests a date range, never a page", async () => {
    renderAt();
    await screen.findByText("Claude Code");
    const qs = String(range.mock.calls.at(-1)?.[0] ?? "");
    expect(qs).toContain("since=");
    expect(qs).toContain("until=");
    expect(qs).not.toContain("page=");
    expect(qs).not.toContain("offset=");
  });

  it("carries the provider scope into the query", async () => {
    renderAt("/timeline?provider=hermes");
    await screen.findByText("Hermes");
    expect(String(range.mock.calls.at(-1)?.[0] ?? "")).toContain("provider=hermes");
  });

  it("changing the range refetches", async () => {
    renderAt();
    await screen.findByText("Claude Code");
    const before = range.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: /last year|1y/i }));
    expect(range.mock.calls.length).toBeGreaterThan(before);
  });

  it("shows an empty state rather than a blank grid", async () => {
    range.mockResolvedValueOnce({
      since: "2026-01-01", until: "2026-03-31", bucket: "day", cells: [],
    });
    renderAt();
    expect(await screen.findByText(/no activity in this range/i)).toBeTruthy();
  });

  // ── Date axis (Fix: the grid had lane labels and cells but no dates) ────

  it("renders a date-axis header row above the lanes", async () => {
    renderAt();
    await screen.findByText("Claude Code");
    const table = screen.getByRole("table", { name: /activity by provider over time/i });
    // The axis row is the first row, marked aria-hidden (it duplicates what
    // each cell's own aria-label already states) but still visibly present.
    const rows = within(table).getAllByRole("row", { hidden: true });
    expect(rows[0].getAttribute("aria-hidden")).toBe("true");
    // 2026-01-05 is a bucket present in the mocked range; its thinned label
    // is "01-05" (day bucket -> MM-DD, see TimelinePage's axisLabel).
    expect(within(rows[0]).getByText("01-05")).toBeTruthy();
  });

  // ── Cell click loads that day's rows (§5.1) ─────────────────────────────
  // These cover the drill-down: a day cell loads that day scoped to its OWN
  // lane (not every provider), a week/month cell zooms instead, the
  // not-tool-specific lane's cell can't carry a provider filter at all, and
  // an empty day must say so rather than render nothing.

  it("a day-bucket cell click issues a day request and renders rows", async () => {
    day.mockResolvedValueOnce({
      day: "2026-01-05",
      items: [
        {
          id: 1, kind: "conversation", provider: "claude_code",
          ts: "2026-01-05T10:00:00Z", title: "Planning session",
        },
      ],
    });
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^Claude Code, 2026-01-05, 12 events$/,
    });
    await userEvent.click(cell);
    expect(day.mock.calls.at(-1)?.[0]).toBe("2026-01-05");
    expect(await screen.findByText("Planning session")).toBeTruthy();
  });

  it("a month-bucket cell click narrows the range instead of requesting a single day", async () => {
    range.mockResolvedValueOnce({
      since: "2026-01-01",
      until: "2026-12-31",
      bucket: "month",
      cells: [{ bucket: "2026-02-01", provider: "claude_code", kind: "conversation" as const, n: 40 }],
    });
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^Claude Code, 2026-02-01, 40 events$/,
    });
    const rangeCallsBefore = range.mock.calls.length;
    const dayCallsBefore = day.mock.calls.length;
    await userEvent.click(cell);
    // A single date cannot stand for a month — no day request goes out...
    expect(day.mock.calls.length).toBe(dayCallsBefore);
    // ...instead the range narrows to that month's span and refetches.
    expect(range.mock.calls.length).toBeGreaterThan(rangeCallsBefore);
    const qs = String(range.mock.calls.at(-1)?.[0] ?? "");
    expect(qs).toContain("since=2026-02-01");
    expect(qs).toContain("until=2026-02-28");
  });

  it("scopes the detail request to the clicked lane, not the app-wide provider scope", async () => {
    // Fix: clicking the Hermes row's cell used to open that day's detail for
    // EVERY provider — dayQs was built from the (here, empty) app-wide scope,
    // never from which lane was actually clicked.
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^Hermes, 2026-01-05, 3 events$/,
    });
    await userEvent.click(cell);
    await screen.findByRole("region", { name: /events on 2026-01-05/i });
    const qs = String(day.mock.calls.at(-1)?.[1] ?? "");
    expect(qs).toBe("provider=hermes");
  });

  it("a different lane's click scopes to that lane, proving it isn't a fixed value", async () => {
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^Claude Code, 2026-01-05, 12 events$/,
    });
    await userEvent.click(cell);
    const qs = String(day.mock.calls.at(-1)?.[1] ?? "");
    expect(qs).toBe("provider=claude_code");
  });

  it("carries the active app-wide provider scope when it matches the clicked lane", async () => {
    renderAt("/timeline?provider=hermes");
    const cell = await screen.findByRole("button", {
      name: /^Hermes, 2026-01-05, 3 events$/,
    });
    await userEvent.click(cell);
    await screen.findByRole("region", { name: /events on 2026-01-05/i });
    const qs = String(day.mock.calls.at(-1)?.[1] ?? "");
    expect(qs).toContain("provider=hermes");
  });

  it("the unattributed lane's cell click carries that provider, not an unfiltered request", async () => {
    // Regression: this lane used to skip the provider filter client-side
    // (there being no way to express source_tool IS NULL through the old
    // filter), so its click came back with every provider's rows for that
    // day mixed in — the cell said "1 events" but the panel showed rows
    // belonging to other lanes too. queries/timeline.py now understands
    // "unattributed" as a real filter value (source_tool IS NULL), so the
    // client sends it like any other lane.
    day.mockResolvedValueOnce({
      day: "2026-01-05",
      items: [
        {
          id: 9, kind: "conversation", provider: "unattributed",
          ts: "2026-01-05T09:00:00Z", title: "No recorded tool",
        },
      ],
    });
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^\(unattributed\), 2026-01-05, 1 events$/,
    });
    await userEvent.click(cell);
    await screen.findByText("No recorded tool");
    const qs = String(day.mock.calls.at(-1)?.[1] ?? "");
    expect(qs).toBe("provider=unattributed");
  });

  it("the not-tool-specific lane's cell click carries no provider filter", async () => {
    // Kinds with no provider column vanish from day_detail entirely if any
    // provider filter is present (throughline/queries/timeline.py's
    // day_detail: `if provider_col is None: if providers: continue`), so
    // this lane must never get one.
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^not tool-specific, 2026-01-05, 5 events$/,
    });
    await userEvent.click(cell);
    await screen.findByRole("region", { name: /events on 2026-01-05/i });
    const qs = String(day.mock.calls.at(-1)?.[1] ?? "");
    expect(qs).not.toContain("provider=");
  });

  it("shows a message rather than a blank panel when a clicked day has no events", async () => {
    day.mockResolvedValueOnce({ day: "2026-01-05", items: [] });
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^Claude Code, 2026-01-05, 12 events$/,
    });
    await userEvent.click(cell);
    expect(await screen.findByText(/no events on 2026-01-05/i)).toBeTruthy();
  });

  it("shows how many of the cell's total are displayed when the detail response is truncated", async () => {
    // Fix: `/timeline/day/{date}` caps at 100 rows and the panel showed no
    // total and no indication of truncation — a cell whose aria-label said
    // "8600 events" opened a silently truncated 100-row list.
    day.mockResolvedValueOnce({
      day: "2026-01-05",
      items: Array.from({ length: 2 }, (_, i) => ({
        id: i, kind: "conversation" as const, provider: "claude_code",
        ts: "2026-01-05T10:00:00Z", title: `Item ${i}`,
      })),
    });
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^Claude Code, 2026-01-05, 12 events$/,
    });
    await userEvent.click(cell);
    await screen.findByText("Item 0");
    // The cell's own count (12) is the authority, not items.length (2).
    expect(await screen.findByText(/showing 2 of 12/i)).toBeTruthy();
  });
});
