import { QueryClient, QueryClientProvider, type UseQueryResult } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TasksSection } from "./TasksSection";
import type { PmTask } from "@/lib/api";
const mock = vi.hoisted(() => ({ executionReadiness: vi.fn(), projectRepos: vi.fn(), launch: vi.fn() }));
vi.mock("@/lib/api", () => ({ pmApi: mock }));
const ready = { available: true, checks: [{ id: "pipeline", available: true, path: "/pipeline.sh" }] };
function mount() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><TasksSection projectId={1}
      teams={[{ id: 2, name: "Review team", description: null, token_budget: null }]}
      tasks={{ data: { tasks: [] }, isPending: false } as unknown as UseQueryResult<{ tasks: PmTask[] }>} />
    </MemoryRouter>
  </QueryClientProvider>);
}
async function fill() {
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Team"), "2");
  await user.type(screen.getByLabelText("Title"), "Review");
  await user.type(screen.getByLabelText("Repo path"), "/repo");
  return user;
}
beforeEach(() => { vi.clearAllMocks(); mock.projectRepos.mockResolvedValue({ repo_projects: [] }); });
afterEach(cleanup);
it("blocks launch while the dependency check is loading", async () => {
  mock.executionReadiness.mockReturnValue(new Promise(() => {}));
  mount(); await fill();
  expect((screen.getByRole("button", { name: "Start task" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Checking dependencies…")).toBeTruthy();
  expect(mock.launch).not.toHaveBeenCalled();
});
it("blocks unavailable runtime and enables launch after a successful retry", async () => {
  mock.executionReadiness.mockResolvedValueOnce({ available: false, checks: [{ id: "pipeline", available: false, path: "/missing.sh" }] }).mockResolvedValue(ready);
  mount(); const user = await fill();
  expect((screen.getByRole("button", { name: "Start task" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/Provide your compatible/)).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Start task" }) as HTMLButtonElement).disabled).toBe(false));
  expect(mock.launch).not.toHaveBeenCalled();
});
it("keeps launch blocked when the check fails", async () => {
  mock.executionReadiness.mockRejectedValue(new Error("Offline"));
  mount(); await fill();
  expect(await screen.findByText("Could not check dependencies. Launch is blocked.")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Start task" }) as HTMLButtonElement).disabled).toBe(true);
});
