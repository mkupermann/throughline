import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectPage } from "./ProjectPage";

const { context, sessions } = vi.hoisted(() => ({
  context: vi.fn(),
  sessions: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ projectsApi: { context, sessions } }));

const firstMessage = {
  id: 1,
  role: "user",
  content: "First message",
  content_blocks: null,
  tool_calls: null,
  tool_name: null,
  model: null,
  created_at: "2026-01-01T09:00:00Z",
  conversation_id: 12,
  conversation_title: "First session",
  conversation_started_at: "2026-01-01T09:00:00Z",
  generated_by: null,
};

const projectContext = {
  project: "atlas",
  summary: "2 sessions, 2 messages",
  sessionCount: 2,
  messageCount: 2,
  total: 1,
  offset: 0,
  limit: 500,
  complete: true,
  order: "oldest" as const,
  includeGenerated: false,
  knowledge: [
    {
      id: 7,
      type: "memory" as const,
      category: "decision",
      content: "Use stable ordering",
      confidence: 0.95,
      source_type: "conversation",
      source_id: 12,
    },
    {
      id: 8,
      type: "memory" as const,
      category: "preference",
      content: "Keep the API small",
      confidence: 0.85,
      source_type: "manual",
      source_id: null,
    },
  ],
  messages: [firstMessage],
};

const sessionRows = [
  {
    id: 12,
    session_id: "session-12",
    title: "First session",
    message_count: 24,
    started_at: "2026-01-02T09:00:00Z",
    ended_at: "2026-01-02T09:45:00Z",
    source_tool: "claude_code",
    model: "sonnet",
    git_branch: "feature/atlas",
    generated_by: null,
  },
  {
    id: 11,
    session_id: "session-11",
    title: "Earlier session",
    message_count: 3,
    started_at: "2026-01-01T09:00:00Z",
    ended_at: "2026-01-01T09:05:00Z",
    source_tool: "cursor",
    model: null,
    git_branch: null,
    generated_by: null,
  },
];

function sessionResponse(offset = 0, rows = sessionRows) {
  return {
    project: "atlas",
    order: "newest",
    q: null,
    sessions: rows,
    total: 3,
    offset,
    has_more: offset + rows.length < 3,
    include_generated: false,
    hidden_generated: 5,
  };
}

