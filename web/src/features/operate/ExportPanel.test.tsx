import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/Toaster";
import { ExportPanel } from "./ExportPanel";

// A successful run mounts the shared job console, which opens an EventSource.
class FakeEventSource {
  addEventListener() {}
  removeEventListener() {}
  close() {}
}
vi.stubGlobal("EventSource", FakeEventSource);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    exportApi: {
      options: vi.fn(),
      start: vi.fn(),
    },
  };
});

import { exportApi } from "@/lib/api";

const options = {
  root: "/Users/dev",
  suggested: "/Users/dev/Throughline-Export",
  job: "export-markdown",
  hostPath: "/Users/dev",
  defaults: { includeGenerated: false, redact: false, toolOutput: 0, memory: true },
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ExportPanel />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("ExportPanel", () => {
  beforeEach(() => {
    vi.mocked(exportApi.options).mockResolvedValue(options);
    vi.mocked(exportApi.start).mockReset();
  });

  it("offers the suggested destination without the user typing one", async () => {
    renderPanel();
    const field = (await screen.findByRole("textbox", { name: "Destination" })) as HTMLInputElement;
    await waitFor(() => expect(field.value).toBe("/Users/dev/Throughline-Export"));
    expect(field.getAttribute("name")).toBe("destination");
    expect(screen.getByRole("button", { name: "Choose folder…" })).toBeTruthy();
  });

  it("says where the export is allowed to write", async () => {
    renderPanel();
    expect(await screen.findByText(/\/Users\/dev/)).toBeTruthy();
  });

  it("sends the destination and the chosen options", async () => {
    vi.mocked(exportApi.start).mockResolvedValue({
      out: "/Users/dev/Vault",
      job: { id: "j1", name: "export-markdown", running: true },
    });
    const user = userEvent.setup();
    renderPanel();

    const field = (await screen.findByLabelText(/destination/i)) as HTMLInputElement;
    await waitFor(() => expect(field.value).toBe(options.suggested));
    await user.clear(field);
    await user.type(field, "/Users/dev/Vault");
    await user.click(screen.getByLabelText(/redact/i));
    await user.click(screen.getByRole("button", { name: /export/i }));

    await waitFor(() =>
      expect(exportApi.start).toHaveBeenCalledWith(
        expect.objectContaining({ out: "/Users/dev/Vault", redact: true }),
      ),
    );
  });

  it("will not start without a destination", async () => {
    const user = userEvent.setup();
    renderPanel();

    const field = (await screen.findByLabelText(/destination/i)) as HTMLInputElement;
    await waitFor(() => expect(field.value).toBe(options.suggested));
    await user.clear(field);
    await user.click(screen.getByRole("button", { name: /export/i }));

    expect(exportApi.start).not.toHaveBeenCalled();
    expect(await screen.findByText(/enter a destination/i)).toBeTruthy();
  });

  it("shows the server's reason when a destination is refused", async () => {
    vi.mocked(exportApi.start).mockRejectedValue(new Error("The destination is outside /Users/dev"));
    const user = userEvent.setup();
    renderPanel();

    await screen.findByLabelText(/destination/i);
    await user.click(screen.getByRole("button", { name: /export/i }));

    expect(await screen.findByText(/is outside/)).toBeTruthy();
  });
});

describe("ExportPanel discoverability", () => {
  beforeEach(() => {
    vi.mocked(exportApi.options).mockResolvedValue(options);
  });

  it("names Obsidian, because that is what people call the destination", async () => {
    renderPanel();
    expect(await screen.findByText(/obsidian/i)).toBeTruthy();
  });

  it("carries an anchor so it can be linked to directly", async () => {
    const { container } = renderPanel();
    await screen.findByLabelText(/destination/i);
    expect(container.querySelector("#export")).toBeTruthy();
  });
});

describe("ExportPanel in a container", () => {
  it("says where the files appear on the host, not only inside the container", async () => {
    vi.mocked(exportApi.options).mockResolvedValue({
      ...options,
      root: "/home/throughline/exports",
      suggested: "/home/throughline/exports/Throughline-Export",
      hostPath: "C:\\Users\\alex\\throughline\\exports",
    });
    renderPanel();

    // Typing a Windows path is refused; without this the refusal reads as the
    // export being broken, and a successful one lands somewhere unfindable.
    expect(await screen.findByText(/C:\\Users\\alex\\throughline\\exports/)).toBeTruthy();
  });

  it("does not repeat itself when the two are the same", async () => {
    vi.mocked(exportApi.options).mockResolvedValue({ ...options, hostPath: options.root });
    renderPanel();
    await screen.findByLabelText(/destination/i);
    expect(screen.queryByText(/appears on this machine/i)).toBeNull();
  });
});
