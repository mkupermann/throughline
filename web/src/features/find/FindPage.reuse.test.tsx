import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FindItem } from "@/lib/api";
import { FindPage } from "./FindPage";
import { RECENT_QUERIES_KEY } from "./recentQueries";

const { search, detail, ask, writeText } = vi.hoisted(() => ({
  search: vi.fn(),
  detail: vi.fn(),
  ask: vi.fn(),
  writeText: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  findApi: {
    search,
    facets: async () => ({
      kinds: [{ value: "memory", n: 1 }],
      categories: [{ value: "decision", n: 1 }],
      statuses: [{ value: "active", n: 1 }],
      projects: [{ value: "atlas", n: 1 }],
      tags: [],
    }),
    detail,
    projectByName: async (name: string) => ({ kind: "project", record: { name }, related: {} }),
  },
  askApi: { ask },
}));

const result: FindItem = {
  kind: "memory",
  id: 7,
  title: "Stable ordering",
  snippet: "Load every page before reversing.",
  project: "atlas",
  occurred_at: "2026-01-02T09:00:00Z",
  category: "decision",
  status: "active",
  confidence: 0.95,
  conversation_id: 12,
  score: 0.8,
  retrievers: 2,
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}

function renderAt(path = "/find") {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={[path]}>
        <FindPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Find and Ask reuse", () => {
  beforeEach(() => {
    localStorage.clear();
    search.mockReset();
    detail.mockReset();
    ask.mockReset();
    writeText.mockReset();
    search.mockResolvedValue({
      query: "postgres",
      total: 1,
      limit: 30,
      offset: 0,
      items: [result],
      modes: ["lexical"],
      notes: [],
      backend: { available: true, label: "text" },
    });
    detail.mockResolvedValue({ kind: "memory", record: { id: 7, content: result.snippet }, related: {} });
    ask.mockResolvedValue({
      question: "Why PostgreSQL?",
      answer: "It keeps vectors beside the source records [1].",
      sources: [
        {
          n: 1,
          kind: "memory_chunk",
          id: 7,
          ref: "Database decision",
          project: "atlas",
          category: "decision",
          conversation_id: 12,
          distance: 0.1,
          excerpt: "Keep vectors beside source records.",
        },
      ],
      cited: [1],
      degraded: null,
      backend: "ollama",
      model: "qwen2.5:7b-instruct",
      local: true,
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("separates the Find or Ask intent from Find result layouts", async () => {
    const user = userEvent.setup();
    renderAt();

    const find = screen.getByRole("button", { name: "Find" });
    const askButton = screen.getByRole("button", { name: "Ask" });
    expect(find.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("group", { name: "Result layout" })).toBeTruthy();

    await user.click(askButton);

    expect(askButton.getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("group", { name: "Result layout" })).toBeNull();
    expect(screen.getByRole("searchbox", { name: "Ask your history" })).toBeTruthy();
  });

  it("restores the last Find layout after an Ask round trip", async () => {
    const user = userEvent.setup();
    renderAt();

    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await user.click(screen.getByRole("button", { name: "Find" }));

    expect(screen.getByRole("button", { name: "Table" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("location").textContent).toContain("mode=table");
  });

  it("copies one search result as reusable context and announces success", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderAt("/find?q=postgres");
    await screen.findByRole("button", { name: "Copy context for Stable ordering" });

    await user.click(screen.getByRole("button", { name: "Copy context for Stable ordering" }));

    expect(writeText).toHaveBeenCalledOnce();
    expect(writeText.mock.calls[0][0]).toContain("Source: Throughline memory #7");
    expect(await screen.findByText("Context copied for Stable ordering.")).toBeTruthy();
  });

  it("lets Enter activate Copy context without opening the selected result", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderAt("/find?q=postgres");
    const copy = await screen.findByRole("button", { name: "Copy context for Stable ordering" });

    copy.focus();
    await user.keyboard("{Enter}");

    expect(writeText).toHaveBeenCalledOnce();
    expect(screen.getByTestId("location").textContent).toContain("/find");
  });

  it("replaces the live-region node when the same context is copied twice", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderAt("/find?q=postgres");
    const copy = await screen.findByRole("button", { name: "Copy context for Stable ordering" });

    await user.click(copy);
    const message = await screen.findByText("Context copied for Stable ordering.");
    const status = message.closest('[role="status"]')!;
    const firstAnnouncement = status.firstChild;
    await user.click(copy);

    await waitFor(() => expect(status.firstChild).not.toBe(firstAnnouncement));
    expect(writeText).toHaveBeenCalledTimes(2);
  });

  it("recalls a recent question with its original intent", async () => {
    localStorage.setItem(
      RECENT_QUERIES_KEY,
      JSON.stringify([
        { query: "Why PostgreSQL?", intent: "ask", usedAt: "2026-01-02T09:00:00Z" },
      ]),
    );
    const user = userEvent.setup();
    renderAt();

    await user.click(screen.getByRole("button", { name: "Ask: Why PostgreSQL?" }));

    await waitFor(() =>
      expect((screen.getByRole("searchbox", { name: "Ask your history" }) as HTMLInputElement).value).toBe(
        "Why PostgreSQL?",
      ),
    );
    expect(screen.getByTestId("location").textContent).toContain("mode=ask");
  });

  it("copies a grounded answer and keeps its source directly openable", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderAt("/find?mode=ask&q=Why%20PostgreSQL%3F");

    expect(await screen.findByText(/keeps vectors beside the source records/i, {}, { timeout: 2500 })).toBeTruthy();
    const source = screen.getByRole("link", { name: /Database decision/ });
    expect(source.getAttribute("href")).toBe("/m/7");

    await user.click(screen.getByRole("button", { name: "Copy answer with sources" }));

    expect(writeText).toHaveBeenCalledOnce();
    expect(writeText.mock.calls[0][0]).toContain("## Sources");
    expect(await screen.findByText("Answer and sources copied.")).toBeTruthy();
  });

  it("hides a previous answer as soon as the question changes", async () => {
    const user = userEvent.setup();
    renderAt("/find?mode=ask&q=Why%20PostgreSQL%3F");
    expect(await screen.findByText(/keeps vectors beside the source records/i, {}, { timeout: 2500 })).toBeTruthy();

    const input = screen.getByRole("searchbox", { name: "Ask your history" });
    await user.clear(input);
    await user.type(input, "No");

    await waitFor(() => expect(screen.getByTestId("location").textContent).toContain("q=No"));
    expect(screen.queryByRole("button", { name: "Copy answer with sources" })).toBeNull();
    expect(screen.queryByText(/keeps vectors beside the source records/i)).toBeNull();
  });

  it("records a question when the Ask request starts, not when it later finishes", async () => {
    ask.mockImplementation(() => new Promise(() => undefined));
    renderAt("/find?mode=ask&q=Why%20PostgreSQL%3F");

    await waitFor(() => expect(ask).toHaveBeenCalledOnce(), { timeout: 2500 });
    expect(JSON.parse(localStorage.getItem(RECENT_QUERIES_KEY)!)[0]).toMatchObject({
      query: "Why PostgreSQL?",
      intent: "ask",
    });
  });
});
