import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/lib/theme";
import { CommandPalette } from "./CommandPalette";

// jsdom provides neither of these; cmdk observes its list, and the theme
// provider asks for the system colour preference.
// cmdk scrolls the highlighted item into view; jsdom has no layout.
Element.prototype.scrollIntoView = () => {};

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
  dispatchEvent: () => false,
}));

function open() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <CommandPalette />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("CommandPalette", () => {
  it("offers the Markdown export by name", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");

    // A feature reachable only by scrolling one page is a feature people ask
    // for again because they could not find it.
    expect(await screen.findByText(/export as markdown/i)).toBeTruthy();
  });

  it("finds the export when searching for Obsidian", async () => {
    const user = userEvent.setup();
    open();
    await user.keyboard("{Meta>}k{/Meta}");
    await user.type(await screen.findByPlaceholderText(/jump to/i), "obsidian");

    expect(await screen.findByText(/export as markdown/i)).toBeTruthy();
  });
});
