import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { resolveProject, statusText, stateFromError } from "../src/statusLogic";
import { NO_MCP_ENDPOINT, ThroughlineError } from "../src/errors";
import { validateKey, validateUrl } from "../src/validate";

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
  assert.deepEqual(stateFromError(new ThroughlineError("sign_in_required", "m"), u), { kind: "sign_in_required" });
  assert.equal(statusText({ kind: "sign_in_required" }), "Throughline: sign-in required (run Throughline: Set Agent Key)");
  assert.deepEqual(stateFromError(new ThroughlineError("access_off", "m"), u), { kind: "access_off" });
  assert.deepEqual(stateFromError(new ThroughlineError("unreachable", "m"), u), { kind: "unreachable", url: u });
  assert.deepEqual(stateFromError(new Error("boom"), u), { kind: "other", reason: "boom" });
});

test("status_text_for_a_server_without_mcp", () => {
  const e = new ThroughlineError("protocol", NO_MCP_ENDPOINT);
  assert.deepEqual(stateFromError(e, "http://x:1"), { kind: "no_mcp" });
  assert.equal(statusText({ kind: "no_mcp" }), "Throughline: not connected (server has no /mcp endpoint)");
  // Other protocol errors keep their own text.
  assert.equal(stateFromError(new ThroughlineError("protocol", "other"), "http://x:1").kind, "other");
});

test("url_must_be_http_or_https", () => {
  assert.equal(validateUrl("http://127.0.0.1:8788"), "http://127.0.0.1:8788");
  assert.equal(validateUrl(" https://tl.example/base "), "https://tl.example/base");
  for (const bad of ["", "  ", "file:///etc/passwd", "javascript:alert(1)", "ftp://x", "127.0.0.1:8788", "localhost:8788", "not a url"]) {
    assert.equal(validateUrl(bad), undefined, bad);
  }
});

test("url_setting_is_machine_scoped", () => {
  const pkg = JSON.parse(readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
  const props = pkg.contributes.configuration.properties;
  assert.equal(props["throughline.url"].scope, "machine");
  assert.equal(props["throughline.project"].scope, "resource");
});

test("key_must_be_printable_ascii", () => {
  for (const ok of ["k".repeat(40), "abc-123_XYZ.~!", "a"]) assert.equal(validateKey(ok), true, ok);
  for (const bad of ["", "a b", "a\tb", "a\nb", "schl\u00fcssel", "\u007f", "key\u2028"]) {
    assert.equal(validateKey(bad), false, JSON.stringify(bad));
  }
});