function renderPage(entry = "/project/atlas") {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/project/:name" element={<ProjectPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectPage", () => {
  beforeEach(() => {
    context.mockReset();
    sessions.mockReset();
    context.mockResolvedValue(projectContext);
    sessions.mockImplementation(async (_project, options) =>
      options?.offset
        ? sessionResponse(options.offset, [
            {
              ...sessionRows[1],
              id: 10,
              session_id: "session-10",
              title: "Oldest session",
            },
          ])
        : sessionResponse(),
    );
  });

  it("opens a source-linked project document before the session index", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Knowledge" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Decision" })).toBeTruthy();
    expect(screen.getByText("Use stable ordering")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open source conversation" }).getAttribute("href")).toBe(
      "/c/12",
    );
    expect(screen.getByText("Source: manual")).toBeTruthy();
    expect(screen.getByRole("link", { name: /first session/i }).getAttribute("href")).toBe("/c/12");
    expect(sessions).not.toHaveBeenCalled();
  });

  it("renders tool calls and their complete payload instead of an empty message", async () => {
    context.mockResolvedValue({
      ...projectContext,
      messages: [
        {
          ...firstMessage,
          role: "assistant",
          content: null,
          content_blocks: [
            { type: "tool_use", name: "Read", input: { file_path: "src/app.ts" } },
          ],
          tool_calls: [{ name: "Read" }],
          tool_name: "Read",
          model: "qwen2.5:7b-instruct",
        },
      ],
    });

    renderPage();

    expect(await screen.findByText("Read")).toBeTruthy();
    expect(screen.getByText("src/app.ts")).toBeTruthy();
    expect(screen.getByText("qwen2.5:7b-instruct")).toBeTruthy();
  });

  it("loads the complete transcript before reversing it", async () => {
    context.mockImplementation(async (_project, options) => {
      const offset = options?.offset ?? 0;
      return {
        ...projectContext,
        total: 2,
        offset,
        complete: offset === 1,
        messages: [
          offset === 1
            ? {
                ...firstMessage,
                id: 2,
                content: "Second message",
                created_at: "2026-01-02T09:00:00Z",
              }
            : firstMessage,
        ],
      };
    });
    renderPage();
    await screen.findByText("First message");

    await userEvent.click(screen.getByRole("button", { name: /newest first/i }));

    await waitFor(() => expect(context.mock.calls.some((call) => call[1]?.offset === 1)).toBe(true));
    const messages = await screen.findAllByText(/First message|Second message/);
    expect(messages.map((message) => message.textContent)).toEqual(["Second message", "First message"]);
    expect(context.mock.calls.every((call) => call[1]?.order === "oldest")).toBe(true);
  });

  it("preserves global chronology when sessions overlap", async () => {
    context.mockResolvedValue({
      ...projectContext,
      total: 3,
      messages: [
        { ...firstMessage, content: "A first", created_at: "2026-01-01T09:00:00Z" },
        {
          ...firstMessage,
          id: 2,
          conversation_id: 13,
          conversation_title: "Second session",
          content: "B middle",
          created_at: "2026-01-01T09:01:00Z",
        },
        { ...firstMessage, id: 3, content: "A last", created_at: "2026-01-01T09:02:00Z" },
      ],
    });
    renderPage();

    await screen.findByText("A first");
    const transcript = screen.getByRole("heading", { name: "Transcript" }).parentElement!;
    const order = within(transcript)
      .getAllByText(/A first|B middle|A last/)
      .map((node) => node.textContent);
    expect(order).toEqual(["A first", "B middle", "A last"]);
  });

  it("uses later page totals while loading the complete document", async () => {
    context.mockImplementation(async (_project, options) => {
      const offset = options?.offset ?? 0;
      if (offset === 0) {
        return { ...projectContext, total: 2, complete: false, messages: [firstMessage] };
      }
      if (offset === 1) {
        return {
          ...projectContext,
          total: 3,
          offset,
          complete: false,
          messages: [{ ...firstMessage, id: 2, content: "Second message" }],
        };
      }
      return {
        ...projectContext,
        total: 3,
        offset,
        complete: true,
        messages: [{ ...firstMessage, id: 3, content: "Third message" }],
      };
    });
    renderPage();
    await screen.findByText("First message");

    await userEvent.click(screen.getByRole("button", { name: "Load complete project" }));

    expect(await screen.findByText("Third message")).toBeTruthy();
    expect(context.mock.calls.some((call) => call[1]?.offset === 2)).toBe(true);
  });

  it("does not loop when automatic newest-first loading makes no progress", async () => {
    context.mockImplementation(async (_project, options) => {
      const offset = options?.offset ?? 0;
      return offset === 0
        ? { ...projectContext, total: 2, complete: false, messages: [firstMessage] }
        : { ...projectContext, total: 2, offset, complete: false, messages: [] };
    });
    renderPage("/project/atlas?order=newest");

    await screen.findByText("First message");
    await waitFor(() => expect(context.mock.calls.filter((call) => call[1]?.offset === 1)).toHaveLength(1));
    await new Promise((resolve) => window.setTimeout(resolve, 100));

    expect(context.mock.calls.filter((call) => call[1]?.offset === 1)).toHaveLength(1);
  });

  it("discards an old document page after generated-content scope changes", async () => {
    let resolveOldPage!: (value: typeof projectContext) => void;
    const oldPage = new Promise<typeof projectContext>((resolve) => {
      resolveOldPage = resolve;
    });
    context.mockImplementation(async (_project, options) => {
      if (options?.includeGenerated) {
        return {
          ...projectContext,
          summary: "Generated scope",
          total: 1,
          messages: [{ ...firstMessage, id: 20, content: "Current generated scope" }],
        };
      }
      if (options?.offset === 1) return oldPage;
      return { ...projectContext, total: 2, complete: false, messages: [firstMessage] };
    });
    renderPage();
    await screen.findByText("First message");
    await userEvent.click(screen.getByRole("button", { name: "Load complete project" }));
    await waitFor(() => expect(context.mock.calls.some((call) => call[1]?.offset === 1)).toBe(true));

    await userEvent.click(screen.getByRole("tab", { name: "Sessions" }));
    await userEvent.click(await screen.findByRole("button", { name: "Show them" }));
    await userEvent.click(screen.getByRole("tab", { name: "Document" }));
    expect(await screen.findByText("Current generated scope")).toBeTruthy();

    resolveOldPage({
      ...projectContext,
      offset: 1,
      total: 2,
      messages: [{ ...firstMessage, id: 2, content: "Stale hidden message" }],
    });
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(screen.queryByText("Stale hidden message")).toBeNull();
  });

  it("implements keyboard-operable tabs with associated panels", async () => {
    const user = userEvent.setup();
    renderPage();
    const documentTab = screen.getByRole("tab", { name: "Document" });
    const sessionsTab = screen.getByRole("tab", { name: "Sessions" });

    expect(documentTab.getAttribute("aria-selected")).toBe("true");
    expect(documentTab.getAttribute("tabindex")).toBe("0");
    expect(sessionsTab.getAttribute("tabindex")).toBe("-1");
    expect(documentTab.getAttribute("aria-controls")).toBe("project-document-panel");
    expect(document.getElementById("project-sessions-panel")).toBeTruthy();
    expect(document.getElementById("project-sessions-panel")?.hasAttribute("hidden")).toBe(true);
    expect(screen.getByRole("tabpanel").id).toBe("project-document-panel");

    documentTab.focus();
    await user.keyboard("{ArrowRight}");

    await waitFor(() => expect(sessionsTab.getAttribute("aria-selected")).toBe("true"));
    expect(document.activeElement).toBe(sessionsTab);
    expect(sessionsTab.getAttribute("aria-controls")).toBe("project-sessions-panel");
    expect(document.getElementById("project-document-panel")).toBeTruthy();
    expect(document.getElementById("project-document-panel")?.hasAttribute("hidden")).toBe(true);
    expect(screen.getByRole("tabpanel").id).toBe("project-sessions-panel");
  });

  it("keeps search, sort, metadata, generated disclosure, and load-all in Sessions", async () => {
    const user = userEvent.setup();
    renderPage("/project/atlas?mode=sessions");

    const search = await screen.findByRole("textbox", { name: "Search inside atlas" });
    expect(screen.getByRole("button", { name: /newest first/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /oldest first/i })).toBeTruthy();
    const first = await screen.findByRole("link", { name: /First session/ });
    expect(within(first).getByText("claude_code")).toBeTruthy();
    expect(within(first).getByText("feature/atlas")).toBeTruthy();
    expect(within(first).getByText("45 min")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /load all sessions/i }));
    await screen.findByRole("link", { name: /Oldest session/ });
    expect(sessions.mock.calls.some((call) => call[1]?.offset === 2)).toBe(true);

    await user.type(search, "needle{Enter}");
    await waitFor(() => expect(sessions.mock.calls.some((call) => call[1]?.q === "needle")).toBe(true));

    await user.click(await screen.findByRole("button", { name: "Show them" }));
    await waitFor(() =>
      expect(sessions.mock.calls.some((call) => call[1]?.includeGenerated === true)).toBe(true),
    );
  });

  it("discards an old session page after the sort order changes", async () => {
    let resolveOldPage!: (value: ReturnType<typeof sessionResponse>) => void;
    const oldPage = new Promise<ReturnType<typeof sessionResponse>>((resolve) => {
      resolveOldPage = resolve;
    });
    sessions.mockImplementation(async (_project, options) => {
      if (options?.order === "oldest") {
        return sessionResponse(0, [{ ...sessionRows[1], id: 30, title: "Current oldest scope" }]);
      }
      if (options?.offset) return oldPage;
      return sessionResponse();
    });
    renderPage("/project/atlas?mode=sessions");
    await screen.findByRole("link", { name: /First session/ });
    await userEvent.click(screen.getByRole("button", { name: /load all sessions/i }));
    await waitFor(() => expect(sessions.mock.calls.some((call) => call[1]?.offset === 2)).toBe(true));

    await userEvent.click(screen.getByRole("button", { name: /oldest first/i }));
    expect(await screen.findByRole("link", { name: /Current oldest scope/ })).toBeTruthy();
    resolveOldPage(sessionResponse(2, [{ ...sessionRows[1], id: 10, title: "Stale newest scope" }]));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(screen.queryByRole("link", { name: /Stale newest scope/ })).toBeNull();
  });
});
