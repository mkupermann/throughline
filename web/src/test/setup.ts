/**
 * Vitest does not run with `globals: true` here (tests import
 * describe/it/expect explicitly, matching the rest of the codebase), so
 * @testing-library/react's automatic per-test cleanup — which detects a
 * global `afterEach` — never registers on its own. Without this, every
 * `render()` in a file leaves its DOM mounted and later tests in the same
 * file see a stack of previous renders, corrupting `screen` queries.
 */
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
