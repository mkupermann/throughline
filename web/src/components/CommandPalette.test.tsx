import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/lib/theme";
import { ToastProvider } from "@/components/Toaster";
import { CommandPalette } from "./CommandPalette";

// jsdom provides neither of these; cmdk observes its list, and the theme
// provider asks for the system colour preference.
// cmdk scrolls the highlighted item into view; jsdom has no layout.
Element.prototype.scrollIntoView = () => {};

// The palette now queries live data for its "Jump to" group and its job
// availability check — stub both so a test typing into the query box stays
// deterministic and offline, same as FindPage's own tests.
const search = vi.fn(async () => ({
  total: 0,
  items: [] as unknown[],
  modes: [] as string[],
  notes: [] as string[],
  backend: { available: true },
}));
const run = vi.fn(async (name: string) => ({ job_id: "test-job", name, running: true }));

vi.mock("@/lib/api", () => ({
  findApi: { search: () => search() },
  operateApi: {
    status: async () => ({ jobs: [] }),
    run: (name: string) => run(name),
  },
}));

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
  dispatchEvent: () => false,
}));

function open() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ThemeProvider>
          <ToastProvider>
            <CommandPalette />
          </ToastProvider>
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CommandPalette", () => {
  beforeEach(() => {
    search.mockClear();
    run.mockClear();
  });

  it("offers the Markdown export by name", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");

    // A feature reachable only by scrolling one page is a feature people ask
    // for again because they could not find it.
    expect(await screen.findByText(/export as markdown/i)).toBeTruthy();
  });

  it("applies the visible panel geometry to the dialog itself", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");

    expect((await screen.findByRole("dialog", { name: "Command palette" })).classList).toContain(
      "palette",
    );
  });

  it("organises navigation under the same task groups as the sidebar", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");

    expect(await screen.findByText("Work")).toBeTruthy();
    expect(screen.getByText("Trust")).toBeTruthy();
    expect(screen.getByText("System")).toBeTruthy();
    expect(screen.getAllByText("Project Management").length).toBeGreaterThan(0);
    expect(screen.getByText("Review")).toBeTruthy();
    expect(screen.queryByText("Curate")).toBeNull();
  });

  it("finds the export when searching for Obsidian", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByPlaceholderText(/jump to/i), "obsidian");

    expect(await screen.findByText(/export as markdown/i)).toBeTruthy();
  });

  it("offers job actions that run without a destination or a selection", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");

    const item = await screen.findByText(/run: ingest sessions/i);
    await user.click(item);

    expect(run).toHaveBeenCalledWith("ingest");
  });

  it("jumps straight to a specific record for a longer query", async () => {
    search.mockResolvedValueOnce({
      total: 1,
      items: [
        {
          kind: "memory",
          id: 42,
          title: "Use ruff for linting",
          snippet: null,
          project: "acme-web",
          occurred_at: null,
          category: "preference",
          status: "active",
          confidence: 0.9,
          conversation_id: null,
          score: 1,
          retrievers: 1,
        },
      ],
      modes: ["lexical"],
      notes: [],
      backend: { available: true },
    });

    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByPlaceholderText(/jump to/i), "ruff");

    expect(await screen.findByText("Use ruff for linting")).toBeTruthy();
  });

  it("does not query for a jump target until two characters are typed", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByPlaceholderText(/jump to/i), "r");

    // Give any debounce a moment to fire, then confirm it didn't.
    await new Promise((r) => setTimeout(r, 300));
    expect(search).not.toHaveBeenCalled();
  });
});
