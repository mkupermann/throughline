import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TemplatesPage } from "./TemplatesPage";
const mock = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("@/lib/api", () => mock);
const template = {
  id: 4,
  kind: "project",
  name: "UX review",
  description: "Improve a workflow",
  version: 2,
  content: { objective: "Make recovery clear" },
};
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <MemoryRouter>
        <TemplatesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  mock.request.mockReset();
  mock.request.mockImplementation(async (path: string) =>
    path === "/pm/templates"
      ? { templates: [template] }
      : { ...template, version: 3 },
  );
});
afterEach(cleanup);
it("creates a real instance from the version previewed and exposes its destination", async () => {
  mock.request.mockImplementation(async (path: string) =>
    path === "/pm/templates"
      ? { templates: [template] }
      : { kind: "project", id: 31 },
  );
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /UX review/ }));
  await user.clear(screen.getByLabelText("Instance name"));
  await user.type(screen.getByLabelText("Instance name"), "Atlas redesign");
  await user.click(
    screen.getByRole("button", { name: "Create project from template" }),
  );
  await waitFor(() =>
    expect(mock.request).toHaveBeenCalledWith("/pm/templates/4/instantiate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Atlas redesign", version: 2 }),
    }),
  );
  expect(
    (await screen.findByRole("link", { name: /Open/ })).getAttribute("href"),
  ).toBe("/pm/projects/31");
});
it("edits with a version guard and omits immutable kind from PATCH", async () => {
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /UX review/ }));
  await user.click(screen.getByRole("button", { name: "Edit template" }));
  await user.clear(screen.getByLabelText("Template name"));
  await user.type(screen.getByLabelText("Template name"), "Updated review");
  await user.click(screen.getByRole("button", { name: "Save template" }));
  await waitFor(() =>
    expect(mock.request).toHaveBeenCalledWith("/pm/templates/4", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Updated review",
        description: template.description,
        content: template.content,
        expected_version: 2,
      }),
    }),
  );
});
it("keeps failed creation recoverable and restores focus when closing preview", async () => {
  mock.request.mockImplementation(async (path: string) => {
    if (path === "/pm/templates") return { templates: [template] };
    throw new Error("Connection unavailable");
  });
  const user = userEvent.setup();
  mount();
  const card = await screen.findByRole("button", { name: /UX review/ });
  await user.click(card);
  await user.click(
    screen.getByRole("button", { name: "Create project from template" }),
  );
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Connection unavailable",
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Create project from template",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
  await user.click(screen.getByRole("button", { name: "Close preview" }));
  expect(document.activeElement).toBe(card);
});
it("prevents context changes while saving and shows pinned reference versions", async () => {
  let finish!: (value: unknown) => void;
  mock.request.mockImplementation((path: string) =>
    path === "/pm/templates"
      ? Promise.resolve({
          templates: [
            { ...template, content: { team_template: { id: 9, version: 1 } } },
            {
              id: 9,
              kind: "team",
              name: "Review team",
              version: 2,
              description: "Team",
              content: {},
            },
          ],
        })
      : new Promise((resolve) => {
          finish = resolve;
        }),
  );
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: /UX review/ }));
  await user.click(screen.getByRole("button", { name: "Edit template" }));
  expect(
    (screen.getByLabelText("Team template") as HTMLSelectElement)
      .selectedOptions[0].textContent,
  ).toContain("v1 · pinned");
  await user.click(screen.getByRole("button", { name: "Save template" }));
  expect(
    (
      screen.getByRole("button", {
        name: "Create template",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    (
      screen.getByRole("button", {
        name: /Team templates/,
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  finish(template);
  await screen.findByRole("button", { name: "Edit template" });
});
it("filters domain templates and assigns the selected category to a new template", async () => {
  mock.request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (!init) return { templates: [template, { ...template, id: 5, name: "Cash forecast", content: { category: "finance" } }, { ...template, id: 6, name: "Experiment", content: { category: "science" } }] };
    return { ...JSON.parse(init.body as string), id: 7, version: 1 };
  });
  const user = userEvent.setup(); mount();
  await screen.findByRole("button", { name: /Cash forecast/ });
  await user.selectOptions(screen.getByRole("combobox", {name: "Category", exact: true}), "finance");
  expect(screen.queryByRole("button", {name:/Experiment/})).toBeNull();
  expect(screen.queryByRole("button", {name:/UX review/})).toBeNull();
  await user.click(screen.getByRole("button", { name: "Create template", exact: true }));
  expect((screen.getByLabelText("Template category") as HTMLSelectElement).value).toBe("finance");
  await user.type(screen.getByLabelText("Template name"), "Forecast review");
  await user.click(screen.getByRole("button", {name:"Save template", exact:true}));
  await waitFor(() => expect(mock.request).toHaveBeenCalledWith("/pm/templates", expect.objectContaining({method:"POST",body:JSON.stringify({kind:"project",name:"Forecast review",description:"",content:{category:"finance"}})})));
});
