import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { it, expect, vi } from "vitest";
import { AiSettings } from "./AiSettings";
const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  providers: vi.fn(),
  models: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  request: mocks.request,
  pmApi: {
    listAiProviders: mocks.providers,
    refreshAiProviderModels: mocks.models,
  },
}));
it("saves an explicit local embedding model and never offers a chat CLI for embeddings", async () => {
  mocks.request.mockImplementation(async () => ({
    purposes: ["embeddings"],
    bindings: [],
    bridge: { clis: { codex: { installed: true } } },
  }));
  mocks.providers.mockResolvedValue({
    providers: [
      {
        id: 1,
        name: "Local AI",
        provider_type: "ollama",
        base_url: "http://localhost:11434",
        custom_models: [],
        enabled: true,
      },
    ],
  });
  mocks.models.mockResolvedValue({
    models: ["nomic-embed-text"],
    unavailable: false,
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <AiSettings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const provider = await screen.findByRole("combobox", {
    name: "Provider or installed CLI",
  });
  await screen.findByRole("option", { name: "Local AI · ollama" });
  expect(screen.queryByRole("option", { name: /codex/ })).toBeNull();
  await userEvent.selectOptions(provider, "api:1");
  await userEvent.type(
    screen.getByRole("combobox", { name: "Model" }),
    "nomic-embed-text",
  );
  await userEvent.click(screen.getByRole("button", { name: "Save Search embeddings" }));
  await waitFor(() =>
    expect(
      mocks.request.mock.calls.some(
        (call) =>
          call[0] === "/ai/settings/embeddings" && call[1]?.method === "PUT",
      ),
    ).toBe(true),
  );
  const saved = mocks.request.mock.calls.find(
    (call) => call[1]?.method === "PUT",
  )!;
  expect(JSON.parse(saved[1].body)).toEqual({
    provider_id: 1,
    cli: null,
    model: "nomic-embed-text",
    embedding_dim: 768,
  });
});

it("serializes connection tests and shows an actionable bridge error", async () => {
  let finish: (value: unknown) => void = () => {};
  const pending = new Promise((resolve) => {
    finish = resolve;
  });
  mocks.request.mockImplementation((path: string) =>
    path.endsWith("/test")
      ? pending
      : Promise.resolve({
          purposes: ["answer", "titles"],
          bindings: ["answer", "titles"].map((purpose) => ({
            purpose,
            cli: "vibe",
            model: "",
            provider_id: null,
            embedding_dim: null,
          })),
          bridge: { clis: { vibe: { installed: true } } },
        }),
  );
  mocks.providers.mockResolvedValue({ providers: [] });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <AiSettings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const buttons = await screen.findAllByRole("button", {
    name: /Test connection for/,
  });
  await userEvent.click(buttons[0]);
  expect((buttons[0] as HTMLButtonElement).disabled).toBe(true);
  expect((buttons[1] as HTMLButtonElement).disabled).toBe(true);
  const error =
    "The host CLI bridge is busy with another request. Wait for it to finish, then test again.";
  finish({ ok: false, error });
  expect(await screen.findByText(`Answers: ${error}`)).toBeTruthy();
  await waitFor(() =>
    expect((buttons[1] as HTMLButtonElement).disabled).toBe(false),
  );
});


it("gives every purpose a named form and uniquely named actions", async () => {
  const purposes = ["answer", "titles", "project_names", "extraction", "reflection", "embeddings"];
  const names = ["Answers", "Conversation titles", "Project names", "Knowledge and entities", "Reflection", "Search embeddings"];
  mocks.request.mockResolvedValue({ purposes, bindings: [], bridge: { clis: {} } });
  mocks.providers.mockResolvedValue({ providers: [] });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AiSettings /></MemoryRouter></QueryClientProvider>);
  for (const name of names) {
    const form = await screen.findByRole("form", { name });
    expect(within(form).getByRole("button", { name: `Save ${name}` })).toBeTruthy();
    const test = within(form).getByRole("button", { name: `Test connection for ${name}` });
    expect((test as HTMLButtonElement).disabled).toBe(true);
    expect(test.getAttribute("aria-describedby")).toBeTruthy();
    expect(within(form).getByRole("status")).toBeTruthy();
  }
});
