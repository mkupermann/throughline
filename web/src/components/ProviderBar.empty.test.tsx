import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ProviderBar } from "./ProviderBar";

// Which chips the bar shows. Throughline registers nine adapters and nobody
// runs all nine, so six of them read "0" on a typical machine — six filters
// whose only possible outcome is an empty result, occupying the top strip of
// every page. This file pins what earns a chip.

vi.mock("@/lib/api", () => ({
  providersApi: {
    list: async () => ({
      providers: [
        // Has data — always shown.
        { name: "claude_code", label: "Claude Code", chart_slot: 1, on_disk: 224,
          pending: 0, excluded: 98, ingested: 3016, last_run: null, status: "ok" },
        // Nothing ingested yet, but files are waiting — this is exactly when
        // the user needs to see it, and the dot is how they find out.
        { name: "codex", label: "Codex", chart_slot: 4, on_disk: 12,
          pending: 12, excluded: 0, ingested: 0, last_run: null, status: "not_ingested" },
        // Registered, but nothing here and nothing waiting.
        { name: "zed", label: "Zed", chart_slot: 6, on_disk: 0,
          pending: 0, excluded: 0, ingested: 0, last_run: null, status: "no_data" },
        { name: "cursor", label: "Cursor", chart_slot: 5, on_disk: 0,
          pending: 0, excluded: 0, ingested: 0, last_run: null, status: "no_data" },
        // The residue row: always shown, never a filter.
        { name: "(unattributed)", label: "(unattributed)", chart_slot: 0, on_disk: 0,
          pending: 0, excluded: 0, ingested: 8, last_run: null, status: "unknown" },
      ],
    }),
  },
}));

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <ProviderBar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProviderBar hides adapters with nothing here", () => {
  it("keeps providers that hold data or have imports waiting", async () => {
    renderAt("/find");
    // Await a chip, not the bar: the bar renders immediately with no data and
    // fills in when the query resolves.
    expect(await screen.findByRole("button", { name: /Claude Code/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Codex/ })).toBeTruthy();
    expect(screen.getByText("(unattributed)")).toBeTruthy();
  });

  it("drops providers with nothing ingested and nothing pending", async () => {
    renderAt("/find");
    await screen.findByRole("button", { name: /Claude Code/ });
    expect(screen.queryByRole("button", { name: /Zed/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Cursor/ })).toBeNull();
  });

  it("says how many it dropped, rather than silently showing fewer", async () => {
    // "Supports nine assistants" and a bar showing three must not contradict
    // each other with nothing on screen to explain the gap.
    renderAt("/find");
    await screen.findByRole("button", { name: /Claude Code/ });
    expect(screen.getByText(/\+2 with no data here/)).toBeTruthy();
  });

  it("keeps an empty provider that the scope is currently filtered to", async () => {
    // Otherwise the filter stays applied while the control to clear it is
    // gone — visible effect, invisible cause.
    renderAt("/find?provider=zed");
    const chip = await screen.findByRole("button", { name: /Zed/ });
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/\+1 with no data here/)).toBeTruthy();
  });

  it("does not appear on Overview at all", async () => {
    // /api/overview never reads the provider parameter — the chips returned
    // identical totals filtered and unfiltered. A control that highlights and
    // rewrites the URL while changing nothing on screen is worse than no
    // control: the reader concludes the numbers are scoped when they are not.
    const { container } = renderAt("/");
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector("[data-testid='provider-bar']")).toBeNull();
  });
});
