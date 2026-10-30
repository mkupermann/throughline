import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveProject, statusText, stateFromError } from "../src/statusLogic";
import { ThroughlineError } from "../src/errors";

test("project_defaults_to_the_folder_name", () => {
  assert.equal(resolveProject("my-repo", undefined), "my-repo");
  assert.equal(resolveProject("my-repo", "  "), "my-repo");
  assert.equal(resolveProject(undefined, ""), undefined);
});

test("project_setting_overrides_folder", () => {
  assert.equal(resolveProject("my-repo", "other"), "other");
  assert.equal(resolveProject(undefined, " other "), "other");
});

test("status_text_for_each_state", () => {
  assert.equal(statusText({ kind: "connected", project: "p" }), "Throughline: p · connected");
  assert.equal(statusText({ kind: "connected" }), "Throughline: connected");
  assert.equal(statusText({ kind: "no_key" }), "Throughline: not connected (no key, run Throughline: Set Agent Key)");
  assert.equal(statusText({ kind: "unauthorized" }), "Throughline: not connected (key rejected)");
  assert.equal(statusText({ kind: "access_off" }), "Throughline: not connected (agent access is off on the server)");
  assert.equal(
    statusText({ kind: "unreachable", url: "http://x:1" }),
    "Throughline: not connected (server unreachable at http://x:1)",
  );
  assert.equal(statusText({ kind: "other", reason: "boom" }), "Throughline: not connected (boom)");
});

test("state_from_error_maps_kinds", () => {
  const u = "http://x:1";
  assert.deepEqual(stateFromError(new ThroughlineError("no_key", "m"), u), { kind: "no_key" });
  assert.deepEqual(stateFromError(new ThroughlineError("unauthorized", "m"), u), { kind: "unauthorized" });
  assert.deepEqual(stateFromError(new ThroughlineError("access_off", "m"), u), { kind: "access_off" });
  assert.deepEqual(stateFromError(new ThroughlineError("unreachable", "m"), u), { kind: "unreachable", url: u });
  assert.deepEqual(stateFromError(new Error("boom"), u), { kind: "other", reason: "boom" });
});
