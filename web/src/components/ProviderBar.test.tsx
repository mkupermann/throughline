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

  it("renders nothing on Console", async () => {
    // Spec §4.2: raw SQL ignores the scope, and a control that does not affect
    // what you see is worse than none.
    const { container } = renderAt("/console");
    expect(container.querySelector("[data-testid='provider-bar']")).toBeNull();
  });
});
