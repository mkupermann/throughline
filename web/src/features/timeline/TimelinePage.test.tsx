import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { TimelinePage } from "./TimelinePage";

// Typed with its real argument (rather than `(...a: unknown[]) => ...`) so
// `range.mock.calls.at(-1)?.[0]` below is a `URLSearchParams`, not an element
// of an inferred zero-length tuple.
const range = vi.fn(async (_qs: URLSearchParams) => ({
  since: "2026-01-01",
  until: "2026-03-31",
  bucket: "day" as const,
  cells: [
    { bucket: "2026-01-05", provider: "claude_code", kind: "conversation" as const, n: 12 },
    { bucket: "2026-01-05", provider: "hermes", kind: "conversation" as const, n: 3 },
    { bucket: "2026-02-01", provider: "not_tool_specific", kind: "skill" as const, n: 2 },
  ],
}));

vi.mock("@/lib/api", () => ({
  timelineApi: {
    range: (qs: URLSearchParams) => range(qs),
    day: async () => ({ day: "2026-01-05", items: [] }),
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
});
