import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ProviderBar } from "./ProviderBar";
import { formatCount } from "@/lib/format";

vi.mock("@/lib/api", () => ({
  providersApi: {
    list: async () => ({
      providers: [
        { name: "claude_code", label: "Claude Code", chart_slot: 1, on_disk: 224,
          pending: 126, excluded: 98, ingested: 3016, last_run: null, status: "pending" },
        { name: "hermes", label: "Hermes", chart_slot: 3, on_disk: 33,
          pending: 33, excluded: 0, ingested: 0, last_run: null, status: "not_ingested" },
        { name: "(unattributed)", label: "(unattributed)", chart_slot: 0, on_disk: 0,
          pending: 0, excluded: 0, ingested: 8, last_run: null, status: "unknown" },
      ],
    }),
  },
}));

function LocationProbe() {
  const loc = useLocation();
  return <output data-testid="loc">{loc.search}</output>;
}

function renderAt(path: string) {
  // ProviderBar calls useQuery, which needs a QueryClientProvider ancestor —
  // App.tsx supplies one at the root; this recreates just enough of that for
  // an isolated render. A fresh client per render keeps tests independent.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <ProviderBar />
        <LocationProbe />
        <Routes>
          <Route path="*" element={null} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProviderBar", () => {
  it("shows a chip per provider with its ingested count", async () => {
    renderAt("/find");
    const chip = await screen.findByRole("button", { name: /Claude Code/ });
    // formatCount is locale-aware (Intl.NumberFormat with no locale arg), so
    // the grouping separator depends on the runtime's locale — assert
    // against its actual output rather than assuming en-US's comma.
    expect(within(chip).getByText(formatCount(3016))).toBeTruthy();
  });

  it("marks an un-ingested provider so it cannot be mistaken for empty", async () => {
    renderAt("/find");
    const hermes = await screen.findByRole("button", { name: /Hermes/ });
    expect(hermes.getAttribute("data-status")).toBe("not_ingested");
  });

  it("writes the provider param when a chip is selected", async () => {
    renderAt("/find");
    await userEvent.click(await screen.findByRole("button", { name: /Hermes/ }));
    expect(screen.getByTestId("loc").textContent).toContain("provider=hermes");
  });

  it("toggles a selected chip back off", async () => {
    renderAt("/find?provider=hermes");
    await userEvent.click(await screen.findByRole("button", { name: /Hermes/ }));
    expect(screen.getByTestId("loc").textContent).not.toContain("provider=hermes");
  });

  it("reflects the active scope from the URL, so it is never invisible", async () => {
    renderAt("/find?provider=hermes");
    const hermes = await screen.findByRole("button", { name: /Hermes/ });
    expect(hermes.getAttribute("aria-pressed")).toBe("true");
  });

  it("shows the unattributed count without offering it as a filter", async () => {
    // source_tool IS NULL has no value the `= ANY(...)` provider filter can
    // match, in Find or in Timeline — a clickable chip here would set
    // ?provider=(unattributed) and silently zero out every result. It must
    // show its count (the 8 conversations are real) but never be a button.
    renderAt("/find");
    const count = formatCount(8);
    await screen.findByText(count);
    expect(screen.queryByRole("button", { name: /\(unattributed\)/ })).toBeNull();
  });

  it("never adds provider=(unattributed) to the URL", async () => {
    renderAt("/find");
    await screen.findByText(formatCount(8));
    expect(screen.getByTestId("loc").textContent).not.toContain("unattributed");
  });

  it("renders nothing on Console", async () => {
    // Spec §4.2: raw SQL ignores the scope, and a control that does not affect
    // what you see is worse than none.
    const { container } = renderAt("/console");
    expect(container.querySelector("[data-testid='provider-bar']")).toBeNull();
  });
});
