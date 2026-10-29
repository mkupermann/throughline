import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { FindItem } from "@/lib/api";
import { FindPage } from "./FindPage";

// Reading results without leaving the page. Every result used to be a link and
// nothing else, so inspecting one cost a navigation away and back — and the way
// back lost the scroll position. These tests pin the keyboard path and the one
// thing that made it unreachable in practice: the search box is auto-focused,
// so j and k were typed into the query instead of moving the selection.

function item(id: number, title: string): FindItem {
  return {
    id,
    kind: "memory",
    title,
    snippet: `snippet ${id}`,
    project: "proj",
    category: "pattern",
    status: "active",
    confidence: 0.9,
    occurred_at: "2026-01-05T10:00:00Z",
    conversation_id: null,
    retrievers: 1,
  } as FindItem;
}

const search = vi.fn(async () => ({
  total: 3,
  items: [item(1, "first"), item(2, "second"), item(3, "third")],
  modes: ["lexical"],
  notes: [],
  backend: { available: true },
}));

const detail = vi.fn(async (_kind: string, id: number | string) => ({
  kind: "memory",
  record: { id, content: `body of ${id}` },
  related: {},
}));

vi.mock("@/lib/api", () => ({
  findApi: {
    search: () => search(),
    // Real shape (GET /api/find/facets): one array per dimension. An empty
    // object crashes FacetRail on `values.length`.
    facets: async () => ({
      kinds: [{ value: "memory", n: 3 }],
      categories: [{ value: "pattern", n: 3 }],
      statuses: [{ value: "active", n: 3 }],
      projects: [{ value: "proj", n: 3 }],
      tags: [],
    }),
    detail: (kind: string, id: number | string) => detail(kind, id),
    projectByName: async (n: string) => ({ kind: "project", record: { name: n }, related: {} }),
  },
  providersApi: { list: async () => ({ providers: [] }) },
}));

function renderAt(path = "/find?q=postgres") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <FindPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Find keyboard navigation", () => {
  it("previews the first result without any interaction", async () => {
    renderAt();
    expect(await screen.findByText("body of 1")).toBeTruthy();
  });

  it("does not type j into the query — the search box hands over on ArrowDown", async () => {
    // The regression this guards: the field is auto-focused, so pressing j
    // appended a character to the query and re-ran the search instead of
    // moving. The feature was unreachable without clicking elsewhere first.
    renderAt();
    await screen.findByText("body of 1");

    const input = screen.getByRole("searchbox", { name: /search/i });
    await userEvent.type(input, "{ArrowDown}");
    await userEvent.keyboard("j");

    expect((input as HTMLInputElement).value).toBe("postgres");
    expect(await screen.findByText("body of 2")).toBeTruthy();
  });

  it("j and k walk the list and the preview follows", async () => {
    renderAt();
    await screen.findByText("body of 1");

    await userEvent.type(screen.getByRole("searchbox", { name: /search/i }), "{ArrowDown}");
    await userEvent.keyboard("jj");
    expect(await screen.findByText("body of 3")).toBeTruthy();

    await userEvent.keyboard("k");
    expect(await screen.findByText("body of 2")).toBeTruthy();
  });

  it("stops at the ends rather than wrapping", async () => {
    // Wrapping from the last result back to the first reads as a jump to a
    // random place when the list is longer than the screen.
    renderAt();
    await screen.findByText("body of 1");
    await userEvent.type(screen.getByRole("searchbox", { name: /search/i }), "{ArrowDown}");

    await userEvent.keyboard("kk");
    expect(await screen.findByText("body of 1")).toBeTruthy();

    await userEvent.keyboard("jjjjj");
    expect(await screen.findByText("body of 3")).toBeTruthy();
  });

  it("prefetches the neighbours so moving does not wait on a request", async () => {
    // VIBE's call, and it is right: without this, holding j is a series of
    // round trips and the panel blanks between them.
    renderAt();
    await screen.findByText("body of 1");
    await waitFor(() => {
      const ids = detail.mock.calls.map((c) => String(c[1]));
      expect(ids).toContain("2");
    });
  });
});
