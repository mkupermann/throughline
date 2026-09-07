import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectStory, handoffText } from "./ProjectStory";
import type { StoryHistory } from "@/lib/api";
const mocks = vi.hoisted(() => ({
  history: vi.fn(),
  session: vi.fn(),
  checkpoint: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ storyApi: mocks }));
const data: StoryHistory = {
  project: "Atlas",
  path: null,
  paths: [
    { path: "/team/a/Atlas", sessions: 1 },
    { path: "/team/b/Atlas", sessions: 1 },
  ],
  coverage: {
    sessions: 2,
    messages: 2,
    refreshed_at: "2026-09-01T10:00:00Z",
    unattributed: 0,
  },
  sessions: [
    {
      id: 1,
      session_id: "s1",
      title: "Compare search methods",
      opening: "Investigate method A",
      source_tool: "codex",
      model: "model-a",
      started_at: "2026-09-01T10:00:00Z",
      ended_at: null,
      git_branch: null,
      generated_by: null,
      message_count: 2,
      project_path: "/team/a/Atlas",
      knowledge_count: 1,
    },
  ],
  total: 1,
  offset: 0,
  has_more: false,
  checkpoints: [],
  latest_checkpoints: [],
  hidden_generated: 0,
  query: "",
  order: "newest",
};
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={["/project/Atlas"]}>
        <Routes>
          <Route path="/project/:name" element={<ProjectStory />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  mocks.history.mockResolvedValue(data);
  mocks.session.mockResolvedValue({
    messages: [
      {
        id: 10,
        uuid: "u10",
        role: "assistant",
        content: "Method A failed replication",
        created_at: "2026-09-01T10:00:00Z",
        model: "model-a",
        tool_name: null,
        matches: false,
      },
    ],
    knowledge: [
      {
        id: 8,
        content: "A is promising",
        category: "insight",
        status: "active",
        confidence: 0.99,
        superseded_by: null,
        created_at: "2026-09-02T10:00:00Z",
      },
    ],
    knowledge_total: 1,
    matches: [],
    total: 1,
    offset: 0,
    has_more: false,
  });
  mocks.checkpoint.mockResolvedValue({ id: 4 });
});
describe("Project story", () => {
  it("starts with honest empty state and loads evidence only when expanded", async () => {
    mount();
    await screen.findByRole("heading", { name: "Where we stand" });
    expect(screen.getAllByText(/Not recorded yet/)).toHaveLength(4);
    expect(mocks.session).not.toHaveBeenCalled();
    expect(screen.getByText(/This name groups 2 working folders/)).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: /Compare search methods/ }),
    );
    await screen.findByText("A is promising");
    expect(screen.getAllByText(/Unreviewed/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Confidence 99/)).toBeNull();
    expect(
      screen.getByRole("link", { name: "Source ↗" }).getAttribute("href"),
    ).toBe("/c/1#m10");
  });
  it("records state only after the user writes a note against an explicit source", async () => {
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: /Compare search methods/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Use as source for project state",
      }),
    );
    expect(mocks.checkpoint).not.toHaveBeenCalled();
    await userEvent.type(
      screen.getByRole("textbox", { name: "Your project note" }),
      "Replication failed; try B.",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save with source" }),
    );
    await waitFor(() =>
      expect(mocks.checkpoint).toHaveBeenCalledWith("Atlas", {
        path: null,
        kind: "status",
        content: "Replication failed; try B.",
        conversation_id: 1,
        message_id: 10,
      }),
    );
  });
  it("previews only selected sources and never calls an AI service", async () => {
    mount();
    const prepare = await screen.findByRole("button", {
      name: "Prepare handoff",
    });
    expect((prepare as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(
      await screen.findByRole("checkbox", { name: /Include Compare/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Prepare handoff (1)" }),
    );
    const preview = screen.getByRole("textbox", {
      name: "Exact export contents",
    }) as HTMLTextAreaElement;
    expect(preview.value).toContain("1 selected sessions of 2");
    expect(preview.value).toContain("not a complete transcript");
    expect(mocks.session).not.toHaveBeenCalled();
  });
  it("excludes project notes belonging to unselected sessions from export", () => {
    const checkpoint = {
      id: 2,
      kind: "status" as const,
      content: "Excluded private note",
      created_at: "2026-09-01T10:00:00Z",
      source_conversation_id: 99,
      source_message_id: null,
      source_session_id: "other",
      source_excerpt: "",
      source_available: true,
      message_available: false,
      source_changed: false,
      recorded_by: "local user",
    };
    expect(
      handoffText(
        {
          ...data,
          checkpoints: [checkpoint],
          latest_checkpoints: [checkpoint],
        },
        data.sessions,
      ),
    ).not.toContain("Excluded private note");
  });
});
