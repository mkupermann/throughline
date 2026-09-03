import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/lib/theme";
import { Shell } from "./Shell";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, ScrollRestoration: () => null };
});
vi.mock("./ProviderBar", () => ({ ProviderBar: () => null }));
vi.mock("./CommandPalette", () => ({ CommandPalette: () => null }));

vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener() {},
  removeEventListener() {},
}));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}{location.search}</output>;
}

function renderShell(path = "/curate?provider=hermes") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ThemeProvider>
        <Shell />
        <LocationProbe />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("Shell", () => {
  it("groups task navigation, names Review, and retains provider scope", () => {
    renderShell();

    for (const group of ["Work", "Trust", "System", "Project Management"]) {
      expect(screen.getByRole("navigation", { name: group })).toBeTruthy();
    }

    const review = screen.getByRole("link", { name: /^Review/ });
    expect(review.getAttribute("href")).toBe("/curate?provider=hermes");
    expect(review.getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("link", { name: "Curate" })).toBeNull();
    expect(screen.getByRole("link", { name: /^Find/ }).getAttribute("href")).toBe("/find?provider=hermes");
  });

  it("keeps every navigation link named when its visible text is collapsed", () => {
    renderShell();

    for (const name of [
      "Overview",
      "Find",
      "Timeline",
      "Review",
      "Operate",
      "Console",
      "Project Management",
    ]) {
      const link = screen.getByRole("link", { name });
      expect(link.getAttribute("aria-label")).toBe(name);
    }
  });

  it("opens keyboard help with the command palette and every go chord", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: /keyboard shortcuts/i }));

    const help = screen.getByRole("dialog", { name: /keyboard shortcuts/i });
    expect(within(help).getByText(/Cmd\/Ctrl\+K/)).toBeTruthy();
    for (const chord of ["g o", "g f", "g t", "g c", "g p", "g s", "g m"]) {
      expect(within(help).getByText(chord)).toBeTruthy();
    }
  });

  it("shows the platform shortcut once in the command-palette nudge", () => {
    renderShell();

    const nudge = screen.getByText(/Press/).closest(".palette-nudge");
    expect(nudge?.textContent).toContain("Ctrl+K");
    expect(nudge?.querySelector("svg")).toBeNull();
  });

  it("moves focus into keyboard help and contains tab navigation", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Keyboard shortcuts" }));

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close keyboard shortcuts" }));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close keyboard shortcuts" }));
  });

  it("closes keyboard help with Escape and restores focus to its trigger", async () => {
    const user = userEvent.setup();
    renderShell();
    const trigger = screen.getByRole("button", { name: "Keyboard shortcuts" });

    await user.click(trigger);
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: /keyboard shortcuts/i })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes keyboard help when its backdrop is pressed", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Keyboard shortcuts" }));
    await user.click(screen.getByTestId("keyboard-help-backdrop"));

    expect(screen.queryByRole("dialog", { name: /keyboard shortcuts/i })).toBeNull();
  });

  it("does not run global go chords while keyboard help is modal", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Keyboard shortcuts" }));
    await user.keyboard("gf");

    expect(screen.getByTestId("location").textContent).toBe("/curate?provider=hermes");
    expect(screen.getByRole("dialog", { name: /keyboard shortcuts/i })).toBeTruthy();
  });

  it("persists a compact density preference without changing navigation", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Compact density" }));

    expect(document.documentElement.dataset.density).toBe("compact");
    expect(localStorage.getItem("throughline-density")).toBe("compact");
    expect(screen.getByTestId("location").textContent).toBe("/curate?provider=hermes");
  });
});
