import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { AccessGate } from "./AccessGate";
import { sessionToken } from "@/lib/session";
const mocked = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("@/lib/api", () => ({ request: mocked.request }));
const anonymous = { mode: "team", user: null, csrf_token: null };
const authenticated = {
  mode: "team",
  user: { id: 1, username: "reader", display_name: "Reader", role: "viewer" },
  csrf_token: "fictional-csrf",
};
function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <AccessGate>
        <p>Private project history</p>
      </AccessGate>
    </QueryClientProvider>,
  );
  return cache;
}
beforeEach(() => {
  mocked.request.mockReset();
  localStorage.clear();
});
describe("workspace sign-in boundary", () => {
  it("keeps project content hidden until sign-in succeeds", async () => {
    mocked.request
      .mockResolvedValueOnce(anonymous)
      .mockRejectedValueOnce(new Error("Invalid username or password."))
      .mockResolvedValueOnce(authenticated);
    setup();
    await screen.findByRole("heading", { name: "Sign in to your workspace" });
    expect(screen.queryByText("Private project history")).toBeNull();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Username"), "reader");
    await user.type(
      screen.getByLabelText("Password"),
      "fictional long password",
    );
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("alert");
    expect(screen.queryByText("Private project history")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("Private project history");
    expect(sessionToken()).toBe("fictional-csrf");
  });
  it("clears cached project data when the server revokes a session", async () => {
    mocked.request.mockResolvedValue(authenticated);
    const cache = setup();
    await screen.findByText("Private project history");
    cache.setQueryData(["private"], "secret");
    fireEvent(window, new Event("throughline-session-expired"));
    await screen.findByRole("heading", { name: "Sign in to your workspace" });
    expect(cache.getQueryData(["private"])).toBeUndefined();
    expect(sessionToken()).toBe("");
  });
  it("does not silently show the app when the session endpoint fails", async () => {
    mocked.request.mockRejectedValue(new Error("Workspace unavailable"));
    setup();
    await screen.findByRole("alert");
    await waitFor(() =>
      expect(screen.queryByText("Private project history")).toBeNull(),
    );
  });
});
