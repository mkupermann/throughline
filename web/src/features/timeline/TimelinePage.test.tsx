import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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

vi.mock("@/lib/api", () => ({
  timelineApi: {
    range: (qs: URLSearchParams) => range(qs),
    day: (d: string, qs: URLSearchParams) => day(d, qs),
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
  it("renders one lane per provider present in the range", async () => {
    renderAt();
    expect(await screen.findByText("claude_code")).toBeTruthy();
    expect(screen.getByText("hermes")).toBeTruthy();
  });

  it("gives non-provider sources their own lane rather than dropping them", async () => {
    // Spec §5.3: all eight of the old Calendar's sources stay reachable.
    renderAt();
    expect(await screen.findByText(/not tool-specific/i)).toBeTruthy();
  });

  it("requests a date range, never a page", async () => {
    renderAt();
    await screen.findByText("claude_code");
    const qs = String(range.mock.calls.at(-1)?.[0] ?? "");
    expect(qs).toContain("since=");
    expect(qs).toContain("until=");
    expect(qs).not.toContain("page=");
    expect(qs).not.toContain("offset=");
  });

  it("carries the provider scope into the query", async () => {
    renderAt("/timeline?provider=hermes");
    await screen.findByText("hermes");
    expect(String(range.mock.calls.at(-1)?.[0] ?? "")).toContain("provider=hermes");
  });

  it("changing the range refetches", async () => {
    renderAt();
    await screen.findByText("claude_code");
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

  // §5.1: "Clicking a cell is what loads rows." These four cover the
  // drill-down: a day cell loads that day, a week/month cell cannot (a
  // single date can't represent a week or month) so it zooms instead, the
  // active provider scope must reach the detail request, and an empty day
  // must say so rather than render nothing.

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
      name: /^claude_code, 2026-01-05, 12 events$/,
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
      name: /^claude_code, 2026-02-01, 40 events$/,
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

  it("carries the active provider scope into the detail request", async () => {
    renderAt("/timeline?provider=hermes");
    const cell = await screen.findByRole("button", {
      name: /^hermes, 2026-01-05, 3 events$/,
    });
    await userEvent.click(cell);
    await screen.findByRole("region", { name: /events on 2026-01-05/i });
    const qs = String(day.mock.calls.at(-1)?.[1] ?? "");
    expect(qs).toContain("provider=hermes");
  });

  it("shows a message rather than a blank panel when a clicked day has no events", async () => {
    day.mockResolvedValueOnce({ day: "2026-01-05", items: [] });
    renderAt();
    const cell = await screen.findByRole("button", {
      name: /^claude_code, 2026-01-05, 12 events$/,
    });
    await userEvent.click(cell);
    expect(await screen.findByText(/no events on 2026-01-05/i)).toBeTruthy();
  });
});
